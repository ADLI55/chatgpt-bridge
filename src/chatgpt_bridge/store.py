"""SQLite index of a ChatGPT export, with search that works for Chinese too.

Full-text search uses SQLite FTS5 with the ``trigram`` tokenizer, which matches
substrings and so handles text without spaces (Chinese, Japanese). Trigrams need
at least three characters, so shorter terms (most two-character Chinese words)
fall back to a ``LIKE`` scan, which is still fast for a personal history.
"""

from __future__ import annotations

import math
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable, Iterator

from .parser import MAIN, Conversation

SCHEMA_VERSION = "1"

SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE conversations (
    id            TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    create_time   REAL,
    update_time   REAL,
    model         TEXT,
    gizmo_id      TEXT,
    is_archived   INTEGER NOT NULL DEFAULT 0,
    message_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE messages (
    id              INTEGER PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    seq             INTEGER NOT NULL,
    role            TEXT NOT NULL,
    channel         TEXT NOT NULL,
    create_time     REAL,
    text            TEXT NOT NULL
);
CREATE INDEX messages_by_conversation ON messages(conversation_id, seq);
CREATE INDEX conversations_by_time ON conversations(update_time);
"""

FTS_SCHEMA = (
    "CREATE VIRTUAL TABLE messages_fts USING fts5("
    "text, content='messages', content_rowid='id', tokenize='trigram')"
)

_CHATGPT_URL = re.compile(r"/c/([0-9a-fA-F-]{8,})")
_TERM = re.compile(r'"([^"]+)"|(\S+)')


class HistoryNotImported(RuntimeError):
    pass


def default_db_path() -> Path:
    env = os.environ.get("CHATGPT_BRIDGE_DB")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".chatgpt-bridge" / "history.db"


# ---------------------------------------------------------------- dates


def parse_date(value: str | None, *, end: bool = False) -> float | None:
    """Parse YYYY, YYYY-MM or YYYY-MM-DD (local time) to an epoch timestamp.

    With ``end=True`` returns the start of the *next* period, for use as an
    exclusive upper bound, so ``until="2024-03"`` covers all of March 2024.
    """
    if not value:
        return None
    value = value.strip()
    for fmt, unit in (("%Y-%m-%d", "day"), ("%Y-%m", "month"), ("%Y", "year")):
        try:
            dt = datetime.strptime(value, fmt)
        except ValueError:
            continue
        if end:
            if unit == "day":
                dt = datetime.fromordinal(dt.toordinal() + 1)
            elif unit == "month":
                dt = dt.replace(year=dt.year + dt.month // 12, month=dt.month % 12 + 1)
            else:
                dt = dt.replace(year=dt.year + 1)
        return dt.timestamp()
    raise ValueError(f"Unrecognised date {value!r}; use YYYY, YYYY-MM or YYYY-MM-DD")


def format_time(ts: float | None) -> str:
    if not ts:
        return "unknown date"
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


# ---------------------------------------------------------------- import


def build_index(conversations: Iterable[Conversation], db_path: Path, source: str = "") -> dict:
    """Write a fresh index to ``db_path`` (atomically replacing any old one)."""
    db_path = Path(db_path).expanduser()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = db_path.with_name(db_path.name + ".tmp")
    tmp.unlink(missing_ok=True)

    conn = sqlite3.connect(tmp)
    try:
        conn.executescript(SCHEMA)
        try:
            conn.execute(FTS_SCHEMA)
            fts = True
        except sqlite3.OperationalError:  # SQLite older than 3.34: LIKE only
            fts = False

        n_conv = n_msg = 0
        for conv in conversations:
            main_count = sum(1 for m in conv.messages if m.channel == MAIN)
            conn.execute(
                "INSERT OR REPLACE INTO conversations VALUES (?,?,?,?,?,?,?,?)",
                (
                    conv.id,
                    conv.title,
                    conv.create_time,
                    conv.update_time,
                    conv.model,
                    conv.gizmo_id,
                    int(conv.is_archived),
                    main_count,
                ),
            )
            conn.executemany(
                "INSERT INTO messages (conversation_id, seq, role, channel, create_time, text)"
                " VALUES (?,?,?,?,?,?)",
                [
                    (conv.id, i, m.role, m.channel, m.create_time, m.text)
                    for i, m in enumerate(conv.messages)
                ],
            )
            n_conv += 1
            n_msg += main_count

        if fts:
            conn.execute("INSERT INTO messages_fts(messages_fts) VALUES ('rebuild')")
        meta = {
            "schema_version": SCHEMA_VERSION,
            "imported_at": str(time.time()),
            "source": source,
            "fts": "1" if fts else "0",
        }
        conn.executemany("INSERT INTO meta VALUES (?,?)", meta.items())
        conn.commit()
    finally:
        conn.close()

    os.replace(tmp, db_path)
    return {"conversations": n_conv, "messages": n_msg, "fts": fts, "db": str(db_path)}


# ---------------------------------------------------------------- queries


@dataclass
class Snippet:
    seq: int
    role: str
    text: str


@dataclass
class SearchHit:
    id: str
    title: str
    create_time: float | None
    update_time: float | None
    message_count: int
    score: float
    title_match: bool = False
    snippets: list[Snippet] = field(default_factory=list)


def _like_pattern(term: str) -> str:
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _fts_phrase(term: str) -> str:
    return '"' + term.replace('"', '""') + '"'


def split_terms(query: str) -> list[str]:
    terms = [(m.group(1) or m.group(2)).strip() for m in _TERM.finditer(query)]
    seen: list[str] = []
    for t in terms:
        if t and t.casefold() not in (s.casefold() for s in seen):
            seen.append(t)
    return seen


def make_snippet(text: str, terms: list[str], width: int = 240) -> str:
    flat = re.sub(r"\s+", " ", text).strip()
    folded = flat.casefold()
    positions = [folded.find(t.casefold()) for t in terms]
    positions = [p for p in positions if p >= 0]
    pos = min(positions) if positions else 0
    start = max(0, pos - width // 3)
    end = min(len(flat), start + width)
    start = max(0, end - width)
    out = flat[start:end]
    if start > 0:
        out = "…" + out
    if end < len(flat):
        out = out + "…"
    return out


class History:
    """Read-only access to an imported history. Opens a fresh connection per
    call, so re-importing while the MCP server runs is picked up immediately."""

    def __init__(self, db_path: Path | str | None = None):
        self.db_path = Path(db_path).expanduser() if db_path else default_db_path()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        if not self.db_path.exists():
            raise HistoryNotImported(
                f"No ChatGPT history imported yet (expected {self.db_path}). "
                "Export your data from ChatGPT (Settings -> Data controls -> Export data), "
                "then run: chatgpt-bridge import <path-to-export.zip>"
            )
        conn = sqlite3.connect(self.db_path.resolve().as_uri() + "?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    # -- overview

    def stats(self) -> dict:
        with self._connect() as conn:
            meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
            row = conn.execute(
                "SELECT COUNT(*) n, MIN(create_time) first, MAX(update_time) last FROM conversations"
            ).fetchone()
            n_msg = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE channel = ?", (MAIN,)
            ).fetchone()[0]
        return {
            "conversations": row["n"],
            "messages": n_msg,
            "first": row["first"],
            "last": row["last"],
            "imported_at": float(meta.get("imported_at", 0) or 0),
            "source": meta.get("source", ""),
            "fts": meta.get("fts") == "1",
        }

    def list_conversations(
        self,
        limit: int = 20,
        offset: int = 0,
        since: str | None = None,
        until: str | None = None,
        title_contains: str | None = None,
    ) -> tuple[list[sqlite3.Row], int]:
        where, params = self._date_clause(since, until)
        if title_contains:
            where.append("title LIKE ? ESCAPE '\\'")
            params.append(_like_pattern(title_contains))
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        with self._connect() as conn:
            total = conn.execute(f"SELECT COUNT(*) FROM conversations {clause}", params).fetchone()[0]
            rows = conn.execute(
                f"SELECT * FROM conversations {clause} "
                "ORDER BY COALESCE(update_time, create_time) DESC LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
        return rows, total

    # -- lookup

    def resolve_id(self, ref: str) -> str:
        """Accept a full id, a unique prefix, or a chatgpt.com/c/<id> link."""
        ref = ref.strip()
        m = _CHATGPT_URL.search(ref)
        if m:
            ref = m.group(1)
        with self._connect() as conn:
            if conn.execute("SELECT 1 FROM conversations WHERE id = ?", (ref,)).fetchone():
                return ref
            rows = conn.execute(
                "SELECT id, title FROM conversations WHERE id LIKE ? ESCAPE '\\' LIMIT 6",
                (_like_pattern(ref)[1:],),  # prefix match: drop the leading %
            ).fetchall()
        if len(rows) == 1:
            return rows[0]["id"]
        if not rows:
            raise KeyError(f"No conversation with id {ref!r}")
        options = ", ".join(f"{r['id']} ({r['title']})" for r in rows)
        raise KeyError(f"Id prefix {ref!r} is ambiguous: {options}")

    def get_conversation(self, ref: str) -> tuple[sqlite3.Row, list[sqlite3.Row]]:
        conv_id = self.resolve_id(ref)
        with self._connect() as conn:
            conv = conn.execute("SELECT * FROM conversations WHERE id = ?", (conv_id,)).fetchone()
            msgs = conn.execute(
                "SELECT seq, role, channel, create_time, text FROM messages "
                "WHERE conversation_id = ? ORDER BY seq",
                (conv_id,),
            ).fetchall()
        return conv, msgs

    # -- search

    @staticmethod
    def _date_clause(since: str | None, until: str | None, prefix: str = "") -> tuple[list[str], list]:
        where: list[str] = []
        params: list = []
        lo = parse_date(since)
        hi = parse_date(until, end=True)
        # A conversation matches if it was active at any point in the range.
        if lo is not None:
            where.append(f"COALESCE({prefix}update_time, {prefix}create_time) >= ?")
            params.append(lo)
        if hi is not None:
            where.append(f"COALESCE({prefix}create_time, {prefix}update_time) < ?")
            params.append(hi)
        return where, params

    def search(
        self,
        query: str,
        limit: int = 10,
        role: str = "any",
        since: str | None = None,
        until: str | None = None,
        snippets_per_hit: int = 2,
    ) -> list[SearchHit]:
        """Find conversations containing *all* terms (in the title or any message).

        Ranking: title matches count most, then how often each term appears.
        """
        terms = split_terms(query)
        if not terms:
            raise ValueError("Empty search query")
        role_sql, role_params = "", []
        if role in ("user", "assistant"):
            role_sql, role_params = " AND m.role = ?", [role]

        with self._connect() as conn:
            fts = conn.execute("SELECT value FROM meta WHERE key='fts'").fetchone()
            use_fts = bool(fts) and fts[0] == "1"

            per_term_msgs: list[dict[str, int]] = []
            per_term_titles: list[set[str]] = []
            for term in terms:
                if use_fts and len(term) >= 3:
                    sql = (
                        "SELECT m.conversation_id, COUNT(*) FROM messages_fts f "
                        "JOIN messages m ON m.id = f.rowid "
                        f"WHERE messages_fts MATCH ? AND m.channel = ?{role_sql} "
                        "GROUP BY m.conversation_id"
                    )
                    params = [_fts_phrase(term), MAIN, *role_params]
                else:
                    sql = (
                        "SELECT m.conversation_id, COUNT(*) FROM messages m "
                        f"WHERE m.text LIKE ? ESCAPE '\\' AND m.channel = ?{role_sql} "
                        "GROUP BY m.conversation_id"
                    )
                    params = [_like_pattern(term), MAIN, *role_params]
                per_term_msgs.append(dict(conn.execute(sql, params).fetchall()))
                per_term_titles.append(
                    {
                        r[0]
                        for r in conn.execute(
                            "SELECT id FROM conversations WHERE title LIKE ? ESCAPE '\\'",
                            (_like_pattern(term),),
                        )
                    }
                )

            candidates: set[str] | None = None
            for msgs, titles in zip(per_term_msgs, per_term_titles):
                ids = set(msgs) | titles
                candidates = ids if candidates is None else candidates & ids
            if not candidates:
                return []

            date_where, date_params = self._date_clause(since, until)
            rows = {}
            ids = list(candidates)
            for i in range(0, len(ids), 500):
                chunk = ids[i : i + 500]
                where = ["id IN (%s)" % ",".join("?" * len(chunk)), *date_where]
                for r in conn.execute(
                    f"SELECT * FROM conversations WHERE {' AND '.join(where)}", [*chunk, *date_params]
                ):
                    rows[r["id"]] = r

            hits: list[SearchHit] = []
            for cid, r in rows.items():
                score = 0.0
                title_match = False
                for msgs, titles in zip(per_term_msgs, per_term_titles):
                    if cid in titles:
                        score += 4.0
                        title_match = True
                    if cid in msgs:
                        score += 1.0 + math.log1p(msgs[cid])
                hits.append(
                    SearchHit(
                        id=cid,
                        title=r["title"],
                        create_time=r["create_time"],
                        update_time=r["update_time"],
                        message_count=r["message_count"],
                        score=round(score, 3),
                        title_match=title_match,
                    )
                )
            hits.sort(key=lambda h: (h.score, h.update_time or h.create_time or 0), reverse=True)
            hits = hits[:limit]

            for hit in hits:
                hit.snippets = self._snippets(conn, hit.id, terms, role_sql, role_params, snippets_per_hit)
        return hits

    @staticmethod
    def _snippets(conn, conv_id, terms, role_sql, role_params, n) -> list[Snippet]:
        likes = " OR ".join("m.text LIKE ? ESCAPE '\\'" for _ in terms)
        rows = conn.execute(
            "SELECT m.seq, m.role, m.text FROM messages m "
            f"WHERE m.conversation_id = ? AND m.channel = ?{role_sql} AND ({likes}) "
            "ORDER BY m.seq LIMIT 200",
            [conv_id, MAIN, *role_params, *(_like_pattern(t) for t in terms)],
        ).fetchall()
        if not rows:  # matched on the title only: show how the conversation starts
            rows = conn.execute(
                "SELECT m.seq, m.role, m.text FROM messages m "
                f"WHERE m.conversation_id = ? AND m.channel = ?{role_sql} ORDER BY m.seq LIMIT 1",
                [conv_id, MAIN, *role_params],
            ).fetchall()
        folded_terms = [t.casefold() for t in terms]

        def coverage(row) -> int:
            folded = row["text"].casefold()
            return sum(1 for t in folded_terms if t in folded)

        best = sorted(rows, key=lambda r: (-coverage(r), r["seq"]))[:n]
        best.sort(key=lambda r: r["seq"])
        return [Snippet(r["seq"], r["role"], make_snippet(r["text"], terms)) for r in best]
