import json

import pytest

from chatgpt_bridge.parser import HIDDEN, MAIN, TOOL, load_export, parse_conversation


def by_title(convs, title):
    return next(c for c in convs if c.title == title)


def test_follows_current_branch_only(conversations):
    conv = parse_conversation(conversations[0])
    texts = " ".join(m.text for m in conv.messages)
    assert "pandas" in texts
    assert "abandonedbranch" not in texts
    assert "旧分支的回答" not in texts


def test_channels(conversations):
    conv = parse_conversation(conversations[0])
    main = [m for m in conv.messages if m.channel == MAIN]
    assert [m.role for m in main] == ["user", "assistant", "user", "assistant"]

    hidden = [m.text for m in conv.messages if m.channel == HIDDEN]
    assert any("secretreasoning" in t for t in hidden)

    tool = [m.text for m in conv.messages if m.channel == TOOL]
    assert any("zzcodeonly" in t and t.startswith("```python") for t in tool)
    assert any("toolresultxyz" in t for t in tool)


def test_multimodal_parts(conversations):
    conv = parse_conversation(conversations[0])
    img = next(m for m in conv.messages if "表格" in m.text and m.role == "user")
    assert img.text.startswith("[image]")


def test_load_zip(export_zip):
    convs = load_export(export_zip)
    assert {c.title for c in convs} == {
        "Python 数据清洗",
        "Trip planning to Kyoto",
        "Long chat",
        "PostgreSQL 索引优化",
    }


def test_load_split_files_in_folder(tmp_path, conversations):
    folder = tmp_path / "export"
    (folder / "nested").mkdir(parents=True)
    (folder / "conversations-000.json").write_text(json.dumps(conversations[:2]))
    (folder / "nested" / "conversations-001.json").write_text(json.dumps(conversations[2:]))
    (folder / "user.json").write_text("{}")
    assert len(load_export(folder)) == 4


def test_load_plain_json_and_dedupe(tmp_path, conversations):
    path = tmp_path / "conversations.json"
    path.write_text(json.dumps(conversations + [conversations[1]]))
    assert len(load_export(path)) == 4


def test_missing_current_node_falls_back_to_latest_leaf(conversations):
    raw = conversations[1]
    raw["current_node"] = None
    conv = parse_conversation(raw)
    assert [m.role for m in conv.messages] == ["user", "assistant"]


def test_zip_without_conversations(tmp_path):
    import zipfile

    path = tmp_path / "x.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("readme.txt", "hi")
    with pytest.raises(ValueError, match="no conversations"):
        load_export(path)
