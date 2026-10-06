"""Email notification service.

Sends price alerts via SMTP when a tracked item hits its target price.
"""

import html
import logging
import smtplib
import ssl
import unicodedata
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib.parse import urlparse

from hunter_bargain.config import settings
from hunter_bargain.models import Item
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


def _build_html_body(item: Item, result: SearchResult) -> str:
    """Build the HTML email body for a price alert.

    The item name comes from an API caller and the title, currency, merchant and URL from a
    third-party listing, so every value is HTML-escaped before it goes into the markup.
    """
    name = _escape(item.name)
    title = _escape(result.title)
    price = _escape(f"${result.price:.2f} {result.currency}")
    target = _escape(f"${item.target_price:.2f}")
    source = _escape(_source_label(result))

    link_html = ""
    url = _link_url(result.url)
    if url:
        link_html = (
            '<p style="text-align: center; margin: 24px 0;">'
            f'<a href="{_escape(url)}" '
            'style="display: inline-block; background-color: #2d8a4e; color: #ffffff; '
            "padding: 14px 32px; border-radius: 8px; text-decoration: none; "
            'font-size: 16px; font-weight: 700;">'
            "Buy Now"
            "</a></p>"
        )

    return f"""
    <html>
    <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
        <h2 style="color: #2d8a4e;">Price Alert: {name}</h2>
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
            </tr>
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


def send_price_alert(item: Item, result: SearchResult) -> bool:
    """Send a price alert email for an item that hit its target.

    Returns True if the email was sent successfully, False otherwise.
    Fails gracefully — never raises to the caller.
    """
    # SMTP_SECURITY=none is for a local test server such as Mailpit: no TLS and no login, so it
    # needs no credentials. Every other value takes the TLS path.
    tls = settings.smtp_security != "none"
    if tls and (not settings.smtp_user or not settings.smtp_password):
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
        plain = (
            f"Price Alert: {item.name}\n"
            f"Price: ${result.price:.2f} {result.currency}\n"
            f"Target: ${item.target_price:.2f}\n"
            f"Source: {_source_label(result)}\n"
            f"Link: {_link_url(result.url) or 'N/A'}\n"
        )
        msg.attach(MIMEText(plain, "plain"))
        msg.attach(MIMEText(_build_html_body(item, result), "html"))

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
            server.send_message(msg)

        logger.info("Price alert sent to %s for item %d", item.notify_email, item.id)
        return True

    except Exception:
        logger.exception("Failed to send price alert for item %d", item.id)
        return False
