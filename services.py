"""
services.py
============

Clean abstraction over the *runtime* state of the SSH service:
whether the service unit/capability is registered at all, whether
it's currently running, and starting/stopping it.

This module contains NO operating-system-specific logic -- like
installer.py, it only ever executes the command templates already
resolved onto a PlatformInfo object by platform.py.

Division of responsibility vs. installer.py
--------------------------------------------
installer.py answers "is the SSH *package* present on disk?"
services.py answers "is the SSH *service* registered and running?"

These are deliberately kept separate rather than merged into one
is_installed() here (even though the original spec sketch lists
is_installed() as an example services.py function), because they are
genuinely different questions with a real edge case between them: on
some distros, installing the openssh-server package does not
immediately register a systemd unit, so a package can be "installed"
per dpkg/rpm while the service unit is still momentarily "not found"
per systemctl. Keeping these as two distinct checks (installer.py's
is_installed, services.py's service_exists) lets portable_ssh.py
detect and report that specific edge case clearly, instead of masking
it behind a single conflated boolean.
"""

from __future__ import annotations

from enum import Enum, auto

from platform import OSFamily, PlatformInfo
from utils import get_logger, run_command

STATUS_TIMEOUT_SECONDS = 10.0
START_STOP_TIMEOUT_SECONDS = 20.0

# Substrings that indicate "the service unit itself does not exist"
# (as opposed to existing but being stopped). These come from the
# native tools' own error text, so they're checked here rather than
# invented per-platform logic elsewhere.
_NOT_FOUND_MARKERS: tuple[str, ...] = (
    "not found",
    "could not be found",
    "no such service",
    "cannot find any service",
    "unit sshd.service",
    "unit ssh.service",
)


class ServiceError(RuntimeError):
    """
    Raised when a service start/stop operation fails.

    Carries a user-friendly message; portable_ssh.py catches this and
    displays str(exc) without a traceback in normal (non-verbose) mode.
    """


class ServiceState(Enum):
    """The overall resolved state of the SSH service."""
    RUNNING = auto()
    STOPPED = auto()
    NOT_FOUND = auto()
    UNKNOWN = auto()


def service_exists(platform_info: PlatformInfo) -> bool:
    """
    Return True if the SSH service/capability is registered on this
    machine (regardless of whether it's currently running).

    On macOS, Remote Login is always a valid system setting to query
    (it's never "not found" the way a systemd unit can be), so this
    always returns True there.
    """
    if platform_info.os_family is OSFamily.MACOS:
        return True

    result = run_command(platform_info.profile.status_cmd, timeout=STATUS_TIMEOUT_SECONDS)
    combined_output = f"{result.stdout}\n{result.stderr}".lower()
    return not any(marker in combined_output for marker in _NOT_FOUND_MARKERS)


def is_running(platform_info: PlatformInfo) -> bool:
    """
    Return True if the SSH service is currently running.

    Returns False (rather than raising) if the service does not
    exist at all -- "not found" and "not running" both mean "not
    running" from the caller's point of view; use service_exists()
    separately if the distinction matters.
    """
    result = run_command(platform_info.profile.status_cmd, timeout=STATUS_TIMEOUT_SECONDS)
    return platform_info.profile.running_indicator in result.stdout


def status(platform_info: PlatformInfo) -> ServiceState:
    """
    Return the fully-resolved ServiceState, combining service_exists()
    and is_running() into a single, easy-to-branch-on result.
    """
    if not service_exists(platform_info):
        return ServiceState.NOT_FOUND
    return ServiceState.RUNNING if is_running(platform_info) else ServiceState.STOPPED


def start(platform_info: PlatformInfo) -> None:
    """
    Start the SSH service.

    Raises
    ------
    ServiceError
        If the service does not exist, or the start command fails,
        or the service still isn't running after the start command
        reports success (defensive verification -- some service
        managers can report success on a no-op).
    """
    logger = get_logger()

    if not service_exists(platform_info):
        raise ServiceError(
            "The SSH service is not registered on this system, even though "
            "installation reported success. This can happen immediately "
            "after a fresh install on some distributions -- try again in a "
            "few seconds, or check your package manager's install log."
        )

    if is_running(platform_info):
        logger.debug("Service already running; start() is a no-op.")
        return

    logger.info("Starting SSH service...")
    result = run_command(platform_info.profile.start_cmd, timeout=START_STOP_TIMEOUT_SECONDS)

    if not result.success or not is_running(platform_info):
        raise ServiceError(_start_failure_message(platform_info, result.stderr))

    logger.info("SSH service started.")


def stop(platform_info: PlatformInfo) -> None:
    """
    Stop the SSH service.

    Raises
    ------
    ServiceError
        If the stop command fails, or the service is still reported
        as running afterward.

    Note: this is called from cleanup.py ONLY when this application
    is the one that started the service (see RunState.started_by_us).
    Stopping a service the user already had running before we started
    would violate the "leave the machine exactly as it was" guarantee.
    """
    logger = get_logger()

    if not is_running(platform_info):
        logger.debug("Service already stopped; stop() is a no-op.")
        return

    logger.info("Stopping SSH service...")
    result = run_command(platform_info.profile.stop_cmd, timeout=START_STOP_TIMEOUT_SECONDS)

    if not result.success or is_running(platform_info):
        raise ServiceError(_stop_failure_message(platform_info, result.stderr))

    logger.info("SSH service stopped.")


def _start_failure_message(platform_info: PlatformInfo, stderr: str) -> str:
    detail = stderr.strip() or "(no error output captured)"
    hint = (
        "Make sure you are running this program as Administrator."
        if platform_info.os_family is OSFamily.WINDOWS
        else "Make sure you are running this program with sudo."
    )
    return f"Failed to start the SSH service.\nDetails: {detail}\n{hint}"


def _stop_failure_message(platform_info: PlatformInfo, stderr: str) -> str:
    detail = stderr.strip() or "(no error output captured)"
    return (
        f"Failed to stop the SSH service during cleanup.\nDetails: {detail}\n"
        "You may need to stop it manually to restore your machine to its "
        "original state."
    )