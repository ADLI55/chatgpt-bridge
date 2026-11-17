import pytest

from chatgpt_bridge import store
from chatgpt_bridge.parser import load_export
from chatgpt_bridge.server import format_conversation
from chatgpt_bridge.store import History, HistoryNotImported, build_index, parse_date, split_terms

A = "aaaa1111-0000-0000-0000-000000000001"
B = "bbbb2222-0000-0000-0000-000000000002"
C = "cccc3333-0000-0000-0000-000000000003"
D = "dddd4444-0000-0000-0000-000000000004"


def ids(hits):
    return [h.id for h in hits]


def test_stats(db):
    s = History(db).stats()
    assert s["conversations"] == 4
    assert s["fts"] is True


def test_chinese_three_chars_uses_fts(db):
    hits = History(db).search("缺失值")
    assert ids(hits) == [A]
    assert any("缺失值" in s.text for s in hits[0].snippets)


def test_chinese_two_chars_uses_like(db):
    assert ids(History(db).search("清洗")) == [A]


def test_terms_may_be_in_different_messages(db):
    # "pandas" is in the user's message, "fillna" in ChatGPT's reply
    assert ids(History(db).search("pandas fillna")) == [A]


def test_all_terms_required(db):
    assert History(db).search("pandas Kyoto") == []


def test_english_case_insensitive(db):
    assert ids(History(db).search("kyoto TEMPLES")) == [B]


def test_phrase(db):
    assert ids(History(db).search('"classic temples"')) == [B]
    assert History(db).search('"temples classic"') == []


def test_role_filter(db):
    h = History(db)
    assert h.search("fillna", role="user") == []
    assert ids(h.search("fillna", role="assistant")) == [A]


def test_date_filter(db):
    h = History(db)
    assert h.search("Kyoto", until="2023-06") == []
    assert ids(h.search("Kyoto", since="2023-07", until="2023-07")) == [B]
    assert ids(h.search("Kyoto", since="2023")) == [B]
    with pytest.raises(ValueError):
        h.search("Kyoto", since="last week")


def test_title_only_match_ranks_and_has_snippet(db):
    hits = History(db).search("索引")
    assert ids(hits) == [D]
    assert hits[0].title_match
    assert hits[0].snippets and "faster" in hits[0].snippets[0].text


def test_hidden_and_tool_text_not_searchable(db):
    h = History(db)
    assert h.search("secretreasoning") == []
    assert h.search("zzcodeonly") == []
    assert h.search("abandonedbranch") == []


def test_like_wildcards_are_literal(db):
    assert History(db).search("100%") == []
    assert History(db).search("_") == []


def test_resolve_id(db):
    h = History(db)
    assert h.resolve_id("bbbb") == B
    assert h.resolve_id(f"https://chatgpt.com/c/{C}") == C
    with pytest.raises(KeyError):
        h.resolve_id("ffff")


def test_list_and_paging(db):
    rows, total = History(db).list_conversations(limit=2)
    assert total == 4
    assert [r["id"] for r in rows] == [C, D]  # newest first
    rows, total = History(db).list_conversations(title_contains="kyoto")
    assert total == 1 and rows[0]["id"] == B


def test_read_conversation_paging(db):
    conv, msgs = History(db).get_conversation(C)
    page1 = format_conversation(conv, msgs, 0, 5000, False)
    assert "Continue with start=" in page1
    nxt = int(page1.rsplit("start=", 1)[1].rstrip("."))
    page2 = format_conversation(conv, msgs, nxt, 5000, False)
    assert f"[#{nxt}]" in page2 and f"[#{nxt}]" not in page1


def test_read_conversation_tools_toggle(db):
    conv, msgs = History(db).get_conversation(A)
    assert "zzcodeonly" not in format_conversation(conv, msgs, 0, 100000, False)
    assert "zzcodeonly" in format_conversation(conv, msgs, 0, 100000, True)
    assert "secretreasoning" not in format_conversation(conv, msgs, 0, 100000, True)


def test_not_imported(tmp_path):
    with pytest.raises(HistoryNotImported, match="chatgpt-bridge import"):
        History(tmp_path / "missing.db").search("x")


def test_reimport_is_seen_by_existing_history(db, conversations, tmp_path):
    import json

    h = History(db)
    assert h.stats()["conversations"] == 4
    path = tmp_path / "conversations.json"
    path.write_text(json.dumps(conversations[:1]))
    build_index(load_export(path), db)
    assert h.stats()["conversations"] == 1


def test_without_fts(monkeypatch, export_zip, tmp_path):
    monkeypatch.setattr(store, "FTS_SCHEMA", "CREATE VIRTUAL TABLE x USING no_such_module()")
    path = tmp_path / "nofts.db"
    result = build_index(load_export(export_zip), path)
    assert result["fts"] is False
    assert ids(History(path).search("缺失值")) == [A]
    assert ids(History(path).search("pandas fillna")) == [A]


def test_helpers():
    assert split_terms('foo "bar baz"  Foo') == ["foo", "bar baz"]
    assert parse_date("2024-12", end=True) == parse_date("2025-01")
    assert parse_date("2024-02-28", end=True) == parse_date("2024-02-29")
