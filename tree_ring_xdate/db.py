"""SQLite persistence layer.

The whole lab state lives in one SQLite database file so the service can
run fully offline.  Tables:

series         one row per ingested sample (latest version of a sample id)
rings          every measurement row (width + flags), linked to a series
runs           one cross-dating calculation (sample x reference x params)
candidates     candidate offsets produced by a run
hypotheses     named dating hypotheses (sets of locked placements)
locks          (hypothesis, sample) -> locked offset/start year
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS series (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    sample_id       TEXT NOT NULL UNIQUE,
    unit            TEXT NOT NULL,
    known_start     INTEGER,
    n_rings         INTEGER NOT NULL,
    has_missing     INTEGER NOT NULL DEFAULT 0,
    raw_payload     TEXT,
    payload_format  TEXT,
    warnings        TEXT NOT NULL DEFAULT '[]',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    series_id   INTEGER NOT NULL REFERENCES series(id) ON DELETE CASCADE,
    seq         INTEGER NOT NULL,          -- 1-based position in the sample
    year        INTEGER,                   -- NULL for undated samples
    width       REAL NOT NULL,             -- 0 marks a missing ring
    missing     INTEGER NOT NULL DEFAULT 0,
    raw_line    INTEGER,                   -- original source line / item index
    UNIQUE(series_id, seq),
    UNIQUE(series_id, year)
);

CREATE TABLE IF NOT EXISTS runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    sample_id       TEXT NOT NULL,
    reference       TEXT NOT NULL,         -- 'master' or a sample id
    offset_min      INTEGER,
    offset_max      INTEGER,
    min_overlap     INTEGER NOT NULL,
    narrow_z        REAL NOT NULL,
    narrow_q        REAL NOT NULL,
    tolerance       REAL NOT NULL,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS candidates (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    offset          INTEGER NOT NULL,
    overlap_start   INTEGER NOT NULL,
    overlap_end     INTEGER NOT NULL,
    n_overlap       INTEGER NOT NULL,
    correlation     REAL,
    sign_agreement  REAL,
    narrow_hits     TEXT NOT NULL,         -- JSON list of hit years
    n_narrow_hits   INTEGER NOT NULL,
    rank            INTEGER NOT NULL,
    UNIQUE(run_id, offset)
);

CREATE TABLE IF NOT EXISTS hypotheses (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    note        TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS locks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    hypothesis_id   INTEGER NOT NULL REFERENCES hypotheses(id) ON DELETE CASCADE,
    sample_id       TEXT NOT NULL,
    offset          INTEGER NOT NULL,      -- calendar year of ring position 1
    source_run_id   INTEGER REFERENCES runs(id) ON DELETE SET NULL,
    created_at      TEXT NOT NULL,
    UNIQUE(hypothesis_id, sample_id)
);

CREATE INDEX IF NOT EXISTS idx_rings_series ON rings(series_id);
CREATE INDEX IF NOT EXISTS idx_locks_hyp ON locks(hypothesis_id);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(db_path: str) -> sqlite3.Connection:
    """Open (and initialise) the lab database.

    ``check_same_thread=False`` is safe here because every request runs
    under the server-wide RLock injected by :mod:`tree_ring_xdate.server`.
    """
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


# ---------------------------------------------------------------------------
# Series
# ---------------------------------------------------------------------------

def upsert_series(conn: sqlite3.Connection, record: dict) -> int:
    """Insert or replace one validated sample.

    ``record`` is the dict produced by :func:`validate.validate_sample`
    (or by ``parse_payload``).  The replacement is atomic: old rings are
    removed together with the old series row.
    """
    ts = now_iso()
    with conn:
        old = conn.execute(
            "SELECT id, created_at FROM series WHERE sample_id = ?",
            (record["sample_id"],),
        ).fetchone()
        if old is None:
            cur = conn.execute(
                """INSERT INTO series (sample_id, unit, known_start, n_rings,
                       has_missing, raw_payload, payload_format, warnings,
                       created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    record["sample_id"],
                    record["unit"],
                    record.get("known_start"),
                    len(record["rings"]),
                    1 if record.get("has_missing") else 0,
                    record.get("raw_payload"),
                    record.get("payload_format"),
                    json.dumps(record.get("warnings", []), ensure_ascii=False),
                    ts, ts,
                ),
            )
            series_id = cur.lastrowid
        else:
            series_id = old["id"]
            conn.execute("DELETE FROM series WHERE id = ?", (series_id,))
            cur = conn.execute(
                """INSERT INTO series (id, sample_id, unit, known_start, n_rings,
                       has_missing, raw_payload, payload_format, warnings,
                       created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    series_id,
                    record["sample_id"],
                    record["unit"],
                    record.get("known_start"),
                    len(record["rings"]),
                    1 if record.get("has_missing") else 0,
                    record.get("raw_payload"),
                    record.get("payload_format"),
                    json.dumps(record.get("warnings", []), ensure_ascii=False),
                    old["created_at"], ts,
                ),
            )
        rows = [
            (
                series_id,
                ring["seq"],
                ring.get("year"),
                float(ring["width"]),
                1 if ring.get("missing") else 0,
                ring.get("raw_line"),
            )
            for ring in record["rings"]
        ]
        conn.executemany(
            """INSERT INTO rings (series_id, seq, year, width, missing, raw_line)
               VALUES (?,?,?,?,?,?)""",
            rows,
        )
    return series_id


def get_series(conn: sqlite3.Connection, sample_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM series WHERE sample_id = ?", (sample_id,)
    ).fetchone()
    if row is None:
        return None
    return series_row_to_dict(conn, row)


def list_series(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM series ORDER BY sample_id").fetchall()
    return [series_row_to_dict(conn, r, with_rings=False) for r in rows]


def series_row_to_dict(conn: sqlite3.Connection, row: sqlite3.Row,
                       with_rings: bool = True) -> dict:
    d = dict(row)
    d["warnings"] = json.loads(d["warnings"] or "[]")
    d["has_missing"] = bool(d["has_missing"])
    if with_rings:
        rings = conn.execute(
            "SELECT seq, year, width, missing, raw_line FROM rings "
            "WHERE series_id = ? ORDER BY seq", (row["id"],)
        ).fetchall()
        d["rings"] = [
            {
                "seq": r["seq"],
                "year": r["year"],
                "width": r["width"],
                "missing": bool(r["missing"]),
                "raw_line": r["raw_line"],
            }
            for r in rings
        ]
    return d


def delete_series(conn: sqlite3.Connection, sample_id: str) -> bool:
    with conn:
        cur = conn.execute(
            "DELETE FROM series WHERE sample_id = ?", (sample_id,)
        )
    return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Runs / candidates
# ---------------------------------------------------------------------------

def save_run(conn: sqlite3.Connection, sample_id: str, reference: str,
             params: dict, candidates: list[dict]) -> int:
    ts = now_iso()
    with conn:
        cur = conn.execute(
            """INSERT INTO runs (sample_id, reference, offset_min, offset_max,
                   min_overlap, narrow_z, narrow_q, tolerance, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                sample_id, reference,
                params.get("offset_min"), params.get("offset_max"),
                params["min_overlap"], params["narrow_z"], params["narrow_q"],
                params["tolerance"], ts,
            ),
        )
        run_id = cur.lastrowid
        conn.executemany(
            """INSERT INTO candidates (run_id, offset, overlap_start,
                   overlap_end, n_overlap, correlation, sign_agreement,
                   narrow_hits, n_narrow_hits, rank)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            [
                (
                    run_id, c["offset"], c["overlap_start"], c["overlap_end"],
                    c["n_overlap"], c.get("correlation"),
                    c.get("sign_agreement"),
                    json.dumps(c["narrow_hits"]), c["n_narrow_hits"],
                    c["rank"],
                )
                for c in candidates
            ],
        )
    return run_id


def list_runs(conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["candidates"] = [
            dict(c) for c in conn.execute(
                "SELECT * FROM candidates WHERE run_id = ? ORDER BY rank",
                (r["id"],),
            )
        ]
        for c in d["candidates"]:
            c["narrow_hits"] = json.loads(c["narrow_hits"])
        out.append(d)
    return out


# ---------------------------------------------------------------------------
# Hypotheses / locks
# ---------------------------------------------------------------------------

def create_hypothesis(conn: sqlite3.Connection, name: str,
                      note: str = "") -> int:
    ts = now_iso()
    with conn:
        cur = conn.execute(
            "INSERT INTO hypotheses (name, note, created_at, updated_at) "
            "VALUES (?,?,?,?)", (name, note, ts, ts),
        )
        return cur.lastrowid


def get_hypothesis(conn: sqlite3.Connection, name: str,
                   with_locks: bool = True) -> dict | None:
    row = conn.execute(
        "SELECT * FROM hypotheses WHERE name = ?", (name,)
    ).fetchone()
    if row is None:
        return None
    d = dict(row)
    if with_locks:
        d["locks"] = [
            dict(r) for r in conn.execute(
                "SELECT sample_id, offset, source_run_id, created_at "
                "FROM locks WHERE hypothesis_id = ? ORDER BY sample_id",
                (row["id"],),
            )
        ]
    return d


def list_hypotheses(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT id, name, note, created_at, updated_at FROM hypotheses "
        "ORDER BY name"
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["n_locks"] = conn.execute(
            "SELECT COUNT(*) FROM locks WHERE hypothesis_id = ?", (r["id"],)
        ).fetchone()[0]
        out.append(d)
    return out


def upsert_lock(conn: sqlite3.Connection, hypothesis: str, sample_id: str,
                offset: int, source_run_id: int | None = None) -> dict:
    h = get_hypothesis(conn, hypothesis, with_locks=False)
    if h is None:
        raise KeyError(f"hypothesis not found: {hypothesis}")
    ts = now_iso()
    with conn:
        conn.execute(
            """INSERT INTO locks (hypothesis_id, sample_id, offset,
                   source_run_id, created_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(hypothesis_id, sample_id) DO UPDATE SET
                   offset = excluded.offset,
                   source_run_id = excluded.source_run_id,
                   created_at = excluded.created_at""",
            (h["id"], sample_id, int(offset), source_run_id, ts),
        )
        conn.execute(
            "UPDATE hypotheses SET updated_at = ? WHERE id = ?",
            (ts, h["id"]),
        )
    return {"hypothesis": hypothesis, "sample_id": sample_id,
            "offset": int(offset), "source_run_id": source_run_id}


def remove_lock(conn: sqlite3.Connection, hypothesis: str,
                sample_id: str) -> bool:
    h = get_hypothesis(conn, hypothesis, with_locks=False)
    if h is None:
        raise KeyError(f"hypothesis not found: {hypothesis}")
    with conn:
        cur = conn.execute(
            "DELETE FROM locks WHERE hypothesis_id = ? AND sample_id = ?",
            (h["id"], sample_id),
        )
    return cur.rowcount > 0


def delete_hypothesis(conn: sqlite3.Connection, name: str) -> bool:
    with conn:
        cur = conn.execute("DELETE FROM hypotheses WHERE name = ?", (name,))
    return cur.rowcount > 0


def locks_of(conn: sqlite3.Connection, hypothesis: str) -> dict[str, int]:
    h = get_hypothesis(conn, hypothesis, with_locks=False)
    if h is None:
        raise KeyError(f"hypothesis not found: {hypothesis}")
    rows = conn.execute(
        "SELECT sample_id, offset FROM locks WHERE hypothesis_id = ?",
        (h["id"],),
    ).fetchall()
    return {r["sample_id"]: r["offset"] for r in rows}
