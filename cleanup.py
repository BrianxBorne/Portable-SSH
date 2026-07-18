"""
cleanup.py
===========

The safety net for Portable SSH's core promise: "leave the machine
exactly as it was before the program started."

This module owns two things:

1. RunState -- a small, immutable-in-spirit record of what this run
   actually did (installed something? started the service?), created
   once by portable_ssh.py right after detection/install/start, and
   never re-derived by querying the OS again during cleanup. Cleanup
   acts ONLY on what was recorded, not on a fresh guess -- re-checking
   service state inside a signal handler is a classic source of race
   conditions and is deliberately avoided here.

2. managed_session() -- a context manager that guarantees restore()
   runs on every exit path: normal completion, Ctrl+C (KeyboardInterrupt),
   or an unhandled exception. portable_ssh.py's entire "wait for
   interrupt" phase runs inside this context manager.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

import services
from platform import PlatformInfo
from services import ServiceError
from utils import get_logger


@dataclass
class RunState:
    """
    Records what Portable SSH found and did on this machine, so
    cleanup can restore exactly that -- nothing more, nothing less.

    was_installed:
        True if the SSH server was already installed before this run.
        Installation is intentionally never undone (per spec, we only
        ever add software, never remove it) -- this field exists for
        logging/display, not as a cleanup instruction.
    was_running:
        True if the SSH service was already running before this run
        touched anything.
    started_by_us:
        True if this run started the service itself. This is the
        ONLY field cleanup.restore() actually acts on.
    """
    platform_info: PlatformInfo
    was_installed: bool = False
    was_running: bool = False
    started_by_us: bool = False


@contextmanager
def managed_session(run_state: RunState) -> Iterator[RunState]:
    """
    Wrap the "display info and wait" phase of the program.

    Usage:
        with cleanup.managed_session(run_state):
            display(...)
            wait_for_interrupt()

    Guarantees restore(run_state) runs exactly once when the `with`
    block exits, regardless of *how* it exits (falls through
    normally, KeyboardInterrupt, or any other exception) -- a plain
    try/finally is sufficient for this and is simpler and more
    predictable than a signal.signal() handler, which would need to
    be careful about re-entrancy and about what is/isn't safe to call
    from inside a signal handler.

    KeyboardInterrupt and other exceptions are re-raised after
    cleanup runs, so portable_ssh.py's top-level main() decides the
    final user-facing message and exit code -- this function's only
    job is guaranteeing the restoration happens first.
    """
    logger = get_logger()
    try:
        yield run_state
    except KeyboardInterrupt:
        print()  # move past the "^C" the terminal echoes
        logger.info("Interrupted. Restoring SSH to its original state...")
        raise
    except Exception:
        logger.debug("Exiting due to an unexpected error; restoring state before exit.")
        raise
    finally:
        restore(run_state)


def restore(run_state: RunState) -> None:
    """
    Restore the SSH service to its pre-run state.

    Only stops the service if THIS run started it. If SSH was already
    running before Portable SSH touched anything, it is deliberately
    left running -- stopping it would violate the "leave the machine
    exactly as it was" guarantee just as much as leaving something we
    started would.

    A failure to stop the service is logged as a warning with a
    manual-remediation hint, but never raised -- cleanup must not
    itself crash the program on the way out, since that would leave
    the user with a traceback instead of clear next steps.
    """
    logger = get_logger()

    if not run_state.started_by_us:
        logger.info("SSH was already running before Portable SSH started; leaving it running.")
        return

    logger.info("Stopping SSH service to restore original machine state...")
    try:
        services.stop(run_state.platform_info)
        logger.info("Done. SSH has been returned to its original state.")
    except ServiceError as exc:
        logger.warning(
            "Could not automatically stop the SSH service: %s\n"
            "You may need to stop it manually to fully restore this machine.",
            exc,
        )