"""The labelled relevance corpus (#3): every listing title in it gets its expected label.

tests/fixtures/relevance_cases.jsonl holds one case per line: the tracked item's name, a listing
title, the expected label ("keep" or "reject"), where the title comes from and why it has that
label. A case can also give the item's keywords, or the second_hand_condition its Google row
carries.
"""

import json
import re
from pathlib import Path

import pytest

from hunter_bargain.services.engines.base import SearchResult
from hunter_bargain.services.searcher import _is_relevant

CORPUS = Path(__file__).parent / "fixtures" / "relevance_cases.jsonl"
# (line number, case) for every line of the corpus.
CASES = [
    (number, json.loads(line))
    for number, line in enumerate(CORPUS.read_text(encoding="utf-8").splitlines(), start=1)
    if line.strip()
]

# No target price is passed, so the 10% price floor never applies to this price.
PRICE = 100.0

# The examples in #3's Evidence section, with their correct labels. The issue names each item and
# describes each listing, so these titles are written from its words (each case's reason says
# so), and their source is "issue #3 probe".
ISSUE_EXAMPLES = {
    # Kept before the fix.
    ("iPhone 15 Pro", "Apple iPhone 13 Pro 128GB"): "reject",
    ("Sony WH-1000XM5", "Sony WH-1000XM4"): "reject",
    ("PlayStation 5", "PlayStation 4 Slim"): "reject",
    ("iPhone 15 Pro", "iPhone 15 Pro - Renewed"): "reject",
    ("Standing desk", "Monitor Stand for Standing Desk"): "reject",
    ("The", "Phone Case"): "reject",
    # Rejected before the fix.
    ("TP-Link Archer AX55", "TP-Link Archer AX55 Dual-Band Wi-Fi 6 Router"): "keep",
    ("Sony Alpha 7 IV", "Sony Alpha 7 IV Mirrorless Camera with 28-70mm Lens Kit"): "keep",
    ("Shark Navigator Lift-Away", "Shark Navigator Lift-Away Upright Vacuum with HEPA Filter"): (
        "keep"
    ),
    ("Fire HD 10", "Fire HD 10 Touch Screen Tablet"): "keep",
    ("Lenovo IdeaPad Slim 3", "Lenovo IdeaPad Slim 3 Laptop with Backlit Keyboard"): "keep",
    ("Dell S2721D", "Dell S2721D Monitor with Adjustable Stand"): "keep",
}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40]


def _case_id(number: int, case: dict) -> str:
    """The case's line, label, item, keywords or condition if any, and title.

    e.g. "L003-reject-playstation-5-playstation-4-slim", "L028-reject-iphone-14-kw-256gb-apple-..."
    """
    parts = [f"L{number:03d}", case["expected"], _slug(case["item"])]
    if case.get("keywords"):
        parts += ["kw", _slug(case["keywords"])]
    if case.get("condition"):
        parts += ["condition", _slug(case["condition"])]
    return "-".join([*parts, _slug(case["title"])])


@pytest.mark.parametrize("case", [pytest.param(case, id=_case_id(n, case)) for n, case in CASES])
def test_case_gets_its_label(case):
    result = SearchResult(
        title=case["title"],
        price=PRICE,
        currency="USD",
        source="google_shopping",
        condition=case.get("condition"),
    )

    relevant = _is_relevant(result, case["item"], keywords=case.get("keywords"))

    assert relevant is (case["expected"] == "keep"), case["reason"]


def test_corpus_has_at_least_30_cases():
    assert len(CASES) >= 30


def test_every_case_has_a_source_and_a_label():
    """A label other than "keep" or "reject" would read as "reject" in test_case_gets_its_label."""
    bad_lines = [
        number
        for number, case in CASES
        if not (isinstance(case.get("source"), str) and case["source"].strip())
        or case.get("expected") not in {"keep", "reject"}
    ]

    assert bad_lines == []


def test_issue_examples_are_cases_with_their_correct_labels():
    probes = {
        (case["item"], case["title"]): case["expected"]
        for _, case in CASES
        if case["source"] == "issue #3 probe"
    }

    assert probes == ISSUE_EXAMPLES
