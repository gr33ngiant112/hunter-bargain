"""Multi-engine price search orchestrator.

Queries all registered search engines in sequence, aggregates results,
persists price records, and triggers notifications when targets are met.
"""

from __future__ import annotations

import itertools
import logging
import re
from dataclasses import dataclass

from sqlalchemy.orm import Session

from hunter_bargain.models import Item, PriceRecord
from hunter_bargain.schemas import PriceCheckResult, PriceRecordResponse
from hunter_bargain.services.engines.base import EngineError, SearchEngine, SearchResult
from hunter_bargain.services.engines.bing import BingShoppingEngine
from hunter_bargain.services.engines.google import GoogleShoppingEngine
from hunter_bargain.services.notifier import send_price_alert

logger = logging.getLogger(__name__)

# Registry of active search engines. Add new engines here.
_ENGINES: list[SearchEngine] = [
    GoogleShoppingEngine(),
    BingShoppingEngine(),
]


def _keyword_terms(keywords: str | None) -> list[str]:
    """An item's keyword terms: its keywords split on commas, e.g. "unlocked, 256GB"."""
    return [term.strip() for term in (keywords or "").split(",") if term.strip()]


def _build_query(item: Item) -> str:
    """Construct a search query from an item's name and optional keywords.

    Keywords may be comma-separated (e.g. "unlocked,256GB") — these are
    split into individual terms so the search engine treats them as separate
    words rather than a single blob.
    """
    return " ".join([item.name, *_keyword_terms(item.keywords)])


# Words that name an accessory. They count only where a title uses them for an accessory (see
# _is_accessory): a "Dual-Band" router, a vacuum "with HEPA Filter" or a phone with an "S Pen"
# is still the product.
_ACCESSORY_TERMS = frozenset(
    {
        "case",
        "cover",
        "protector",
        "screen",
        "film",
        "tempered",
        "glass",
        "sleeve",
        "pouch",
        "skin",
        "decal",
        "sticker",
        "mount",
        "holder",
        "stand",
        "charger",
        "cable",
        "adapter",
        "strap",
        "band",
        "armband",
        "holster",
        "wallet",
        "folio",
        "bumper",
        "shell",
        "grip",
        "kickstand",
        "stylus",
        "pen",
        "earbuds",
        "earphones",
        "replacement",
        "cleaning",
        "cloth",
        "wipe",
        "ring",
        "lanyard",
        "dock",
        "cradle",
        "keyboard",
        "trackpad",
        "mousepad",
        "webcam",
        "tripod",
        "selfie",
        "gimbal",
        "lens",
        "filter",
        "temper",
    }
)

# Extensions (from Google Shopping) that indicate an accessory listing
_ACCESSORY_EXTENSIONS = frozenset(
    {
        "protective case",
        "phone case",
        "screen protector",
        "accessory",
        "accessories",
        "case",
        "cover",
        "cable",
        "charger",
        "adapter",
        "mount",
        "holder",
        "stand",
    }
)

_NOISE_WORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "for",
        "and",
        "or",
        "of",
        "in",
        "with",
        "to",
        "by",
    }
)

_MIN_RELEVANCE_RATIO = 0.5
_PRICE_FLOOR_RATIO = 0.10

# Words that mark a second-hand listing, as runs of title tokens: "(Renewed)", "Renewed Premium",
# "Pre-Owned", "Open Box" and "For Parts or Not Working" all contain one. There is no per-item
# opt-in for second-hand listings yet, so these are always rejected.
_CONDITION_WORDS = (
    ("renewed",),
    ("refurbished",),
    ("restored",),
    ("used",),
    ("pre", "owned"),
    ("preowned",),
    ("open", "box"),
    ("openbox",),
    ("for", "parts"),
)

# One spelling per unit. A unit joins the number before it, so "256GB", "256 GB" and "(256 GB)"
# are all the token "256gb", and '27"', "27 in", "27-inch" and "27inch" are all "27in".
_UNITS = {
    "tb": "tb",
    "gb": "gb",
    "mb": "mb",
    "in": "in",
    "inch": "in",
    "inches": "in",
    "mm": "mm",
    "cm": "cm",
    "ft": "ft",
    "hz": "hz",
    "ghz": "ghz",
    "mhz": "mhz",
    "mah": "mah",
    "w": "w",
    "v": "v",
    "mp": "mp",
    "k": "k",
    "p": "p",
    "g": "g",
    "kg": "kg",
    "oz": "oz",
    "lb": "lb",
    "lbs": "lb",
    "pack": "pack",
    "pk": "pack",
    "pcs": "pcs",
    "ct": "ct",
    "count": "ct",
    "hr": "hr",
    "hrs": "hr",
    "hour": "hr",
    "hours": "hr",
    "mbps": "mbps",
    "gbps": "gbps",
}
_UNIT_SPELLINGS = frozenset(_UNITS.values())
_INCH_MARKS = frozenset('"”″')

# A word, a number or a model code; a decimal point between digits stays in ("6.1", "2.5gbps").
_TOKEN = re.compile(r"[a-z0-9]+(?:\.[0-9][a-z0-9]*)*")
_NUMBER = re.compile(r"\d+(?:\.\d+)*")
_SIZE = re.compile(r"(\d+(?:\.\d+)*)([a-z]+)")
_DIGIT_RUN = re.compile(r"\d+")

# Bracketed text is left out of a title's phrases and of the model-number places (_breaks_slot):
# "OtterBox iPhone 15 Pro (Only) Commuter Series Case", "Apple iPhone (Renewed Premium) 15 Pro".
_BRACKETED = re.compile(r"\([^()]*\)|\[[^\[\]]*\]")
# Where a title's phrases end: punctuation, a spaced dash or slash, and the words that start a
# list of extras ("with HEPA Filter") or say what a listing is for ("Case for iPhone 15 Pro").
_PHRASE_BREAK = re.compile(
    r"([,;:|+]|\s[-–—/]\s|\bw/|\b(?:with|for|compatible|fits|featuring|includes|including)\b)"
)
# Breaks after which an accessory names the product it fits: "Screen Protector for Pixel 10 Pro",
# "Replacement Ear Pads Compatible with Sony WH-1000XM5".
_FOR_WORDS = frozenset({"for", "compatible", "fits"})


def _tokens(text: str) -> list[str]:
    """Split a name, keyword term or title into lower-case tokens, in order.

    - Letters and digits that run together stay one token: "wh1000xm5", "s24", "m3".
    - Letters before a hyphen join the number after it, so "WH-1000XM5" is "wh1000xm5", the same
      token as "WH1000XM5". "TP-Link" and "Wi-Fi" stay apart: no digit follows the hyphen.
    - A unit joins the number before it across a space or hyphen, spelt one way (_UNITS):
      "256 GB" is "256gb", "6.1-inch" is "6.1in", '27"' is "27in", "2-Pack" is "2pack".
    - Every other character separates tokens, so "PlayStation®5" is "playstation", "5".
    """
    lowered = text.lower()
    tokens: list[str] = []
    end = 0
    for match in _TOKEN.finditer(lowered):
        token, gap = _spelling(match.group()), lowered[end : match.start()]
        end = match.end()
        if tokens and gap == "-" and tokens[-1].isalpha() and token[0].isdigit():
            tokens[-1] += token
        elif (
            tokens and (gap == "-" or gap.isspace()) and token in _UNITS and _is_number(tokens[-1])
        ):
            tokens[-1] += _UNITS[token]
        else:
            tokens.append(token)
        if lowered[end : end + 1] in _INCH_MARKS and _is_number(tokens[-1]):
            tokens[-1] += "in"
    return tokens


def _spelling(token: str) -> str:
    """A number with a unit, spelt with the unit's one spelling: "27inch" -> "27in"."""
    size = _SIZE.fullmatch(token)
    if size and size.group(2) in _UNITS:
        return size.group(1) + _UNITS[size.group(2)]
    return token


def _is_number(token: str) -> bool:
    return _NUMBER.fullmatch(token) is not None


def _has_digit(token: str) -> bool:
    return any(char.isdigit() for char in token)


def _is_code(token: str) -> bool:
    """A model code: letters and digits that are not a size, e.g. "wh1000xm5", "s24", "ax55"."""
    size = _SIZE.fullmatch(token)
    is_size = size is not None and size.group(2) in _UNIT_SPELLINGS
    return _has_digit(token) and not _is_number(token) and not is_size


def _shape(code: str) -> tuple[str, tuple[int, ...]]:
    """A model code's letters with each number as "#", and the numbers' lengths.

    "wh1000xm4" and "wh1000xm5" have one shape, so do "s23" and "s24"; "ax55" and "ax3000" do
    not (TP-Link's AX3000 is a speed class, not another model).
    """
    return _NUMBER.sub("#", code), tuple(len(run) for run in _DIGIT_RUN.findall(code))


def _significant(token: str) -> bool:
    """A token that can identify a product: any token with a digit ("5" in "PlayStation 5"),
    or a word of two letters or more that is not a noise word."""
    return _has_digit(token) or (len(token) >= 2 and token not in _NOISE_WORDS)


@dataclass(frozen=True)
class _Target:
    """What a listing title has to show to be the tracked item, from its name and keywords."""

    # Name tokens with a digit, e.g. "15", "wh1000xm5", "256gb": every one must be in the title.
    identifiers: frozenset[str]
    # The name's other significant words: at least half must be in the title.
    name_words: frozenset[str]
    # Significant tokens of every keyword term: every one must be in the title.
    keyword_tokens: frozenset[str]
    # A number and the word before it, e.g. ("playstation", "5"): see _breaks_slot.
    slots: frozenset[tuple[str, str]]
    # Name and keyword tokens that are model codes, e.g. "wh1000xm5", "s24": see _has_other_code.
    codes: frozenset[str]
    # Every name and keyword token. The item's own words never mark an accessory.
    own_words: frozenset[str]
    # The name's last significant token, e.g. "pro" for "iPhone 15 Pro".
    last_word: str


def _target(item_name: str, keywords: str | None) -> _Target | None:
    """The item's tokens; None if its name has none that can match ("The", "!!!")."""
    name = _tokens(item_name)
    significant = [token for token in name if _significant(token)]
    if not significant:
        return None
    phrases = [name, *(_tokens(term) for term in _keyword_terms(keywords))]
    keyword_tokens = {token for phrase in phrases[1:] for token in phrase if _significant(token)}
    return _Target(
        identifiers=frozenset(token for token in significant if _has_digit(token)),
        name_words=frozenset(token for token in significant if not _has_digit(token)),
        keyword_tokens=frozenset(keyword_tokens),
        slots=frozenset(
            (phrase[i - 1], phrase[i])
            for phrase in phrases
            for i in range(1, len(phrase))
            if _is_number(phrase[i]) and phrase[i - 1].isalpha() and _significant(phrase[i - 1])
        ),
        codes=frozenset(token for token in {*significant, *keyword_tokens} if _is_code(token)),
        own_words=frozenset(token for phrase in phrases for token in phrase),
        last_word=significant[-1],
    )


def _shows_name(tokens: set[str], target: _Target) -> bool:
    """True if the tokens hold every identifier of the item's name and half its other words."""
    if not target.identifiers <= tokens:
        return False
    if not target.name_words:
        return True
    return len(target.name_words & tokens) / len(target.name_words) >= _MIN_RELEVANCE_RATIO


def _has_condition_words(tokens: list[str]) -> bool:
    return any(
        tuple(tokens[i : i + len(words)]) == words
        for i in range(len(tokens))
        for words in _CONDITION_WORDS
    )


def _breaks_slot(tokens: list[str], slots: frozenset[tuple[str, str]]) -> bool:
    """True if a number from the item is not where its name puts it, or another number is.

    "Nintendo Switch 2" needs the 2 right after "Switch" wherever the title says "Switch": "Nintendo
    Switch Console Version 2" has a 2, but it is not the Switch's. "PlayStation 4 Slim ... with 5
    games" fails for "PlayStation 5" too. Numbers in other places are not checked: "with 2
    Controllers", "Spider-Man 2", a year. A title without the word, e.g. "PS5", is not checked here.
    """
    for word, number in slots:
        after = [tokens[i + 1] for i in range(len(tokens) - 1) if tokens[i] == word]
        if word not in tokens:
            continue
        if number not in after or any(_is_number(t) and t != number for t in after):
            return True
    return False


def _has_other_code(tokens: set[str], codes: frozenset[str]) -> bool:
    """True if the title has a model code shaped like one of the item's, but different:
    "WH-1000XM4" next to "WH-1000XM5", "V11" in a title about "Dyson V15 Detect"."""
    shapes = {_shape(code) for code in codes}
    return any(t not in codes and _is_code(t) and _shape(t) in shapes for t in tokens)


def _phrases(title: str) -> list[tuple[str, list[str]]]:
    """The title's phrases, each with the break before it ("" for the first); brackets left out.

    "Spigen for iPhone 15 Pro Case, Ultra Hybrid [No Magnet Ring]" gives ("", ["spigen"]),
    ("for", ["iphone", "15", "pro", "case"]) and (",", ["ultra", "hybrid"]).
    """
    parts = _PHRASE_BREAK.split(_BRACKETED.sub(" ", title.lower()))
    return [("", _tokens(parts[0]))] + [
        (parts[i].strip(), _tokens(parts[i + 1])) for i in range(1, len(parts), 2)
    ]


def _is_accessory(title: str, target: _Target) -> bool:
    """True if the title sells an accessory for the item, not the item.

    An accessory term that is not one of the item's own words counts only:
    - as the head noun, the last word of the title's first phrase: "Google Pixel 10 Pro XL Case
      Cover", "Tempered Glass Screen Protector for Pixel 10 Pro";
    - right after the last word of the item's name: "Spigen for iPhone 15 Pro Case", "Monitor
      Free-Standing Desk Stand Mount Riser" for "Standing desk";
    - before "for", "compatible" or "fits" when the phrase after it names the item: "Replacement
      Ear Pads Compatible with Sony WH-1000XM5 Headphone".
    """

    def is_term(token: str) -> bool:
        return token in _ACCESSORY_TERMS and token not in target.own_words

    phrases = _phrases(title)
    head = next((tokens[-1] for _, tokens in phrases if tokens), None)
    if head is not None and is_term(head):
        return True
    for _, tokens in phrases:
        if any(a == target.last_word and is_term(b) for a, b in itertools.pairwise(tokens)):
            return True
    for k, (before_phrase, _) in enumerate(phrases):
        if before_phrase not in _FOR_WORDS:
            continue
        subject = [token for _, tokens in phrases[:k] for token in tokens]
        fitted = next((tokens for _, tokens in phrases[k:] if tokens), [])
        if any(is_term(token) for token in subject) and _shows_name(set(fitted), target):
            return True
    return False


def _has_accessory_extension(result: SearchResult) -> bool:
    """Return True if the result's extensions metadata indicates an accessory."""
    if not result.extensions:
        return False
    return any(ext.lower() in _ACCESSORY_EXTENSIONS for ext in result.extensions)


def _is_relevant(
    result: SearchResult,
    item_name: str,
    target_price: float | None = None,
    keywords: str | None = None,
) -> bool:
    """Check whether a search result is the tracked item itself (#3).

    A result is irrelevant (returns False) when any of these hold:
    - Its price falls below the price floor (10% of target_price).
    - Its extensions metadata contains accessory-category terms.
    - It is second-hand: it has a condition (Google's second_hand_condition, any value), or its
      title says Renewed, Refurbished, Restored, Used, Pre-Owned, Open Box or For Parts.
    - The item name has no token that can match ("The", "!!!").
    - The title lacks a name token with a digit ("5", "WH-1000XM5", "256GB"), a token of a
      keyword term (keywords are comma-separated, as in the search query), or half the name's
      other significant words.
    - A number from the item is not right after the word it follows in the name, or another
      number is ("PlayStation 4" for "PlayStation 5"); or the title has a model code shaped like
      one of the item's but different ("WH-1000XM4" for "WH-1000XM5").
    - It is an accessory for the item (_is_accessory).
    """
    if target_price and target_price > 0 and result.price < target_price * _PRICE_FLOOR_RATIO:
        return False

    if _has_accessory_extension(result):
        return False

    if result.condition:
        return False

    target = _target(item_name, keywords)
    if target is None:
        return False

    tokens = _tokens(result.title)
    if _has_condition_words(tokens):
        return False

    present = set(tokens)
    if not (_shows_name(present, target) and target.keyword_tokens <= present):
        return False
    if _breaks_slot(_tokens(_BRACKETED.sub(" ", result.title)), target.slots):
        return False
    if _has_other_code(present, target.codes):
        return False
    return not _is_accessory(result.title, target)


def _filter_relevant(results: list[SearchResult], item: Item) -> list[SearchResult]:
    relevant = [
        r for r in results if _is_relevant(r, item.name, item.target_price, keywords=item.keywords)
    ]
    filtered_count = len(results) - len(relevant)
    if filtered_count:
        logger.info(
            "Relevance filter: kept %d of %d results for item %d (%d filtered out)",
            len(relevant),
            len(results),
            item.id,
            filtered_count,
        )
    return relevant


def _persist_results(results: list[SearchResult], item: Item, db: Session) -> list[PriceRecord]:
    """Save search results as PriceRecord rows and return them."""
    records: list[PriceRecord] = []
    for r in results:
        record = PriceRecord(
            item_id=item.id,
            price=r.price,
            currency=r.currency,
            source=r.source,
            url=r.url,
            title=r.title,
        )
        db.add(record)
        records.append(record)
    db.commit()
    # Refresh to populate auto-generated fields (id, checked_at)
    for rec in records:
        db.refresh(rec)
    return records


def run_price_check(item: Item, db: Session) -> PriceCheckResult:
    """Execute a price check across all engines for a single item.

    1. Build search query from item name + keywords.
    2. Query each registered engine; an engine that cannot search is logged at ERROR and
       listed in engine_errors, so a failed search is not reported as "no results".
    3. Persist all price observations.
    4. If any price meets the target, fire an email notification.
    5. Return structured results.
    """
    query = _build_query(item)
    logger.info("Running price check for item %d: %r", item.id, query)

    all_results: list[SearchResult] = []
    engine_errors: list[str] = []
    for engine in _ENGINES:
        try:
            results = engine.search(query)
            all_results.extend(results)
        except EngineError as e:
            # Its message holds the HTTP status and SerpAPI's error string, never the key.
            logger.error("Engine %s failed for item %d: %r", engine.name, item.id, str(e))
            engine_errors.append(f"{engine.name}: {e}")
        except Exception as e:
            # A bug: the traceback is logged (the log setup redacts the key), but exception
            # text can carry the request URL and its key, so the result names the type only.
            logger.exception("Engine %s failed for item %d", engine.name, item.id)
            engine_errors.append(f"{engine.name}: unexpected error ({type(e).__name__})")

    relevant_results = _filter_relevant(all_results, item)

    relevant_results.sort(key=lambda r: r.price)

    records = _persist_results(relevant_results, item, db)

    lowest: SearchResult | None = relevant_results[0] if relevant_results else None

    if lowest and item.target_price and lowest.price <= item.target_price:
        logger.info(
            "Target met for item %d (%r): $%.2f <= $%.2f",
            item.id,
            item.name,
            lowest.price,
            item.target_price,
        )
        send_price_alert(item=item, result=lowest)

    return PriceCheckResult(
        item_id=item.id,
        item_name=item.name,
        lowest_price=lowest.price if lowest else None,
        lowest_source=lowest.source if lowest else None,
        lowest_merchant=lowest.merchant if lowest else None,
        lowest_url=lowest.url if lowest else None,
        results_count=len(relevant_results),
        records=[PriceRecordResponse.model_validate(r) for r in records],
        engine_errors=engine_errors,
    )
