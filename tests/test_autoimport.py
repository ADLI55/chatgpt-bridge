import os
import time
import zipfile

from conftest import write_export

from chatgpt_bridge.autoimport import AutoImporter, find_latest_export
from chatgpt_bridge.store import History

NOW = time.time()


def test_finds_newest_export_and_ignores_other_files(tmp_path, conversations):
    d = tmp_path / "Downloads"
    old = write_export(d / "old-export.zip", conversations[:1], NOW - 3600)
    new = write_export(d / "new-export.zip", conversations, NOW - 60)
    with zipfile.ZipFile(d / "photos.zip", "w") as zf:  # newest, but not an export
        zf.writestr("a.jpg", "x")
    (d / "partial.zip").write_bytes(b"PK\x03\x04 half downloaded")
    (d / "notes.txt").write_text("hi")

    assert find_latest_export([d]) == (new, os.stat(new).st_mtime)
    assert find_latest_export([d], newer_than=NOW - 60) is None
    assert find_latest_export([d], newer_than=NOW - 7200)[0] == new
    assert old.exists()


def test_finds_folder_unzipped_by_browser(tmp_path, conversations):
    import json

    folder = tmp_path / "Downloads" / "chatgpt-export"
    folder.mkdir(parents=True)
    (folder / "conversations.json").write_text(json.dumps(conversations))
    found = find_latest_export([tmp_path / "Downloads"])
    assert found and found[0] == folder


def test_missing_folders_are_fine(tmp_path):
    assert find_latest_export([tmp_path / "nope"]) is None


def test_auto_import_flow(tmp_path, conversations):
    d = tmp_path / "Downloads"
    history = History(tmp_path / "db" / "history.db")
    auto = AutoImporter(history, dirs=[d], interval=3600, enabled=True)

    assert auto.check() is None  # nothing downloaded yet

    write_export(d / "export-1.zip", conversations[:2], NOW - 120)
    auto._last_check = float("-inf")
    note = auto.check()
    assert note and "export-1.zip" in note and "2 conversations" in note
    assert history.stats()["conversations"] == 2

    write_export(d / "export-2.zip", conversations, NOW - 60)
    assert auto.check() is None  # throttled
    note = auto.check(force=True)
    assert note and "export-2.zip" in note
    assert history.stats()["conversations"] == 4
    assert auto.check(force=True) is None  # nothing newer


def test_broken_export_is_reported_once(tmp_path):
    d = tmp_path / "Downloads"
    d.mkdir()
    with zipfile.ZipFile(d / "export.zip", "w") as zf:
        zf.writestr("conversations.json", "{not json")
    auto = AutoImporter(History(tmp_path / "h.db"), dirs=[d], enabled=True)
    note = auto.check(force=True)
    assert note and "could not import" in note
    assert auto.check(force=True) is None


def test_disabled(tmp_path, conversations):
    d = tmp_path / "Downloads"
    write_export(d / "export.zip", conversations)
    auto = AutoImporter(History(tmp_path / "h.db"), dirs=[d], enabled=False)
    assert auto.check() is None
    assert auto.check(force=True) is not None  # explicit refresh still works


def test_env_disable(monkeypatch, tmp_path):
    monkeypatch.setenv("CHATGPT_BRIDGE_AUTO_IMPORT", "0")
    assert AutoImporter(History(tmp_path / "h.db")).enabled is False
