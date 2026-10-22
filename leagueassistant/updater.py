"""Keeps the Windows .exe up to date from the repo's latest GitHub release.

Each release has leagueassistant.exe and build.txt ("<commit> <sha256 of the exe>").
The .exe knows its own commit (leagueassistant/_build.py, written by CI). When
the release has a different commit, we download the new .exe, check its hash,
swap it in and start it. Windows can't overwrite a running .exe but can rename
it, so the running one becomes leagueassistant.exe.old and is deleted on the
next start.
"""

from __future__ import annotations

import hashlib
import logging
import os
import subprocess
import sys
from pathlib import Path

import requests

log = logging.getLogger("leagueassistant.update")

RELEASE_URL = "https://github.com/danbrown1337/LeagueAssistant/releases/latest/download/"
CHECK_EVERY = 6 * 3600  # seconds between checks while the app is running
JUST_UPDATED = "LEAGUEASSISTANT_JUST_UPDATED"


def current_build() -> str | None:
    try:
        from ._build import COMMIT
    except ImportError:
        return None  # running from source or a local build
    return COMMIT


def enabled() -> bool:
    return bool(getattr(sys, "frozen", False)) and sys.platform == "win32" and current_build() is not None


def _release_url() -> str:
    return os.environ.get("LEAGUEASSISTANT_UPDATE_URL", RELEASE_URL)


def latest() -> tuple[str, str] | None:
    """(commit, sha256) of the latest release, or None if it can't be read."""
    r = requests.get(_release_url() + "build.txt", timeout=10)
    if r.status_code != 200:
        return None
    parts = r.text.split()
    return (parts[0], parts[1].lower()) if len(parts) >= 2 else None


def cleanup(exe: Path | None = None):
    """Delete the .exe left over from the last update."""
    exe = exe or Path(sys.executable)
    try:
        exe.with_name(exe.name + ".old").unlink(missing_ok=True)
    except OSError:
        pass  # still running, try next time


def restart(exe: Path, argv: list[str]):
    # PYINSTALLER_RESET_ENVIRONMENT makes the new .exe unpack itself fresh instead of
    # reusing this process's temporary folder, which is deleted when we exit.
    env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1", **{JUST_UPDATED: "1"})
    flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    subprocess.Popen([str(exe), *argv], env=env, creationflags=flags, close_fds=True)
    os._exit(0)


def update(argv: list[str], exe: Path | None = None, current: str | None = None, restart=restart) -> bool:
    """Install the latest release if it differs from this build.

    Calls `restart` (which by default starts the new .exe and exits) after a
    successful swap. Returns False when there was nothing to do or the update
    failed; failures are logged and never stop the app.
    """
    exe = exe or Path(sys.executable)
    current = current or current_build()
    try:
        info = latest()
    except requests.RequestException as e:
        log.info("Couldn't check for updates: %s", e)
        return False
    if not info or info[0] == current:
        return False
    commit, digest = info
    new = exe.with_name(exe.name + ".new")
    old = exe.with_name(exe.name + ".old")
    print(f"Updating LeagueAssistant to build {commit[:7]}...", flush=True)
    try:
        h = hashlib.sha256()
        with requests.get(_release_url() + "leagueassistant.exe", stream=True, timeout=60) as r:
            r.raise_for_status()
            with open(new, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
                    h.update(chunk)
        if h.hexdigest() != digest:
            log.warning("The downloaded update doesn't match its checksum; skipping it")
            new.unlink(missing_ok=True)
            return False
        old.unlink(missing_ok=True)
        exe.rename(old)
        try:
            new.rename(exe)
        except OSError:
            old.rename(exe)  # put the working .exe back
            raise
    except (requests.RequestException, OSError) as e:
        log.warning("Update failed, carrying on with this version: %s", e)
        try:
            new.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    print("Update installed. Restarting...", flush=True)
    restart(exe, argv)
    return True
