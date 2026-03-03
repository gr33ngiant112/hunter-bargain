"""Email notification service.

Sends price alerts via SMTP when a tracked item hits its target price.
"""

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from hunter_bargain.config import settings
from hunter_bargain.models import Item
from hunter_bargain.services.engines.base import SearchResult

logger = logging.getLogger(__name__)


def _build_html_body(item: Item, result: SearchResult) -> str:
    """Build the HTML email body for a price alert."""
    link_html = ""
    if result.url:
        link_html = f'<p><a href="{result.url}">View Listing</a></p>'

    return f"""
    <html>
    <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
        <h2 style="color: #2d8a4e;">Price Alert: {item.name}</h2>
        <p>Great news! We found <strong>{item.name}</strong> at or below your target price.</p>
        <table style="border-collapse: collapse; width: 100%; margin: 16px 0;">
            <tr>
                <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">Product</td>
                <td style="padding: 8px; border: 1px solid #ddd;">{result.title}</td>
            </tr>
            <tr>
                <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">Price</td>
                <td style="padding: 8px; border: 1px solid #ddd; color: #2d8a4e; font-size: 1.2em;">
                    ${result.price:.2f} {result.currency}
                </td>
            </tr>
            <tr>
                <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">Target</td>
                <td style="padding: 8px; border: 1px solid #ddd;">${item.target_price:.2f}</td>
            </tr>
            <tr>
                <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">Source</td>
                <td style="padding: 8px; border: 1px solid #ddd;">{result.source}</td>
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
    if not settings.smtp_user or not settings.smtp_password:
        logger.warning("SMTP not configured — skipping email notification for item %d", item.id)
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"Price Alert: {item.name} — ${result.price:.2f}"
        msg["From"] = settings.email_from or settings.smtp_user
        msg["To"] = item.notify_email

        # Plain text fallback
        plain = (
            f"Price Alert: {item.name}\n"
            f"Price: ${result.price:.2f} {result.currency}\n"
            f"Target: ${item.target_price:.2f}\n"
            f"Source: {result.source}\n"
            f"Link: {result.url or 'N/A'}\n"
        )
        msg.attach(MIMEText(plain, "plain"))
        msg.attach(MIMEText(_build_html_body(item, result), "html"))

        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
            server.starttls()
            server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(msg)

        logger.info("Price alert sent to %s for item %d", item.notify_email, item.id)
        return True

    except Exception:
        logger.exception("Failed to send price alert for item %d", item.id)
        return False
