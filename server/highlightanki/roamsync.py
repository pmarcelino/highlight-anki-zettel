"""Refresh org-roam's cache after writing notes outside Emacs (org format only).

org-roam v1 only marks its database dirty on in-Emacs saves, so files this
server writes stay invisible to a running Emacs until the next
`org-roam-db-build-cache`. We ask that Emacs to rebuild via emacsclient; it
updates its own DB and in-memory session, so nothing else ever writes the
sqlite file while Emacs holds it.

No emacsclient on PATH, or no Emacs server running (`M-x server-start`) ->
skip silently: enabling `org-roam-mode` at Emacs startup runs a full
`org-roam-db-build-cache`, so the notes appear the next time Emacs opens.

If the server runs from a process manager with a bare environment (launchd,
systemd), PATH may lack Homebrew and TMPDIR may be unset — emacsclient needs
TMPDIR to find the server socket. Both are handled here.

Best-effort by design: the note files are the source of truth, so a failed
sync only logs.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
from pathlib import Path

log = logging.getLogger(__name__)

TIMEOUT = 120
EMACSCLIENT_CANDIDATES = (
    "/opt/homebrew/bin/emacsclient",
    "/usr/local/bin/emacsclient",
    "/Applications/Emacs.app/Contents/MacOS/bin/emacsclient",
)
# Some configs (Doom) defer org-roam, so require it before building.
BUILD_FORM = "(progn (require 'org-roam) (org-roam-db-build-cache))"

_lock = threading.Lock()  # rebuilds are idempotent; don't queue duplicates


def sync(org_dir: Path) -> None:
    """Best-effort org-roam cache refresh; never raises."""
    with _lock:
        try:
            _sync(org_dir)
        except Exception:
            log.exception("org-roam cache sync failed")


def _sync(org_dir: Path) -> None:
    client = _emacsclient()
    if client is None:
        log.warning("emacsclient not found; skipping org-roam cache sync")
        return
    if _run([client, "-e", BUILD_FORM]):
        log.info("org-roam cache refreshed for %s", org_dir)
    else:
        log.info("no live Emacs server; org-roam rebuilds itself on next "
                 "Emacs start (org-roam-mode enable)")


def _emacsclient() -> str | None:
    found = shutil.which("emacsclient")
    if found:
        return found
    for candidate in EMACSCLIENT_CANDIDATES:
        if os.access(candidate, os.X_OK):
            return candidate
    return None


def _env() -> dict[str, str]:
    """emacsclient locates the socket via $TMPDIR; launchd doesn't set it."""
    env = dict(os.environ)
    if "TMPDIR" not in env:
        try:
            out = subprocess.run(
                ["/usr/bin/getconf", "DARWIN_USER_TEMP_DIR"],
                capture_output=True, text=True, timeout=10).stdout.strip()
        except OSError:
            out = ""
        if out.startswith("/"):
            env["TMPDIR"] = out
    return env


def _run(cmd: list[str]) -> bool:
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=TIMEOUT,
                              env=_env())
    except (subprocess.TimeoutExpired, OSError):
        return False
    # emacsclient can exit 0 while the evaluated form errored
    return proc.returncode == 0 and b"*ERROR*" not in proc.stdout
