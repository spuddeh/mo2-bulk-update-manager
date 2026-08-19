"""Logging that lands in MO2's own log.

MO2 attaches a handler to the *root* Python logger and sets it to DEBUG
(``plugin_python/src/runner/pythonutils.cpp:151-152``), so plain
``logging.getLogger(...)`` output goes straight to ``logs/mo_interface.log``
and obeys whatever log level the user has set in MO2. Nothing needs
configuring here; ``mobase.log`` does not exist and is not wanted.

Levels, chosen so a bug report is useful without DEBUG being turned on:

``INFO``
    The shape of a run -- what was scanned, what was decided, what was
    downloaded or installed. A handful of lines per session.
``DEBUG``
    Per-mod classification and per-request detail. Hundreds of lines on a
    large modlist, so it is opt-in.
``WARNING``
    Something the user should know went wrong but the run continued.
``ERROR``
    Something failed outright.

Credentials are never logged -- only which source they came from.

**Format every message eagerly.** MO2's handler reads ``record.msg``
(``pythonutils.cpp:103``), not ``record.getMessage()``, so the usual lazy
``log.info("scanned %s mods", n)`` reaches the log file as the literal string
``scanned %s mods``. Use an f-string. For the same reason ``exc_info`` is
never rendered, so a traceback has to be folded into the message itself --
that is what `log_exception` is for.
"""

from __future__ import annotations

import logging
import traceback

ROOT_NAME = "mo2_bulk_update_manager"

PREFIX = "[BulkUpdateManager]"


def get_logger(name: str = "") -> logging.Logger:
    """A logger under this plugin's namespace, so its lines are greppable."""
    return logging.getLogger(f"{ROOT_NAME}.{name}" if name else ROOT_NAME)


def tag(message: str) -> str:
    """Mark a line as this plugin's.

    MO2 logs only the message, not the logger name, so without this the
    plugin's lines are indistinguishable from MO2's own in a bug report.
    """
    return f"{PREFIX} {message}"


def log_exception(logger: logging.Logger, message: str, exc: BaseException) -> None:
    """Record a caught exception with its traceback, without re-raising."""
    trace = "".join(
        traceback.format_exception(type(exc), exc, exc.__traceback__)
    ).strip()
    logger.error(tag(f"{message}: {type(exc).__name__}: {exc}\n{trace}"))
