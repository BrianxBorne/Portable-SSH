"""
network.py
===========

Collects the information Portable SSH needs to tell the user how to
connect: hostname, username, SSH port, and every IPv4 address on this
machine that another computer on the same network could realistically
reach -- filtering out loopback, link-local, and virtual adapters
(Docker, VirtualBox, VMware, Hyper-V, WSL) while deliberately KEEPING
legitimate VPN adapters, since a VPN address is often exactly the
address a remote user wants to connect through.

Design note on scope
---------------------
This module contains a small amount of direct OS branching (only for
default-gateway detection, three lines total). That is a deliberate,
narrow exception to the "no OS logic outside platform.py" rule: the
platform.py abstraction exists specifically for SSH install/service
commands that installer.py and services.py act on, shared across the
project. Gateway detection is a self-contained, read-only piece of
information used only by this module and displayed as-is -- it does
not belong to the install/service command model, and duplicating
platform.py's machinery for one three-way if/elif would add more
complexity than it removes. If a second module ever needed OS-aware
networking logic, this would be worth promoting into platform.py.
"""

from __future__ import annotations

import getpass
import os
import re
import socket
import sys
from dataclasses import dataclass, field
from ipaddress import ip_address
from typing import Optional

from utils import get_logger, run_command

DEFAULT_SSH_PORT = 22
GATEWAY_TIMEOUT_SECONDS = 5.0
PRIMARY_IP_PROBE_TIMEOUT_SECONDS = 1.0

# Interface name patterns that indicate a virtual, container, or
# hypervisor-only adapter that no other machine on the LAN could ever
# reach. VPN adapters (utun, tun, tap, wg, ppp) are intentionally NOT
# included here -- the spec's own example output explicitly expects a
# VPN interface to appear among the reachable addresses.
_VIRTUAL_INTERFACE_PATTERNS: tuple[str, ...] = (
    r"^lo$",
    r"loopback",
    r"docker",
    r"^veth",
    r"^br-",
    r"^virbr",
    r"vbox",
    r"vmnet",
    r"vmware",
    r"hyper-v",
    r"vethernet.*wsl",
    r"^wsl",
    r"awdl",   # macOS Apple Wireless Direct Link (AirDrop), not LAN-reachable
    r"llw",    # macOS low-latency WLAN companion interface
)
_VIRTUAL_INTERFACE_RE = re.compile("|".join(_VIRTUAL_INTERFACE_PATTERNS), re.IGNORECASE)


@dataclass(frozen=True)
class InterfaceAddress:
    """One reachable IPv4 address on one network interface."""
    interface_name: str
    ip_address: str
    subnet_mask: Optional[str] = None


@dataclass(frozen=True)
class NetworkInfo:
    """Everything Portable SSH's display step needs to render."""
    hostname: str
    username: str
    ssh_port: int
    primary_ip: Optional[str]
    default_gateway: Optional[str]
    interfaces: list[InterfaceAddress] = field(default_factory=list)

    @property
    def has_reachable_addresses(self) -> bool:
        return len(self.interfaces) > 0


def collect_network_info(ssh_port: int = DEFAULT_SSH_PORT) -> NetworkInfo:
    """
    Gather hostname, username, reachable IPv4 addresses, and default
    gateway for display to the user.

    This never raises for "no network" conditions -- an empty
    interfaces list and a None gateway/primary_ip are valid, expected
    results that the orchestrator/display layer handles gracefully
    (per the spec's "no active network adapters" / "no reachable
    IPv4 addresses" error-handling requirements). Only truly
    unexpected OS errors would propagate.
    """
    logger = get_logger()

    hostname = _detect_hostname()
    username = getpass.getuser()
    interfaces = _collect_reachable_interfaces()
    primary_ip = _detect_primary_ip() or (interfaces[0].ip_address if interfaces else None)
    gateway = _detect_default_gateway()

    logger.debug(
        "Network info: hostname=%s username=%s interfaces=%d primary_ip=%s gateway=%s",
        hostname, username, len(interfaces), primary_ip, gateway,
    )

    return NetworkInfo(
        hostname=hostname,
        username=username,
        ssh_port=ssh_port,
        primary_ip=primary_ip,
        default_gateway=gateway,
        interfaces=interfaces,
    )


def ssh_command_for(username: str, ip: str, port: int) -> str:
    """
    Build a single ready-to-copy SSH command string for one address.

    Non-standard ports use the `-p` flag; port 22 is omitted since
    it's the SSH default and most users won't expect to see it.
    """
    if port == DEFAULT_SSH_PORT:
        return f"ssh {username}@{ip}"
    return f"ssh -p {port} {username}@{ip}"


def group_by_interface(interfaces: list[InterfaceAddress]) -> dict[str, list[InterfaceAddress]]:
    """
    Group a flat list of InterfaceAddress entries by interface name,
    preserving first-seen order -- used by the display step to render
    the "organize by interface" layout from the spec.
    """
    grouped: dict[str, list[InterfaceAddress]] = {}
    for entry in interfaces:
        grouped.setdefault(entry.interface_name, []).append(entry)
    return grouped


# ---------------------------------------------------------------------------
# Internal collection helpers
# ---------------------------------------------------------------------------

def _detect_hostname() -> str:
    try:
        return socket.gethostname()
    except OSError:
        return "unknown-host"


def _is_virtual_interface(name: str) -> bool:
    return bool(_VIRTUAL_INTERFACE_RE.search(name))


def _is_reachable_ipv4(ip: str) -> bool:
    """
    Return True for an IPv4 address another machine on the LAN could
    plausibly reach: not loopback, not link-local (169.254.x.x /
    APIPA), and not otherwise reserved/unspecified.
    """
    try:
        parsed = ip_address(ip)
    except ValueError:
        return False

    if parsed.version != 4:
        return False
    if parsed.is_loopback or parsed.is_link_local or parsed.is_unspecified:
        return False
    return True


def _collect_reachable_interfaces() -> list[InterfaceAddress]:
    """
    Enumerate every IPv4 address on every UP, non-virtual interface,
    using psutil for cross-platform interface enumeration (the
    stdlib socket module cannot reliably list all adapters across
    Windows/Linux/macOS -- see requirements.txt for that rationale).
    """
    logger = get_logger()
    try:
        import psutil
    except ImportError:
        logger.warning(
            "psutil is not installed; network interface detection is "
            "unavailable. Install dependencies from requirements.txt."
        )
        return []

    results: list[InterfaceAddress] = []

    try:
        all_addrs = psutil.net_if_addrs()
        all_stats = psutil.net_if_stats()
    except Exception as exc:  # pragma: no cover - defensive, OS-dependent
        logger.debug("Failed to enumerate network interfaces: %s", exc)
        return []

    for interface_name, addr_list in all_addrs.items():
        if _is_virtual_interface(interface_name):
            continue

        stats = all_stats.get(interface_name)
        if stats is not None and not stats.isup:
            continue  # inactive/disconnected interface

        for addr in addr_list:
            if addr.family != socket.AF_INET:
                continue
            if not _is_reachable_ipv4(addr.address):
                continue

            results.append(
                InterfaceAddress(
                    interface_name=interface_name,
                    ip_address=addr.address,
                    subnet_mask=getattr(addr, "netmask", None),
                )
            )

    return results


def _detect_primary_ip() -> Optional[str]:
    """
    Determine the IPv4 address this machine would use to reach the
    outside world, using the standard "UDP connect" trick: connecting
    a UDP socket doesn't send any packets, it just asks the OS routing
    table which local address it would use -- so this works offline
    on a LAN and doesn't require actual Internet connectivity.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(PRIMARY_IP_PROBE_TIMEOUT_SECONDS)
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return None


def _detect_default_gateway() -> Optional[str]:
    """
    Best-effort default gateway detection.

    This is the one place in network.py with direct OS branching --
    see the module docstring for why this narrow exception is
    reasonable here. Returns None (not an error) if the gateway can't
    be determined; the display layer treats that as "not available".
    """
    if os.name == "nt":
        return _detect_gateway_windows()
    if sys.platform == "darwin":
        return _detect_gateway_macos()
    if sys.platform.startswith("linux"):
        return _detect_gateway_linux()
    return None


def _detect_gateway_linux() -> Optional[str]:
    result = run_command(["ip", "route", "show", "default"], timeout=GATEWAY_TIMEOUT_SECONDS)
    if not result.success:
        return None
    match = re.search(r"default via (\S+)", result.stdout)
    return match.group(1) if match else None


def _detect_gateway_macos() -> Optional[str]:
    result = run_command(["route", "-n", "get", "default"], timeout=GATEWAY_TIMEOUT_SECONDS)
    if not result.success:
        return None
    match = re.search(r"gateway:\s*(\S+)", result.stdout)
    return match.group(1) if match else None


def _detect_gateway_windows() -> Optional[str]:
    result = run_command(
        [
            "powershell", "-NoProfile", "-Command",
            "(Get-NetRoute -DestinationPrefix '0.0.0.0/0' | "
            "Sort-Object -Property RouteMetric | "
            "Select-Object -First 1 -ExpandProperty NextHop)",
        ],
        timeout=GATEWAY_TIMEOUT_SECONDS,
    )
    if not result.success:
        return None
    candidate = result.stdout.strip()
    if not candidate:
        return None
    try:
        ip_address(candidate)
    except ValueError:
        return None
    return candidate