"""
platform.py
============

The single source of truth for all operating-system-specific
information in Portable SSH.

No other module in this project may branch on operating system or
Linux distribution. Every other module (installer.py, services.py)
receives a fully-populated PlatformInfo object and simply executes
the command templates it contains -- it never asks "am I on Ubuntu?"

IMPORTANT NAMING NOTE
---------------------
This file is intentionally named platform.py per the project spec.
Because the script's own directory is placed first on sys.path, any
`import platform` anywhere in this project would resolve to THIS
file instead of the Python standard library's `platform` module --
including a self-import loop if this file tried to `import platform`
itself. To avoid that entirely, this module (and the rest of the
project) never imports the stdlib `platform` module. OS/version
detection instead uses `os`, `sys`, `sys.getwindowsversion()`, and
direct parsing of `/etc/os-release`, which provide everything we
need without the collision.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import Optional

from utils import get_logger, run_command

OS_RELEASE_PATH = Path("/etc/os-release")


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class UnsupportedPlatformError(RuntimeError):
    """Raised when the host OS is not one Portable SSH knows how to handle."""


class UnsupportedDistroError(RuntimeError):
    """Raised when running on Linux but the distro cannot be identified
    or matched to any known family, even via ID_LIKE fallback."""


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class OSFamily(Enum):
    WINDOWS = auto()
    LINUX = auto()
    MACOS = auto()


class PackageManager(Enum):
    APT = "apt"
    DNF = "dnf"
    YUM = "yum"
    PACMAN = "pacman"
    ZYPPER = "zypper"
    WINDOWS_CAPABILITY = "windows-capability"
    MACOS_NATIVE = "macos-native"


# ---------------------------------------------------------------------------
# Declarative profile: everything OS/distro-specific lives in these
# dataclasses. To support a new distro, add one DistroProfile entry
# below -- no other file needs to change.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DistroProfile:
    """Static, declarative command/package data for one OS or distro."""
    package_manager: PackageManager
    ssh_package_name: Optional[str]
    ssh_service_name: str
    supports_install: bool

    install_cmd: Optional[list[str]]
    installed_check_cmd: list[str]
    installed_indicator: str  # substring expected in stdout when installed

    status_cmd: list[str]
    running_indicator: str  # substring expected in stdout when running

    start_cmd: list[str]
    stop_cmd: list[str]


@dataclass(frozen=True)
class PlatformInfo:
    """
    Fully-resolved platform information for the current machine.

    This is the object every other module receives. It carries no
    behavior, only data -- installer.py and services.py just execute
    the command lists via utils.run_command() and compare output
    against the indicator strings.
    """
    os_family: OSFamily
    os_display_name: str          # e.g. "Windows 11", "Ubuntu 22.04", "macOS 14.5"
    distro_id: Optional[str]      # e.g. "ubuntu", "rocky" -- None on Windows/macOS
    profile: DistroProfile


# ---------------------------------------------------------------------------
# Distro profile table
# ---------------------------------------------------------------------------

_SYSTEMCTL_DEBIAN = DistroProfile(
    package_manager=PackageManager.APT,
    ssh_package_name="openssh-server",
    ssh_service_name="ssh",
    supports_install=True,
    install_cmd=["apt-get", "install", "-y", "openssh-server"],
    installed_check_cmd=["dpkg", "-s", "openssh-server"],
    installed_indicator="install ok installed",
    status_cmd=["systemctl", "is-active", "ssh"],
    running_indicator="active",
    start_cmd=["systemctl", "start", "ssh"],
    stop_cmd=["systemctl", "stop", "ssh"],
)

_SYSTEMCTL_DNF = DistroProfile(
    package_manager=PackageManager.DNF,
    ssh_package_name="openssh-server",
    ssh_service_name="sshd",
    supports_install=True,
    install_cmd=["dnf", "install", "-y", "openssh-server"],
    installed_check_cmd=["rpm", "-q", "openssh-server"],
    installed_indicator="openssh-server",
    status_cmd=["systemctl", "is-active", "sshd"],
    running_indicator="active",
    start_cmd=["systemctl", "start", "sshd"],
    stop_cmd=["systemctl", "stop", "sshd"],
)

_SYSTEMCTL_YUM = DistroProfile(
    package_manager=PackageManager.YUM,
    ssh_package_name="openssh-server",
    ssh_service_name="sshd",
    supports_install=True,
    install_cmd=["yum", "install", "-y", "openssh-server"],
    installed_check_cmd=["rpm", "-q", "openssh-server"],
    installed_indicator="openssh-server",
    status_cmd=["systemctl", "is-active", "sshd"],
    running_indicator="active",
    start_cmd=["systemctl", "start", "sshd"],
    stop_cmd=["systemctl", "stop", "sshd"],
)

_SYSTEMCTL_ARCH = DistroProfile(
    package_manager=PackageManager.PACMAN,
    ssh_package_name="openssh",
    ssh_service_name="sshd",
    supports_install=True,
    install_cmd=["pacman", "-S", "--noconfirm", "openssh"],
    installed_check_cmd=["pacman", "-Q", "openssh"],
    installed_indicator="openssh",
    status_cmd=["systemctl", "is-active", "sshd"],
    running_indicator="active",
    start_cmd=["systemctl", "start", "sshd"],
    stop_cmd=["systemctl", "stop", "sshd"],
)

_SYSTEMCTL_SUSE = DistroProfile(
    package_manager=PackageManager.ZYPPER,
    ssh_package_name="openssh",
    ssh_service_name="sshd",
    supports_install=True,
    install_cmd=["zypper", "--non-interactive", "install", "openssh"],
    installed_check_cmd=["rpm", "-q", "openssh"],
    installed_indicator="openssh",
    status_cmd=["systemctl", "is-active", "sshd"],
    running_indicator="active",
    start_cmd=["systemctl", "start", "sshd"],
    stop_cmd=["systemctl", "stop", "sshd"],
)

_WINDOWS_PROFILE = DistroProfile(
    package_manager=PackageManager.WINDOWS_CAPABILITY,
    ssh_package_name="OpenSSH.Server~~~~0.0.1.0",
    ssh_service_name="sshd",
    supports_install=True,
    install_cmd=[
        "powershell", "-NoProfile", "-Command",
        "Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0",
    ],
    installed_check_cmd=[
        "powershell", "-NoProfile", "-Command",
        "(Get-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0).State",
    ],
    installed_indicator="Installed",
    status_cmd=[
        "powershell", "-NoProfile", "-Command",
        "(Get-Service -Name sshd).Status",
    ],
    running_indicator="Running",
    start_cmd=["powershell", "-NoProfile", "-Command", "Start-Service sshd"],
    stop_cmd=["powershell", "-NoProfile", "-Command", "Stop-Service sshd"],
)

_MACOS_PROFILE = DistroProfile(
    package_manager=PackageManager.MACOS_NATIVE,
    ssh_package_name=None,
    ssh_service_name="com.openssh.sshd",
    # Remote Login is a built-in OS capability on macOS, not an
    # installable package -- there is nothing to install/uninstall.
    supports_install=False,
    install_cmd=None,
    installed_check_cmd=["systemsetup", "-getremotelogin"],
    installed_indicator="Remote Login",  # always present; install step is skipped anyway
    status_cmd=["systemsetup", "-getremotelogin"],
    running_indicator="On",
    start_cmd=["systemsetup", "-setremotelogin", "on"],
    stop_cmd=["systemsetup", "-setremotelogin", "off"],
)

# Maps an /etc/os-release "ID" field directly to a profile.
_DISTRO_PROFILE_TABLE: dict[str, DistroProfile] = {
    "ubuntu": _SYSTEMCTL_DEBIAN,
    "debian": _SYSTEMCTL_DEBIAN,
    "fedora": _SYSTEMCTL_DNF,
    "rocky": _SYSTEMCTL_DNF,
    "almalinux": _SYSTEMCTL_DNF,
    "centos": _SYSTEMCTL_YUM,
    "arch": _SYSTEMCTL_ARCH,
    "opensuse-leap": _SYSTEMCTL_SUSE,
    "opensuse-tumbleweed": _SYSTEMCTL_SUSE,
    "sles": _SYSTEMCTL_SUSE,
}

# Fallback: if the exact ID isn't in the table above, match a token
# from /etc/os-release's ID_LIKE field against this family map. This
# is what lets an *unlisted* distro (e.g. Linux Mint, Pop!_OS, Nobara)
# still work out of the box.
_FAMILY_FALLBACK: dict[str, DistroProfile] = {
    "debian": _SYSTEMCTL_DEBIAN,
    "ubuntu": _SYSTEMCTL_DEBIAN,
    "fedora": _SYSTEMCTL_DNF,
    "rhel": _SYSTEMCTL_DNF,
    "arch": _SYSTEMCTL_ARCH,
    "suse": _SYSTEMCTL_SUSE,
}


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def detect_platform() -> PlatformInfo:
    """
    Detect the current operating system (and Linux distro, if
    applicable) and return a fully-resolved PlatformInfo.

    Raises
    ------
    UnsupportedPlatformError
        If the OS itself is not Windows, Linux, or macOS.
    UnsupportedDistroError
        If running on Linux but the distro cannot be identified or
        matched to any known family.
    """
    logger = get_logger()

    if os.name == "nt":
        display_name = _detect_windows_version()
        logger.debug("Detected platform: %s", display_name)
        return PlatformInfo(
            os_family=OSFamily.WINDOWS,
            os_display_name=display_name,
            distro_id=None,
            profile=_WINDOWS_PROFILE,
        )

    if sys.platform == "darwin":
        display_name = _detect_macos_version()
        logger.debug("Detected platform: %s", display_name)
        return PlatformInfo(
            os_family=OSFamily.MACOS,
            os_display_name=display_name,
            distro_id=None,
            profile=_MACOS_PROFILE,
        )

    if sys.platform.startswith("linux"):
        distro_id, pretty_name = _read_os_release()
        profile = _resolve_linux_profile(distro_id)
        logger.debug("Detected platform: %s (id=%s)", pretty_name, distro_id)
        return PlatformInfo(
            os_family=OSFamily.LINUX,
            os_display_name=pretty_name,
            distro_id=distro_id,
            profile=profile,
        )

    raise UnsupportedPlatformError(
        f"Unsupported operating system: sys.platform={sys.platform!r}. "
        "Portable SSH supports Windows, Linux, and macOS."
    )


def _resolve_linux_profile(distro_id: Optional[str]) -> DistroProfile:
    """Resolve a DistroProfile for a Linux distro ID, falling back to
    the ID_LIKE family map, and raising if nothing matches."""
    if distro_id and distro_id in _DISTRO_PROFILE_TABLE:
        return _DISTRO_PROFILE_TABLE[distro_id]

    id_like_tokens = _read_os_release_id_like()
    for token in id_like_tokens:
        if token in _FAMILY_FALLBACK:
            return _FAMILY_FALLBACK[token]

    raise UnsupportedDistroError(
        f"Could not identify Linux distribution (ID={distro_id!r}, "
        f"ID_LIKE={id_like_tokens!r}). Portable SSH does not yet know "
        "how to manage SSH on this distribution. See platform.py to "
        "add a new DistroProfile entry."
    )


def _parse_os_release() -> dict[str, str]:
    """Parse /etc/os-release into a plain key/value dict."""
    if not OS_RELEASE_PATH.exists():
        return {}

    values: dict[str, str] = {}
    for line in OS_RELEASE_PATH.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, raw_value = line.partition("=")
        values[key.strip()] = raw_value.strip().strip('"').strip("'")
    return values


def _read_os_release() -> tuple[Optional[str], str]:
    """Return (ID, PRETTY_NAME) from /etc/os-release, with sensible
    fallbacks if the file is missing or incomplete."""
    values = _parse_os_release()
    distro_id = values.get("ID")
    pretty_name = values.get("PRETTY_NAME") or distro_id or "Unknown Linux"
    return distro_id, pretty_name


def _read_os_release_id_like() -> list[str]:
    """Return the whitespace-separated tokens of ID_LIKE, e.g.
    ["rhel", "fedora"] for a RHEL-derived distro."""
    values = _parse_os_release()
    id_like = values.get("ID_LIKE", "")
    return id_like.split() if id_like else []


def _detect_windows_version() -> str:
    """
    Return a human-readable Windows version string.

    Windows 11 reports the same major/minor version as Windows 10 in
    every stdlib API; the only reliable distinguishing signal is the
    build number (Windows 11 starts at build 22000).
    """
    try:
        build = sys.getwindowsversion().build  # type: ignore[attr-defined]
    except AttributeError:
        return "Windows"

    if build >= 22000:
        return "Windows 11"
    return "Windows 10"


def _detect_macos_version() -> str:
    """
    Return a human-readable macOS version string, e.g. "macOS 14.5".

    Uses `sw_vers` (a native macOS tool) via run_command rather than
    the stdlib `platform` module, per the naming note at the top of
    this file. Falls back to a generic label if the command fails.
    """
    result = run_command(["sw_vers", "-productVersion"], timeout=5.0)
    if result.success and result.stdout:
        return f"macOS {result.stdout}"
    return "macOS"