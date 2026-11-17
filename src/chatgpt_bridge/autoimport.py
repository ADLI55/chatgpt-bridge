"""Find ChatGPT exports the user downloaded and import them without being asked.

The MCP server checks the watched folders (Downloads, Desktop, Documents by
default) at most once a minute. A ZIP, or a folder some browsers unzip it into
(Safari does this by default), counts as an export if it holds
``conversations.json`` or ``conversations-NNN.json``. When one is newer than the
export the index was built from, it is imported before the tool call answers.
"""

from __future__ import annotations

import os
import sys
import threading
import time
import zipfile
from pathlib import Path

from .parser import CONVERSATION_FILE, load_export
from .store import History, build_index

DEFAULT_INTERVAL = 60.0


def watch_dirs() -> list[Path]:
    env = os.environ.get("CHATGPT_BRIDGE_WATCH")
    if env:
        return [Path(p).expanduser() for p in env.split(os.pathsep) if p.strip()]
    home = Path.home()
    return [home / "Downloads", home / "Desktop", home / "Documents"]


def auto_import_enabled() -> bool:
    return os.environ.get("CHATGPT_BRIDGE_AUTO_IMPORT", "1").lower() not in ("0", "false", "no", "off")


def _export_mtime(path: Path) -> float | None:
    """Return the export's modification time if ``path`` is a ChatGPT export, else None."""
    try:
        if path.is_file():
            if path.suffix.lower() != ".zip" or not zipfile.is_zipfile(path):
                return None
            with zipfile.ZipFile(path) as zf:
                if any(CONVERSATION_FILE.match(Path(n).name) for n in zf.namelist()):
                    return path.stat().st_mtime
            return None
        if path.is_dir():
            files = [p for p in path.iterdir() if CONVERSATION_FILE.match(p.name)]
            if files:
                return max([path.stat().st_mtime, *(f.stat().st_mtime for f in files)])
    except (OSError, zipfile.BadZipFile, RuntimeError):
        return None
    return None


def find_latest_export(dirs: list[Path] | None = None, newer_than: float = 0.0) -> tuple[Path, float] | None:
    """Newest ChatGPT export in ``dirs`` modified after ``newer_than``."""
    candidates: list[tuple[float, Path]] = []
    for d in dirs if dirs is not None else watch_dirs():
        try:
            entries = list(d.iterdir())
        except OSError:
            continue
        for p in entries:
            try:
                st = p.stat()
            except OSError:
                continue
            if st.st_mtime <= newer_than and not p.is_dir():
                continue
            if p.is_file() and p.suffix.lower() != ".zip":
                continue
            candidates.append((st.st_mtime, p))
    for _, p in sorted(candidates, key=lambda c: c[0], reverse=True):
        mtime = _export_mtime(p)
        if mtime is not None and mtime > newer_than:
            return p, mtime
    return None


def import_export(path: Path, db_path: Path, mtime: float | None = None) -> dict:
    path = Path(path).expanduser()
    if mtime is None:
        mtime = path.stat().st_mtime
    conversations = load_export(path)
    return build_index(conversations, db_path, source=str(path.resolve()), source_mtime=mtime)


class AutoImporter:
    """Thread-safe, throttled "is there a newer export?" check used by the server."""

    def __init__(
        self,
        history: History,
        dirs: list[Path] | None = None,
        interval: float = DEFAULT_INTERVAL,
        enabled: bool | None = None,
    ):
        self.history = history
        self.dirs = dirs
        self.interval = interval
        self.enabled = auto_import_enabled() if enabled is None else enabled
        self._lock = threading.Lock()
        self._last_check = float("-inf")
        self._failed: set[tuple[str, float]] = set()

    def check(self, force: bool = False) -> str | None:
        """Import a newer export if there is one. Returns a note for the user, or None."""
        if not self.enabled and not force:
            return None
        with self._lock:
            now = time.monotonic()
            if not force and now - self._last_check < self.interval and self.history.db_path.exists():
                return None
            self._last_check = now
            found = find_latest_export(self.dirs, newer_than=self.history.source_mtime())
            if found is None:
                return None
            path, mtime = found
            if (str(path), mtime) in self._failed:
                return None
            try:
                result = import_export(path, self.history.db_path, mtime)
            except Exception as e:  # a half-downloaded or foreign ZIP must not break the tools
                self._failed.add((str(path), mtime))
                print(f"chatgpt-bridge: could not import {path}: {e}", file=sys.stderr)
                return f"(Found {path.name} but could not import it: {e})"
        return (
            f"(Auto-imported new ChatGPT export {path.name}: "
            f"{result['conversations']} conversations, {result['messages']} messages.)"
        )

    def import_now(self, path: Path | str | None = None) -> tuple[Path, dict]:
        """Import ``path``, or the newest export in the watched folders."""
        with self._lock:
            if path:
                src = Path(path).expanduser()
                return src, import_export(src, self.history.db_path)
            found = find_latest_export(self.dirs)
            if found is None:
                folders = ", ".join(str(d) for d in (self.dirs or watch_dirs()))
                raise FileNotFoundError(f"No ChatGPT export (ZIP with conversations.json) found in {folders}")
            src, mtime = found
            return src, import_export(src, self.history.db_path, mtime)

    def check_in_background(self) -> None:
        if not self.enabled:
            return
        threading.Thread(target=self.check, kwargs={"force": True}, daemon=True).start()
