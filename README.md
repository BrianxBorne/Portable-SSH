# Portable SSH

Temporarily enable SSH on a machine for remote access — and leave the machine exactly as it was found when you're done.

Portable SSH detects your operating system, installs an SSH server only if one isn't already present, starts the SSH service only if it isn't already running, shows you everything you need to connect to this machine remotely, and automatically restores the original state the moment you stop it.

---

## Overview

Sometimes you need SSH access to a machine *right now* — a friend's laptop, a lab workstation, a fresh cloud VM — without permanently changing its configuration. Portable SSH is a single cross-platform Python tool that:

- Figures out what OS/distro it's running on
- Installs an SSH server if one is missing
- Starts the SSH service if it isn't already running (and leaves it alone if it already was)
- Prints every LAN-reachable address you can connect to, with ready-to-copy `ssh` commands
- Runs until you press **Ctrl+C**
- Reverses exactly what it changed — nothing more, nothing less

It does **not** create users, set passwords, open firewall ports, or touch `sshd_config`. It only starts, monitors, and stops the SSH server that's already there.

---

## Features

- **Cross-platform**: Windows, macOS, and 8 Linux distributions out of the box
- **Non-destructive**: never restarts a service that was already running; never reinstalls an already-installed package
- **State-aware cleanup**: restores the machine to its original SSH state on Ctrl+C, normal exit, *or* an unexpected error
- **Smart network detection**: shows every real, LAN-reachable IPv4 address (including VPN adapters), while filtering out Docker, VirtualBox, VMware, Hyper-V, and WSL virtual adapters
- **Friendly error handling**: no raw Python tracebacks in normal use; a `--verbose` flag for full diagnostics
- **Extensible by design**: adding a new Linux distribution is a single declarative entry, not a new file

---

## Architecture

Portable SSH is organized as a small set of single-responsibility modules, each owning exactly one concern:

```
                        ┌─────────────────┐
                        │ portable_ssh.py │   orchestrator (the only file
                        │   (entry point) │   that knows the overall flow)
                        └───┬─────┬───┬───┘
              ┌─────────────┘     │   └─────────────┐
              ▼                   ▼                 ▼
       ┌─────────────┐    ┌──────────────┐   ┌──────────────┐
       │ platform.py │◄───┤ installer.py │   │ services.py  │
       │ (OS/distro  │    │ (is SSH      │   │ (is the      │
       │  detection, │    │  installed?  │   │  service     │
       │  declarative│    │  install if  │   │  running?    │
       │  commands)  │    │  missing)    │   │  start/stop) │
       └─────────────┘    └──────────────┘   └──────┬───────┘
                                                     │
              ┌──────────────┐            ┌──────────▼─────────┐
              │  network.py  │            │     cleanup.py     │
              │ (hostname,   │            │ (RunState +        │
              │  reachable   │            │  guaranteed         │
              │  addresses,  │            │  restoration on     │
              │  gateway)    │            │  every exit path)   │
              └──────────────┘            └─────────────────────┘
                       ▲                             ▲
                       └──────────── utils.py ───────┘
                         (logging, subprocess runner,
                          privilege checks, formatting)
```

**Key design principles:**

- **Single source of truth for OS behavior.** `platform.py` is the *only* place that knows package managers, service names, and the exact commands to install/start/stop/check SSH on each OS. Every other module receives a fully-resolved `PlatformInfo` object and never branches on OS itself (with one narrow, documented exception in `network.py` for read-only gateway detection).
- **Declarative distro table, not a class per OS.** Adding a new Linux distribution means adding one `DistroProfile` entry to a dictionary in `platform.py` — no new files, no subclassing.
- **State is recorded once, trusted later.** Before anything is changed, `portable_ssh.py` records whether SSH was already installed and already running into a `RunState` object. Cleanup acts *only* on that recorded state — it never re-queries the OS during shutdown, which avoids race conditions between what changed and what cleanup assumes.
- **Cleanup is a context manager, not a signal handler.** The entire "display info and wait for Ctrl+C" phase runs inside `cleanup.managed_session(run_state)`, a plain `try/finally` under the hood. This guarantees restoration on normal exit, Ctrl+C, *and* unexpected crashes, without the re-entrancy pitfalls of a real OS signal handler.
- **One subprocess chokepoint.** Every module that shells out goes through `utils.run_command()`, which enforces `shell=False` (no injection risk), a timeout on every call, and normalized success/failure results — no module handles raw `subprocess.CompletedProcess` or raw exceptions directly.

---

## Supported Platforms

| OS | Package Manager | Notes |
|---|---|---|
| Windows 10 / 11 | Windows Capability (`Add-WindowsCapability`) | Requires running as Administrator |
| Ubuntu | `apt` | |
| Debian | `apt` | |
| Fedora | `dnf` | |
| Rocky Linux | `dnf` | |
| AlmaLinux | `dnf` | |
| CentOS | `yum` | |
| Arch Linux | `pacman` | |
| openSUSE (Leap / Tumbleweed) | `zypper` | |
| macOS | native `systemsetup` (Remote Login) | Requires running with `sudo` |

Unlisted Debian-, RHEL-, Arch-, or SUSE-based distributions (e.g. Linux Mint, Pop!_OS, Nobara) are automatically handled via `/etc/os-release`'s `ID_LIKE` fallback, using the closest matching family's commands.

---

## Installation

Requires **Python 3.11+**.

```bash
git clone https://github.com/BrianxBorne/Portable-SSH.git
cd Portable-SSH
pip install -r requirements.txt
```

Dependencies are minimal:
- [`psutil`](https://pypi.org/project/psutil/) — cross-platform network interface enumeration
- [`colorama`](https://pypi.org/project/colorama/) — Windows-only, optional terminal color support

---

## Usage

Run with the privileges your OS requires (Administrator on Windows, `sudo` on Linux/macOS):

```bash
# Linux / macOS
sudo python3 portable_ssh.py

# Windows (run PowerShell/cmd "as Administrator")
python portable_ssh.py
```

Enable verbose/debug logging for diagnostics:

```bash
sudo python3 portable_ssh.py --verbose
```

Example output:

```
==========================================================
Portable SSH
==========================================================

Operating System : Windows 11
Hostname         : OFFICE-PC
Username         : brian
SSH Status       : Running
SSH Port         : 22

Network Interfaces

Ethernet
192.168.1.50

Wi-Fi
10.0.0.40

VPN
172.18.5.12

Reachable Addresses

ssh brian@192.168.1.50
ssh brian@10.0.0.40
ssh brian@172.18.5.12

Press Ctrl+C to stop Portable SSH...
```

Press **Ctrl+C** at any time to stop. If Portable SSH started the SSH service itself, it will be stopped again. If SSH was already running before you launched Portable SSH, it's left running untouched.

---

## Project Structure

```
portable-ssh/
│
├── portable_ssh.py   # Entry point / orchestrator — the only file that knows the overall flow
├── platform.py        # OS & Linux distro detection; declarative install/service command table
├── installer.py       # Is the SSH server installed? Install it if missing.
├── services.py        # Is the SSH service running? Start / stop / status.
├── network.py         # Hostname, username, reachable IPv4 addresses, default gateway
├── cleanup.py          # RunState tracking + guaranteed restoration on every exit path
├── utils.py           # Logging, subprocess execution, privilege checks, formatting
├── requirements.txt
└── README.md
```

---

## Design Decisions

- **Why not merge `installer.py`'s and `services.py`'s installed-checks?** They answer genuinely different questions — "is the package on disk" vs. "is the service unit registered/running" — and there's a real transitional state on some distros where a package installs successfully before its systemd unit is registered. Keeping them separate lets that edge case be detected and reported clearly.
- **Why avoid the stdlib `platform` module?** This project's own `platform.py` shadows it — the script directory is first on `sys.path`, so `import platform` anywhere in this project resolves to our file, not the standard library's. Rather than fight that, the whole project avoids the stdlib module entirely, using `sys.getwindowsversion()`, `/etc/os-release` parsing, and `sw_vers` for OS/version detection instead.
- **Why is default-gateway detection inside `network.py` instead of `platform.py`?** It's read-only, informational, and used only by this one module — folding it into the install/service command abstraction would add more indirection than it removes. This is documented as a deliberate, narrow exception in `network.py`'s module docstring.
- **Why keep VPN adapters in the reachable-address list?** A VPN address is often exactly the address a remote user wants to connect through. Only adapters that are *never* LAN-reachable (Docker, VirtualBox, VMware, Hyper-V, WSL, loopback) are filtered out.

---

## Security Notes

Portable SSH deliberately does **not**:

- Create SSH users or set passwords
- Modify `sshd_config`
- Open firewall ports
- Store any credentials or state to disk

It only starts, monitors, and stops the SSH server that is already present on the machine, and only reinstalls it if it's genuinely missing. Every subprocess call uses `shell=False` with argument lists (never interpolated shell strings), eliminating shell-injection risk. Elevated privileges (Administrator / `sudo` / root) are required and checked up front, since installing packages and managing system services both require them on every supported platform.

---

## Troubleshooting

| Problem | Likely cause / fix |
|---|---|
| "requires administrator privileges" / "requires root privileges" | Re-run as Administrator (Windows) or with `sudo` (Linux/macOS) |
| Installation fails | Check network access to your package repositories; on `apt` systems Portable SSH already retries once after `apt-get update` |
| "SSH service is not registered... even though installation reported success" | Some distros need a moment after install before the service unit appears — try again in a few seconds |
| No reachable addresses shown | The machine may not be connected to a network, or all detected interfaces are virtual/inactive — SSH is still running locally |
| Need more detail on any failure | Re-run with `--verbose` for full diagnostic logging |

---

## Future Roadmap

The architecture is designed so these can be added without restructuring existing modules:

- Custom SSH ports
- Temporary, auto-expiring SSH keys
- QR codes for SSH connection strings
- Optional, explicit firewall management
- Session expiration timers
- Bonjour/mDNS discovery
- Reverse SSH tunnels
- SFTP mode
- JSON / REST API output modes
- Web dashboard and remote monitoring
- Plugin system
- Configuration file support
