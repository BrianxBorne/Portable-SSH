# Portable SSH

Temporarily enable SSH on a machine for remote access — and leave the machine exactly as it was found when you're done.

Portable SSH detects your operating system, installs an SSH server only if one isn't already present, starts the SSH service only if it isn't already running, shows you everything you need to connect to this machine remotely, and automatically restores the original state the moment you stop it.

```
                               ┌──────────────────────────────────────────┐
                               │            portable_ssh.py               │
                               │              Entry Point                 │
                               │  Orchestrates the complete application   │
                               └───────────────┬──────────────────────────┘
                                               │
          ┌────────────────────┬───────────────┼───────────────┬────────────────────┐
          ▼                    ▼               ▼               ▼                    ▼
┌──────────────────┐ ┌──────────────────┐ ┌──────────────────┐ ┌──────────────────┐ ┌──────────────────┐
│   platform.py    │ │  installer.py    │ │   services.py    │ │   network.py     │ │   cleanup.py     │
├──────────────────┤ ├──────────────────┤ ├──────────────────┤ ├──────────────────┤ ├──────────────────┤
│ • Detect OS      │ │ • Check SSH      │ │ • Check service  │ │ • Hostname       │ │ • RunState       │
│ • Detect distro  │ │ • Install SSH    │ │ • Start/Stop     │ │ • Local IPs      │ │ • Restore state  │
│ • OS commands    │ │ • Uses platform  │ │ • Restart        │ │ • Gateway        │ │ • Safe exit      │
└─────────┬────────┘ └─────────┬────────┘ └─────────┬────────┘ └─────────┬────────┘ └─────────┬────────┘
          │                    │                    │                    │                    │
          └────────────────────┴────────────────────┴────────────────────┴────────────────────┘
                                               │
                                               ▼
                             ┌──────────────────────────────────────────┐
                             │               utils.py                   │
                             ├──────────────────────────────────────────┤
                             │ Shared by all modules                    │
                             │ • Logging                                │
                             │ • Subprocess execution                   │
                             │ • Privilege checks                       │
                             │ • Output formatting                      │
                             │ • Common helper functions                │
                             └──────────────────────────────────────────┘
```

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


## Installation

Requires **Python 3.11+**.

```bash
git clone https://github.com/BrianxBorne/Portable-SSH.git
cd Portable-SSH
pip install -r requirements.txt
```

Dependencies :
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
192.xx.x.xx

Wi-Fi
10.x.x.xx

VPN
172.xx.x.xx

Reachable Addresses

ssh saitama@192.xxx.x.xx
ssh saitama@10.x.x.xx
ssh saitama@172.xx.x.xx

Press Ctrl+C to stop Portable SSH...
```
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
