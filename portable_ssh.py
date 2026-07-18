#!/usr/bin/env python3
"""
portable_ssh.py
=================

Portable SSH -- temporarily enable SSH on this machine for remote
access, and automatically restore it to its original state on exit.

This is the only module in the project allowed to know the overall
sequence of steps; it contains no OS-specific logic itself, deferring
entirely to platform.py, installer.py, services.py, network.py, and
cleanup.py for anything that varies by operating system.

See the project README for usage details.
"""

from __future__ import annotations

import argparse
import sys
import time
import traceback

import cleanup
import installer
import network
import services
from installer import InstallationError
from platform import (
    PlatformInfo,
    UnsupportedDistroError,
    UnsupportedPlatformError,
    detect_platform,
)
from services import ServiceError
from utils import (
    elevation_instructions,
    get_logger,
    indent,
    is_elevated,
    section_header,
    setup_logging,
)

EXIT_OK = 0
EXIT_ERROR = 1


def main() -> int:
    args = _parse_args()
    logger = setup_logging(verbose=args.verbose)

    print(section_header("Portable SSH"))
    print()

    try:
        return _run(args, logger)
    except KeyboardInterrupt:
        # Reached only if Ctrl+C happens before cleanup.managed_session
        # is entered (e.g. during the initial checks) -- once inside
        # managed_session, it handles KeyboardInterrupt itself and
        # re-raises after cleanup, which is caught by _run()'s own
        # try/except below. This outer handler exists purely as a
        # final safety net so an early Ctrl+C never prints a traceback.
        print("\nCancelled.")
        return EXIT_OK
    except Exception as exc:  # noqa: BLE001 - top-level catch-all is intentional
        _print_unexpected_error(exc, verbose=args.verbose)
        return EXIT_ERROR


def _run(args: argparse.Namespace, logger) -> int:
    """The full application flow, in order, per the project spec."""

    # ---- Privileges -----------------------------------------------------
    if not is_elevated():
        print(elevation_instructions())
        return EXIT_ERROR

    # ---- Platform detection ----------------------------------------------
    try:
        platform_info = _detect_platform_or_explain()
    except (UnsupportedPlatformError, UnsupportedDistroError) as exc:
        print(f"Cannot continue: {exc}")
        return EXIT_ERROR
    if platform_info is None:
        return EXIT_ERROR

    # ---- Install SSH server if missing ------------------------------------
    try:
        was_installed = installer.ensure_installed(platform_info)
    except InstallationError as exc:
        print(f"\nCould not install the SSH server.\n{indent(str(exc))}")
        return EXIT_ERROR

    if not services.service_exists(platform_info):
        print(
            "\nThe SSH server does not appear to be installed correctly "
            "on this system (the service could not be found even after "
            "checking installation). Try re-running this program, or "
            "check your system's SSH installation manually."
        )
        return EXIT_ERROR

    # ---- Start SSH service if not already running -------------------------
    was_running = services.is_running(platform_info)
    started_by_us = not was_running

    run_state = cleanup.RunState(
        platform_info=platform_info,
        was_installed=was_installed,
        was_running=was_running,
        started_by_us=started_by_us,
    )

    if started_by_us:
        try:
            services.start(platform_info)
        except ServiceError as exc:
            print(f"\nCould not start the SSH service.\n{indent(str(exc))}")
            return EXIT_ERROR

    # ---- Everything from here on is protected by cleanup -------------------
    # Once the service has been started (or confirmed already running),
    # any failure below -- network collection, display, or the wait loop --
    # must still trigger restoration. That's why this is the boundary of
    # the managed_session context manager, not just the final wait loop.
    with cleanup.managed_session(run_state):
        network_info = network.collect_network_info()
        _display(platform_info, network_info, run_state)
        _wait_for_interrupt()

    print("\nPortable SSH stopped.")
    return EXIT_OK


def _detect_platform_or_explain() -> PlatformInfo | None:
    return detect_platform()


def _wait_for_interrupt() -> None:
    print("\nPress Ctrl+C to stop Portable SSH...")
    while True:
        time.sleep(1)


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def _display(platform_info: PlatformInfo, net_info: "network.NetworkInfo", run_state: cleanup.RunState) -> None:
    """Render the connection-information screen shown in the spec."""
    status_label = "Running" if services.is_running(platform_info) else "Stopped"

    print(f"Operating System : {platform_info.os_display_name}")
    print(f"Hostname         : {net_info.hostname}")
    print(f"Username         : {net_info.username}")
    print(f"SSH Status       : {status_label}")
    print(f"SSH Port         : {net_info.ssh_port}")

    if not net_info.has_reachable_addresses:
        print()
        print(
            "No reachable network addresses were found. This machine may "
            "not be connected to a network, or all detected interfaces "
            "were virtual/inactive. SSH is running locally, but no remote "
            "connection information is available."
        )
        return

    print()
    print("Network Interfaces")
    print()
    grouped = network.group_by_interface(net_info.interfaces)
    for interface_name, addresses in grouped.items():
        print(interface_name)
        for addr in addresses:
            print(addr.ip_address)
        print()

    print("Reachable Addresses")
    print()
    for addr in net_info.interfaces:
        print(network.ssh_command_for(net_info.username, addr.ip_address, net_info.ssh_port))


# ---------------------------------------------------------------------------
# Error presentation
# ---------------------------------------------------------------------------

def _print_unexpected_error(exc: Exception, verbose: bool) -> None:
    logger = get_logger()
    if verbose:
        traceback.print_exc()
    else:
        print(
            "\nAn unexpected error occurred and Portable SSH could not "
            "continue. Run with --verbose for full diagnostic details."
        )
        logger.debug("Unexpected error: %s", exc)


# ---------------------------------------------------------------------------
# CLI arguments
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="portable_ssh",
        description=(
            "Temporarily enable SSH on this machine for remote access, "
            "restoring the original state on exit."
        ),
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose/debug logging for diagnostics.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    sys.exit(main())