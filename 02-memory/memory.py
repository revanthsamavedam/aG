"""Team memory — a SQLite store, per user, stdlib only.

The idea: after a run, the specialists' replies are mined for lines
that start with Decision: / Learned: / Best practice:. Those lines
are the durable part of a run — everything else is working text.
Next run, the stored lines are injected into the task prompt as
additional instructions, so the team builds on what it already
decided instead of re-deciding it.

Memory is per user. Every row carries a user_id, and every read
filters on (team_id, user_id) — no query ever reads memory without
a user, so one user's decisions never leak into another user's run.
That is the access-control rule, enforced in the store itself, not
left to the caller. The managed Foundry Memory Service is per-user
by the same shape; this is the hand-rolled version of that rule.

One table:

    entries(id, team_id, user_id, kind, member, text, created_at)
      kind: "decision" | "learning" | "best_practice"

No embeddings, no vector search in v1: recall() is keyword match on
the query, newest first. That is enough to teach the shape; a real
retriever can replace recall() later without touching the rest.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone

# Prefix (as written in a reply) -> kind stored in the DB.
# Matched case-insensitively, leading whitespace allowed.
_PREFIXES = {
    "decision:": "decision",
    "learned:": "learning",
    "best practice:": "best_practice",
}

_KIND_LABEL = {
    "decision": "Decisions",
    "learning": "Learnings",
    "best_practice": "Best practices",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id    TEXT NOT NULL,
    user_id    TEXT NOT NULL,
    kind       TEXT NOT NULL,
    member     TEXT NOT NULL,
    text       TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_entries_team_user
    ON entries (team_id, user_id, id);
"""


class MemoryStore:
    """SQLite-backed memory for one or more teams and users.

    Pass a path for a persistent store, or ":memory:" (the default)
    for tests and the demo's throwaway runs. The demo passes an
    explicit file path so you can inspect it after the run.

    Every method takes a user_id. There is no way to read or write
    memory without one — that is the per-user rule, enforced here.
    """

    def __init__(self, path: str = ":memory:"):
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate_user_id()
        self.conn.commit()

    def _migrate_user_id(self) -> None:
        """Add user_id to stores created before per-user memory.

        The v1 schema had no user_id column. An old demo DB
        (team_memory.db from before this change) gets the column
        added here; its old rows keep user_id '' and are never
        returned, because every read filters on a real user_id.
        """
        cols = [r[1] for r in self.conn.execute(
            "PRAGMA table_info(entries)").fetchall()]
        if cols and "user_id" not in cols:
            self.conn.execute(
                "ALTER TABLE entries ADD COLUMN user_id TEXT NOT NULL DEFAULT ''")

    # -- recording ------------------------------------------------------
    def record_from_reply(self, team_id: str, user_id: str, member: str,
                          text: str) -> list[dict]:
        """Parse Decision:/Learned:/Best practice: lines out of one
        agent reply and store them under (team_id, user_id).
        Returns the entries recorded.

        Extraction rules (v1, deliberately strict):
          * Only lines starting with one of the three prefixes count,
            and the prefix must be followed by a space — "Decision:
            use X" counts, "Decision:use X" does not.
          * The prefix match is case-insensitive; the value is the
            rest of the line, stripped. Empty values are dropped.
          * Every other line is ignored — no guessing, no LLM call.
          * One line = one entry. A reply with no prefixed lines
            records nothing.
        """
        recorded = []
        for line in (text or "").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            low = stripped.lower()
            for prefix, kind in _PREFIXES.items():
                if low.startswith(prefix):
                    rest = stripped[len(prefix):]
                    if rest and rest[0].isspace():
                        value = rest.strip()
                        if value:
                            recorded.append(self._insert(
                                team_id, user_id, kind, member, value))
                    break
        return recorded

    def _insert(self, team_id: str, user_id: str, kind: str, member: str,
                text: str) -> dict:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        cur = self.conn.execute(
            "INSERT INTO entries (team_id, user_id, kind, member, text, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (team_id, user_id, kind, member, text, now))
        self.conn.commit()
        return {"id": cur.lastrowid, "team_id": team_id, "user_id": user_id,
                "kind": kind, "member": member, "text": text,
                "created_at": now}

    # -- reading ---------------------------------------------------------
    def recall(self, team_id: str, user_id: str, query: str | None = None,
               limit: int = 10) -> list[dict]:
        """Relevant entries for one user on one team, newest first.

        No query -> the most recent entries. With a query -> entries
        whose text contains any of the query's words (case-insensitive
        substring match; v1 keyword search, no ranking beyond recency).
        Another user's entries are never returned.
        """
        if query:
            words = [w for w in re.split(r"\W+", query.lower()) if w]
            if not words:
                return []
            conds = " OR ".join("LOWER(text) LIKE ?" for _ in words)
            params: list = [f"%{w}%" for w in words]
            rows = self.conn.execute(
                f"SELECT * FROM entries WHERE team_id = ? AND user_id = ?"
                f" AND ({conds}) ORDER BY id DESC LIMIT ?",
                [team_id, user_id, *params, limit]).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM entries WHERE team_id = ? AND user_id = ?"
                " ORDER BY id DESC LIMIT ?",
                (team_id, user_id, limit)).fetchall()
        return [dict(r) for r in rows]

    def list_entries(self, team_id: str, user_id: str, kind: str | None = None,
                     limit: int | None = None) -> list[dict]:
        """All entries for one user on one team (newest first),
        optionally one kind."""
        sql = "SELECT * FROM entries WHERE team_id = ? AND user_id = ?"
        params: list = [team_id, user_id]
        if kind:
            sql += " AND kind = ?"
            params.append(kind)
        sql += " ORDER BY id DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        return [dict(r) for r in
                self.conn.execute(sql, params).fetchall()]

    def clear(self, team_id: str, user_id: str, kind: str | None = None) -> int:
        """Delete one user's entries on one team (optionally one
        kind). Returns how many were deleted. The table itself
        stays — clearing memory is not the same as dropping the
        store. Another user's entries are untouched."""
        sql = "DELETE FROM entries WHERE team_id = ? AND user_id = ?"
        params: list = [team_id, user_id]
        if kind:
            sql += " AND kind = ?"
            params.append(kind)
        cur = self.conn.execute(sql, params)
        self.conn.commit()
        return cur.rowcount

    def close(self) -> None:
        self.conn.close()


# -- injection ----------------------------------------------------------

def build_memory_context(store: MemoryStore, team_id: str, user_id: str,
                         limit: int = 10) -> str:
    """Format one user's recalled memory as a short prompt block.

    Empty string when the user has no memory yet — the wrapper then
    injects nothing, so a first run behaves exactly like a
    memory-less run. Grouped by kind so the Leader sees decisions,
    learnings and best practices as three short lists, not one
    long blob.
    """
    entries = store.recall(team_id, user_id, limit=limit)
    if not entries:
        return ""
    lines = [f"[Memory for user '{user_id}' — from earlier runs, "
             f"follow it unless the task says otherwise]"]
    for kind in ("decision", "learning", "best_practice"):
        group = [e for e in entries if e["kind"] == kind]
        if not group:
            continue
        lines.append(f"{_KIND_LABEL[kind]}:")
        for e in group:
            lines.append(f"- {e['text']} (@{e['member']})")
    return "\n".join(lines)


def run_team_with_memory(team, task: str, store: MemoryStore,
                         team_id: str, user_id: str) -> str:
    """Run the team with one user's memory: inject, run, record.

    1. build_memory_context() formats what this user already knows.
    2. The block is prepended to the task prompt as additional
       instructions — the ONLY injection point. Team.run itself is
       untouched (vendored file, not modified).
    3. After the run, every specialist reply in team.last_results is
       mined for Decision:/Learned:/Best practice: lines and stored
       under (team_id, user_id), so the next run for this user
       remembers this one. Another user's memory is untouched.
    Returns the team's final answer, same as Team.run.
    """
    context = build_memory_context(store, team_id, user_id)
    prompt = task
    if context:
        prompt = (f"{context}\n\n"
                  f"[Additional instructions — apply the memory "
                  f"above to this task]\n{task}")
    final = team.run(prompt)
    recorded = []
    for member, reply in team.last_results:
        recorded.extend(
            store.record_from_reply(team_id, user_id, member.handle, reply))
    if recorded:
        team.print(f"🧠 Recorded {len(recorded)} memory entr"
                   f"{'y' if len(recorded) == 1 else 'ies'} "
                   f"for user '{user_id}' on team '{team_id}'.")
    return final
