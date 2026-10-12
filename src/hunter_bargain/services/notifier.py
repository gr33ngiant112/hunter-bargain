"""Email notification service.

Sends price alerts via SMTP when a tracked item hits its target price, and notices about the app
itself (SerpAPI searches running low) to ALERT_RECIPIENTS.
"""

import html
import logging
import smtplib
import ssl
import unicodedata
from email.message import Message
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib.parse import urlparse

from hunter_bargain.config import settings
from hunter_bargain.models import Item
from hunter_bargain.schemas import PriceLow
from hunter_bargain.services.engines.base import SearchResult

logger = logging.getLogger(__name__)

# The alert links to a listing URL only with one of these schemes.
_LINK_SCHEMES = frozenset({"http", "https"})


def _escape(value: object) -> str:
    """HTML-escape a value for element text or a double-quoted attribute value."""
    return html.escape(str(value), quote=True)


def _link_url(url: str | None) -> str | None:
    """Return the listing URL if the alert may link to it, otherwise None.

    Listing URLs come from third-party shopping results. Only an http(s) URL gets a link:
    javascript:, data: or relative URLs get none. Neither does a URL with control characters
    (CR or LF would start a new line in the plain-text part) or one that does not parse.
    """
    if not url or any(unicodedata.category(c) == "Cc" for c in url):
        return None
    try:
        scheme = urlparse(url).scheme
    except ValueError:  # e.g. an unclosed "[" in the host
        return None
    return url if scheme in _LINK_SCHEMES else None


def _source_label(result: SearchResult) -> str:
    """The engine name, after the merchant if the listing names one: "Walmart via google_shopping".

    The merchant is third-party text. Its line breaks (CR, LF, U+2028 and the like) become
    spaces, so it cannot add a line to the plain-text part.
    """
    merchant = " ".join((result.merchant or "").split())
    return f"{merchant} via {result.source}" if merchant else result.source


def _price_lows(
    low_90_days: PriceLow | None, all_time_low: PriceLow | None
) -> list[tuple[str, str]]:
    """The lows to show, as (label, value) pairs, e.g. ("All-time low", "$9.99 on 2026-10-01")."""
    lows = [("Lowest in 90 days", low_90_days), ("All-time low", all_time_low)]
    return [(label, f"${low.price:.2f} on {low.checked_at:%Y-%m-%d}") for label, low in lows if low]


def _verify_merchant_warning() -> str:
    """What an alert says about a price far below the check's other prices (#29)."""
    return (
        f"Verify merchant: this price is more than {settings.verify_merchant_pct:g}% below the"
        " median of this check's prices. Check the seller before you buy."
    )


def _build_html_body(
    item: Item, result: SearchResult, lows: list[tuple[str, str]], verify_merchant: bool
) -> str:
    """Build the HTML email body for a price alert.

    The item name comes from an API caller and the title, currency, merchant and URL from a
    third-party listing, so every value is HTML-escaped before it goes into the markup. A price
    to verify gets a warning, and its button says "Verify merchant" instead of "Buy Now".
    """
    name = _escape(item.name)
    title = _escape(result.title)
    price = _escape(f"${result.price:.2f} {result.currency}")
    target = _escape(f"${item.target_price:.2f}")
    source = _escape(_source_label(result))
    cell = "padding: 8px; border: 1px solid #ddd;"
    lows_html = "".join(
        f'\n            <tr><td style="{cell} font-weight: bold;">{_escape(label)}</td>'
        f'<td style="{cell}">{_escape(value)}</td></tr>'
        for label, value in lows
    )

    warning_html = ""
    if verify_merchant:
        warning_html = (
            '<p style="color: #b45309; font-weight: bold;">'
            f"{_escape(_verify_merchant_warning())}</p>"
        )

    link_html = ""
    url = _link_url(result.url)
    if url:
        button, color = (
            ("Verify merchant", "#b45309") if verify_merchant else ("Buy Now", "#2d8a4e")
        )
        link_html = (
            '<p style="text-align: center; margin: 24px 0;">'
            f'<a href="{_escape(url)}" '
            f'style="display: inline-block; background-color: {color}; color: #ffffff; '
            "padding: 14px 32px; border-radius: 8px; text-decoration: none; "
            'font-size: 16px; font-weight: 700;">'
            f"{button}"
            "</a></p>"
        )

    return f"""
    <html>
    <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
        <h2 style="color: #2d8a4e;">Price Alert: {name}</h2>{warning_html}
        <p>Great news! We found <strong>{name}</strong> at or below your target price.</p>
        <table style="border-collapse: collapse; width: 100%; margin: 16px 0;">
            <tr>
                <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">Product</td>
                <td style="padding: 8px; border: 1px solid #ddd;">{title}</td>
            </tr>
            <tr>
                <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">Price</td>
                <td style="padding: 8px; border: 1px solid #ddd; color: #2d8a4e; font-size: 1.2em;">
                    {price}
                </td>
            </tr>
            <tr>
                <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">Target</td>
                <td style="padding: 8px; border: 1px solid #ddd;">{target}</td>
            </tr>{lows_html}
            <tr>
                <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">Source</td>
                <td style="padding: 8px; border: 1px solid #ddd;">{source}</td>
            </tr>
        </table>
        {link_html}
        <hr style="border: none; border-top: 1px solid #eee; margin: 24px 0;">
        <p style="color: #888; font-size: 0.85em;">
            Sent by hunter-bargain. Manage your tracked items via the API.
        </p>
    </body>
    </html>
    """


def _smtp_configured() -> bool:
    """Whether email can be sent. SMTP_SECURITY=none is for a local test server such as Mailpit:
    no TLS and no login, so it needs no credentials. Every other value takes the TLS path, which
    needs SMTP_USER and SMTP_PASSWORD."""
    return settings.smtp_security == "none" or bool(settings.smtp_user and settings.smtp_password)


def _send(message: Message) -> None:
    """Send `message` through SMTP_HOST; an error is raised to the caller.

    SMTP_SECURITY=tls: TLS that verifies the server certificate (implicit on port 465, STARTTLS
    on any other), then the login. none: neither.
    """
    tls = settings.smtp_security != "none"
    # Verify the server certificate and hostname on both paths: without a context,
    # starttls() skips verification.
    context = ssl.create_default_context()
    implicit_tls = tls and settings.smtp_port == 465  # SMTPS: TLS from the first byte
    connection: smtplib.SMTP  # SMTP_SSL is a subclass
    if implicit_tls:
        connection = smtplib.SMTP_SSL(
            settings.smtp_host,
            settings.smtp_port,
            context=context,
            timeout=settings.smtp_timeout,
        )
    else:
        connection = smtplib.SMTP(
            settings.smtp_host, settings.smtp_port, timeout=settings.smtp_timeout
        )

    with connection as server:
        # Without TLS there is no login either: the password never crosses a plain connection.
        if tls:
            if not implicit_tls:
                server.starttls(context=context)
            server.login(settings.smtp_user, settings.smtp_password.get_secret_value())
        server.send_message(message)


def send_price_alert(
    item: Item,
    result: SearchResult,
    low_90_days: PriceLow | None = None,
    all_time_low: PriceLow | None = None,
    verify_merchant: bool = False,
) -> bool:
    """Send a price alert email for an item that hit its target.

    The email also shows the item's lowest recorded price in the last 90 days and its all-time
    low, when they are given (#31). With `verify_merchant`, a price far below the check's other
    prices, it warns to check the seller instead of offering "Buy Now" (#29).
    Returns True if the email was sent successfully, False otherwise.
    Fails gracefully — never raises to the caller.
    """
    if not _smtp_configured():
        logger.warning("SMTP not configured — skipping email notification for item %d", item.id)
        return False

    # The API only accepts ALERT_RECIPIENTS addresses, but rows saved before that check can hold
    # any address, so check again before sending. The address itself is not logged.
    if not settings.is_alert_recipient(item.notify_email):
        logger.warning(
            "Recipient for item %d is not in ALERT_RECIPIENTS — skipping email notification",
            item.id,
        )
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"Price Alert: {item.name} — ${result.price:.2f}"
        msg["From"] = settings.email_from or settings.smtp_user
        msg["To"] = item.notify_email

        # Plain text fallback; like the HTML part, it shows the link only for an http(s) URL.
        lows = _price_lows(low_90_days, all_time_low)
        warning = f"{_verify_merchant_warning()}\n" if verify_merchant else ""
        plain = (
            f"Price Alert: {item.name}\n"
            + warning
            + f"Price: ${result.price:.2f} {result.currency}\n"
            f"Target: ${item.target_price:.2f}\n"
            + "".join(f"{label}: {value}\n" for label, value in lows)
            + f"Source: {_source_label(result)}\n"
            f"Link: {_link_url(result.url) or 'N/A'}\n"
        )
        msg.attach(MIMEText(plain, "plain"))
        msg.attach(MIMEText(_build_html_body(item, result, lows, verify_merchant), "html"))
        _send(msg)

        logger.info("Price alert sent to %s for item %d", item.notify_email, item.id)
        return True

    except Exception:
        logger.exception("Failed to send price alert for item %d", item.id)
        return False


def send_notice(subject: str, text: str) -> bool:
    """Email a plain-text notice about the app itself to every ALERT_RECIPIENTS address.

    Returns True if it was sent. Skipped, with a warning in the log, when SMTP is not configured
    or ALERT_RECIPIENTS is empty. Never raises.
    """
    if not _smtp_configured():
        logger.warning("SMTP not configured — skipping the email: %s", subject)
        return False
    if not settings.alert_recipients:
        logger.warning("ALERT_RECIPIENTS is empty — skipping the email: %s", subject)
        return False
    try:
        msg = MIMEText(text, "plain")
        msg["Subject"] = subject
        msg["From"] = settings.email_from or settings.smtp_user
        msg["To"] = ", ".join(settings.alert_recipients)
        _send(msg)
    except Exception:
        logger.exception("Failed to send the email: %s", subject)
        return False
    count = len(settings.alert_recipients)
    logger.info("Email sent to %d ALERT_RECIPIENTS address(es): %s", count, subject)
    return True
