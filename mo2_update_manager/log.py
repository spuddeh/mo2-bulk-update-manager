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
    large modlist, which is why it is opt-in.
``WARNING``
    Something the user should know went wrong but the run continued.
``ERROR``
    Something failed outright.

Credentials are never logged -- only which source they came from.
"""

from __future__ import annotations

import logging

ROOT_NAME = "mo2_update_manager"


def get_logger(name: str = "") -> logging.Logger:
    """A logger under this plugin's namespace, so its lines are greppable."""
    return logging.getLogger(f"{ROOT_NAME}.{name}" if name else ROOT_NAME)


def log_exception(logger: logging.Logger, message: str, exc: BaseException) -> None:
    """Record a caught exception with its traceback, without re-raising."""
    logger.error("%s: %s: %s", message, type(exc).__name__, exc, exc_info=True)
