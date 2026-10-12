"""The heartbeat: one GET to HEARTBEAT_URL after each check of all items that succeeds (#24).

A push monitor (healthchecks.io, Uptime Kuma) that stops getting it raises the alarm outside the
app, whether the daily job stopped running or its checks stopped succeeding.
"""

import logging

import httpx

from hunter_bargain.config import settings

logger = logging.getLogger(__name__)

# Seconds to wait for HEARTBEAT_URL to connect, and then for each read and write. The check of all
# items that sends the heartbeat holds the run lock until then.
HEARTBEAT_TIMEOUT = 10


def send_heartbeat(transport: httpx.BaseTransport | None = None) -> bool:
    """Send HEARTBEAT_URL one GET; True if it answered with a 2xx status.

    Nothing is sent while HEARTBEAT_URL is empty. The URL holds the monitor's token, so neither
    it nor an error's text, which can quote it, is logged: a failure is a warning with the HTTP
    status or the error's type only, and is never raised. `transport` replaces the network, e.g.
    with an httpx.MockTransport.
    """
    url = settings.heartbeat_url.get_secret_value()
    if not url:
        return False
    try:
        with httpx.Client(transport=transport, timeout=HEARTBEAT_TIMEOUT) as client:
            response = client.get(url)
    except Exception as e:
        logger.warning("Heartbeat failed: %s", type(e).__name__)
        return False
    if not response.is_success:
        logger.warning("Heartbeat failed: HTTP %d", response.status_code)
        return False
    logger.info("Heartbeat sent")
    return True
