"""
installer.py
=============

Responsible for exactly one thing: knowing whether the SSH server is
installed, and installing it if (and only if) it is missing.

This module contains NO operating-system-specific logic itself -- it
consumes the command templates and indicator strings already resolved
onto a PlatformInfo object by platform.py, and executes them via
utils.run_command(). Adding a new distro never requires touching
this file.
"""

from __future__ import annotations

from platform import PackageManager, PlatformInfo
from utils import CommandResult, get_logger, run_command

# Package installation can be considerably slower than a service
# start/stop/status call (network fetch + dependency resolution),
# so it gets its own, more generous timeout.
INSTALL_TIMEOUT_SECONDS = 300.0
CHECK_TIMEOUT_SECONDS = 15.0
APT_UPDATE_TIMEOUT_SECONDS = 180.0


class InstallationError(RuntimeError):
    """
    Raised when the SSH server could not be installed.

    Carries a user-friendly message; the orchestrator (portable_ssh.py)
    is responsible for catching this and displaying str(exc) without a
    traceback in normal (non-verbose) mode.
    """


def is_installed(platform_info: PlatformInfo) -> bool:
    """
    Return True if the SSH server package/capability is already
    present on this machine.

    On macOS, Remote Login is a built-in system capability rather
    than an installable package (profile.supports_install is False),
    so this always returns True -- there is nothing to "install".

    For everyone else, this runs the platform's installed_check_cmd
    and looks for profile.installed_indicator in the combined
    stdout/stderr. We check the indicator text rather than relying
    solely on the process exit code, because some package managers
    (notably dpkg) can return exit code 0 for a package that was
    removed-but-not-purged, where only the status line distinguishes
    a truly installed package from a leftover config-only entry.
    """
    profile = platform_info.profile

    if not profile.supports_install:
        return True

    result = run_command(profile.installed_check_cmd, timeout=CHECK_TIMEOUT_SECONDS)
    combined_output = f"{result.stdout}\n{result.stderr}"
    return profile.installed_indicator in combined_output


def install(platform_info: PlatformInfo) -> None:
    """
    Install the SSH server on this machine.

    Should only be called after is_installed() has already returned
    False -- this function does not re-check first, to avoid a
    redundant subprocess call in the common orchestrator flow (see
    ensure_installed() below, which does both in the correct order).

    Raises
    ------
    InstallationError
        If installation fails, with a user-friendly message
        describing what went wrong and, where possible, a suggested
        next step. Never raises a raw subprocess/OS exception --
        portable_ssh.py can display str(exc) directly to the user.
    """
    logger = get_logger()
    profile = platform_info.profile

    if not profile.supports_install:
        # Nothing to install (e.g. macOS Remote Login). Should not
        # normally be reached since is_installed() already returns
        # True for these platforms, but guarded here defensively.
        return

    if not profile.install_cmd:
        raise InstallationError(
            "No installation command is defined for this platform. "
            "This is a Portable SSH configuration gap, not a problem "
            "with your machine -- please report it."
        )

    logger.info("SSH server not found. Installing (%s)...", profile.package_manager.value)
    result = run_command(profile.install_cmd, timeout=INSTALL_TIMEOUT_SECONDS)

    if not result.success and profile.package_manager is PackageManager.APT:
        # apt is the one package manager where a stale local package
        # index is a common, easily-fixed cause of install failure.
        # Refresh it once and retry, rather than failing immediately.
        logger.debug("apt-get install failed; refreshing package index and retrying once.")
        update_result = run_command(["apt-get", "update"], timeout=APT_UPDATE_TIMEOUT_SECONDS)
        if update_result.success:
            result = run_command(profile.install_cmd, timeout=INSTALL_TIMEOUT_SECONDS)

    if not result.success:
        raise InstallationError(_installation_failure_message(platform_info, result))

    # Verify the install actually took effect rather than trusting
    # exit code 0 blindly (some package managers exit 0 on a no-op
    # that silently didn't do what we expected).
    if not is_installed(platform_info):
        raise InstallationError(
            "The installation command completed without an error, but "
            "the SSH server still could not be detected afterward. "
            "Your package manager may use a different package name on "
            "this system, or the install may require a reboot."
        )

    logger.info("SSH server installed successfully.")


def ensure_installed(platform_info: PlatformInfo) -> bool:
    """
    Ensure the SSH server is installed, installing it only if needed.

    This is the single function the orchestrator (portable_ssh.py)
    should call. It returns whether the server was ALREADY installed
    before this call, which the orchestrator records into RunState
    (was_installed) for later reference/logging -- installation is
    never undone during cleanup, per the spec, so this value is
    informational rather than something cleanup.py acts on.

    Returns
    -------
    bool
        True if SSH was already installed. False if this call just
        installed it.

    Raises
    ------
    InstallationError
        Propagated from install() if installation was needed and
        failed.
    """
    already_installed = is_installed(platform_info)
    if not already_installed:
        install(platform_info)
    return already_installed


def _installation_failure_message(platform_info: PlatformInfo, result: CommandResult) -> str:
    """Build a friendly, actionable error message for a failed install."""
    profile = platform_info.profile
    stderr_snippet = result.stderr.strip() or "(no error output captured)"

    hint = _permission_hint(platform_info)

    return (
        f"Failed to install the SSH server using "
        f"'{' '.join(profile.install_cmd or [])}'.\n"
        f"Details: {stderr_snippet}\n"
        f"{hint}"
    )


def _permission_hint(platform_info: PlatformInfo) -> str:
    """Return a platform-appropriate hint about the most common cause
    of install failure: insufficient privileges."""
    if platform_info.profile.package_manager is PackageManager.WINDOWS_CAPABILITY:
        return "Make sure you are running this program as Administrator."
    if platform_info.profile.package_manager is PackageManager.MACOS_NATIVE:
        return "Make sure you are running this program with sudo."
    return "Make sure you are running this program with sudo and have network access to your package repositories."