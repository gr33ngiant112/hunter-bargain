"""Logging setup for the app, including SerpAPI key redaction.

SerpAPI takes its key as a URL query parameter, so request URLs carry it into
urllib3's DEBUG records and into requests/urllib3 exception text. Redaction runs
in the log record factory: each record, from any logger, is redacted when it is
created and before any handler sees it. That covers the root handler set up here
as well as handlers the app does not own (uvicorn's, pytest's caplog) or that are
added later, which a ``logging.Filter`` on the root handler would miss.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Mapping
from typing import Any

_API_KEY = re.compile(r"api_key=[^&\s'\"]+")
_REDACTED = "api_key=***"
_TRACEBACK_FORMATTER = logging.Formatter()


def _redact(text: str) -> str:
    return _API_KEY.sub(_REDACTED, text)


def _redact_args(args: tuple[Any, ...] | Mapping[str, Any]) -> tuple[Any, ...] | dict[str, Any]:
    if isinstance(args, Mapping):
        return {name: _redact(v) if isinstance(v, str) else v for name, v in args.items()}
    return tuple(_redact(a) if isinstance(a, str) else a for a in args)


def _redact_message(record: logging.LogRecord) -> None:
    try:
        message = record.getMessage()
    except Exception:
        return  # malformed logging call: logging reports it when the record is emitted
    redacted = _redact(message)
    if redacted == message:
        return
    if record.args:
        # Redact string arguments in place first, so formatters that unpack
        # record.args (uvicorn's access log) keep working.
        record.args = _redact_args(record.args)
        if record.getMessage() == redacted:
            return
    record.msg, record.args = redacted, None


def _redact_traceback(record: logging.LogRecord) -> None:
    # logger.exception() leaves the traceback unformatted until a handler emits
    # the record; Formatter.format() then reuses record.exc_text when it is set.
    if record.exc_info:
        text = _TRACEBACK_FORMATTER.formatException(record.exc_info)
        redacted = _redact(text)
        if redacted != text:
            record.exc_text = redacted


class _RedactingRecordFactory:
    """Wraps a log record factory and redacts every record it creates."""

    def __init__(self, wrapped: Callable[..., logging.LogRecord]) -> None:
        self._wrapped = wrapped

    def __call__(self, *args: Any, **kwargs: Any) -> logging.LogRecord:
        record = self._wrapped(*args, **kwargs)
        _redact_message(record)
        _redact_traceback(record)
        return record


def configure_logging(level_name: str) -> None:
    """Log to stderr at *level_name* and keep the SerpAPI key out of every log record."""
    level = getattr(logging, level_name.upper(), logging.INFO)
    logging.basicConfig(level=level, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    # urllib3 logs every request URL, key included, at DEBUG.
    logging.getLogger("urllib3").setLevel(max(level, logging.WARNING))
    factory = logging.getLogRecordFactory()
    if not isinstance(factory, _RedactingRecordFactory):
        logging.setLogRecordFactory(_RedactingRecordFactory(factory))
