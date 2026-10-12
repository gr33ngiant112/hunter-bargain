"""SerpAPI's searches left: a warning when they run low (#24)."""

import logging

from hunter_bargain.config import settings
from hunter_bargain.services.engines.base import EngineError, SerpApiAccount, fetch_serpapi_account
from hunter_bargain.services.notifier import send_notice

logger = logging.getLogger(__name__)


def check_searches_left(searches_per_run: int) -> None:
    """Warn when SerpAPI has fewer searches left than two checks of all items make.

    Called after a check of all items that made `searches_per_run` searches, those that failed
    included, since a run in which SerpAPI refused every search for want of searches is the one
    to warn about. Reads the account from SerpAPI's Account API, which costs no search. Below
    twice that many searches left, logs a warning and emails it to ALERT_RECIPIENTS when SMTP is
    set up. Nothing is read without SERPAPI_KEY, or after a run that made no search.
    """
    if not settings.serpapi_key or searches_per_run == 0:
        return
    try:
        account = fetch_serpapi_account(settings.serpapi_key.get_secret_value())
    except EngineError as e:
        logger.warning("SerpAPI account check failed: %s", e)
        return
    left = account.total_searches_left
    if left is None:
        logger.warning("SerpAPI account check failed: the answer has no total_searches_left")
        return
    if left >= 2 * searches_per_run:
        logger.info("SerpAPI searches left: %d", left)
        return
    message = _running_low(account, left, searches_per_run)
    logger.warning("%s", message)
    send_notice(f"hunter-bargain: SerpAPI searches running low ({left} left)", message)


def _running_low(account: SerpApiAccount, left: int, searches_per_run: int) -> str:
    """The warning, for the log and the email: the account's counts, and nothing else of it."""

    def shown(value: object) -> object:
        return "n/a" if value is None else value

    return (
        f"SerpAPI searches are running low: {left} left, and a check of all items makes"
        f" {searches_per_run} (used this month: {shown(account.this_month_usage)} of"
        f" {shown(account.searches_per_month)}; plan renews: {shown(account.plan_renewal_date)};"
        f" hourly limit: {shown(account.account_rate_limit_per_hour)})."
    )
