"""
utils.py
========

Shared, dependency-free helper functions used across the Portable SSH
project: logging setup, subprocess execution, privilege checks, and
small formatting helpers.

This module must never contain OS-detection or business logic (that
belongs in platform.py, installer.py, services.py). Everything here
is a generic, reusable primitive.
"""

from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from typing import Sequence

LOGGER_NAME = "portable_ssh"


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def setup_logging(verbose: bool = False) -> logging.Logger:
    """
    Configure and return the application-wide logger.

    Normal mode: only WARNING and above are shown, with a clean,
    user-friendly format (no timestamps, no tracebacks).

    Verbose/debug mode: DEBUG and above are shown, with timestamps
    and module names, for diagnostics.

    This should be called exactly once, early in portable_ssh.py's
    entry point. Subsequent calls to logging.getLogger(LOGGER_NAME)
    anywhere else in the project will reuse this configuration.
    """
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)

    # Avoid duplicate handlers if setup_logging is ever called twice
    # (e.g. in tests).
    logger.handlers.clear()

    handler = logging.StreamHandler(stream=sys.stdout)

    if verbose:
        fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        datefmt = "%H:%M:%S"
    else:
        fmt = "[%(levelname)s] %(message)s"
        datefmt = None

    handler.setFormatter(logging.Formatter(fmt=fmt, datefmt=datefmt))
    logger.addHandler(handler)
    logger.propagate = False
    return logger


def get_logger() -> logging.Logger:
    """
    Retrieve the shared application logger from anywhere in the
    project without needing to pass a logger instance around.

    Assumes setup_logging() has already been called by the entry
    point. If it hasn't (e.g. a module is used standalone/in a test),
    this returns a logger with Python's default "no handler" behavior,
    which is safe (messages are simply dropped, not crash-prone).
    """
    return logging.getLogger(LOGGER_NAME)


# ---------------------------------------------------------------------------
# Subprocess execution
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CommandResult:
    """
    Normalized result of running a shell command.

    Every module that shells out (installer.py, services.py,
    network.py) should go through run_command() and receive this
    type, rather than handling subprocess.CompletedProcess directly.
    This keeps error handling and logging consistent project-wide.
    """
    command: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    @property
    def success(self) -> bool:
        return self.returncode == 0


class CommandNotFoundError(RuntimeError):
    """Raised when the executable for a command does not exist on PATH."""


def run_command(
    command: Sequence[str],
    *,
    timeout: float = 30.0,
    check: bool = False,
) -> CommandResult:
    """
    Run a command and return a normalized CommandResult.

    This is the ONLY place in the project that should call
    subprocess.run() directly. All other modules must go through
    this function so that:
      - logging of executed commands is consistent
      - timeouts are always enforced (no hangs waiting on a stuck
        package manager or service call)
      - errors are surfaced as CommandResult rather than raised
        exceptions bubbling up as raw tracebacks to the end user

    Parameters
    ----------
    command:
        The command and its arguments as a list, e.g.
        ["systemctl", "start", "ssh"]. Never pass a single shell
        string; this avoids shell-injection risk entirely since
        shell=False is always used.
    timeout:
        Seconds to wait before giving up. Package installs may need
        a longer timeout than service start/stop calls; callers
        should override as appropriate.
    check:
        If True, raise CommandNotFoundError when the executable does
        not exist, instead of returning a CommandResult with a
        synthetic failure. Most callers should leave this False and
        inspect `.success` instead, since a missing package manager
        is an expected, recoverable condition (e.g. "no dnf on this
        box"), not an exceptional one.

    Returns
    -------
    CommandResult
        Always returned on any completion or handled failure
        (nonzero exit, timeout, missing executable). Only truly
        unexpected OS-level errors propagate as exceptions.
    """
    logger = get_logger()
    command = tuple(command)
    logger.debug("Executing command: %s", " ".join(command))

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
        )
        result = CommandResult(
            command=command,
            returncode=completed.returncode,
            stdout=completed.stdout.strip(),
            stderr=completed.stderr.strip(),
        )
        if not result.success:
            logger.debug(
                "Command failed (exit %d): %s | stderr: %s",
                result.returncode, " ".join(command), result.stderr,
            )
        return result

    except FileNotFoundError as exc:
        logger.debug("Command not found: %s", command[0])
        if check:
            raise CommandNotFoundError(
                f"Required executable not found: {command[0]}"
            ) from exc
        return CommandResult(
            command=command,
            returncode=127,
            stdout="",
            stderr=f"executable not found: {command[0]}",
        )

    except subprocess.TimeoutExpired as exc:
        logger.debug("Command timed out after %.1fs: %s", timeout, command)
        return CommandResult(
            command=command,
            returncode=124,
            stdout="",
            stderr=f"command timed out after {timeout:.0f}s: {exc}",
        )


# ---------------------------------------------------------------------------
# Privilege checks
# ---------------------------------------------------------------------------

def is_elevated() -> bool:
    """
    Return True if the current process has administrative/root
    privileges.

    On POSIX systems this checks the effective UID. On Windows this
    uses the ctypes shell32 IsUserAnAdmin() check. This function is
    the single source of truth for privilege detection -- no other
    module should reimplement this check.
    """
    if os.name == "nt":
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
        except Exception:
            # If the check itself fails for any reason, fail safe by
            # assuming NOT elevated, so the app surfaces a clear
            # "run as Administrator" message rather than proceeding
            # and failing confusingly later.
            return False
    else:
        try:
            return os.geteuid() == 0  # type: ignore[attr-defined]
        except AttributeError:
            return False


def elevation_instructions() -> str:
    """
    Return a user-friendly, platform-appropriate instruction string
    for how to re-run the program with sufficient privileges.
    """
    if os.name == "nt":
        return (
            "Portable SSH requires administrator privileges.\n"
            "Right-click your terminal (PowerShell or Command Prompt) "
            "and choose 'Run as administrator', then run this program again."
        )
    return (
        "Portable SSH requires root privileges.\n"
        "Re-run this program with sudo, e.g.:\n"
        "    sudo python3 portable_ssh.py"
    )


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def section_header(title: str, width: int = 58) -> str:
    """
    Build a bordered section header used by the display output, e.g.:

    ==========================================================
    Portable SSH
    ==========================================================
    """
    border = "=" * width
    return f"{border}\n{title}\n{border}"


def indent(text: str, spaces: int = 4) -> str:
    """Indent every line of a multi-line string by `spaces` spaces."""
    pad = " " * spaces
    return "\n".join(pad + line if line else line for line in text.splitlines())