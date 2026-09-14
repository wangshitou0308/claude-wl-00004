"""SQLite persistence layer.

The whole lab state lives in one SQLite database file so the service can
run fully offline.  Tables:

series         one row per ingested sample (latest version of a sample id)
rings          every measurement row (width + flags), linked to a series
runs           one cross-dating calculation (sample x reference x params)
candidates     candidate offsets produced by a run
hypotheses     named dating hypotheses (sets of locked placements)
locks          (hypothesis, sample) -> locked offset/start year
corrections    correction drafts (missing/false ring events), versioned
correction_events  event list of one draft version
correction_map adopted year mapping (hypothesis, sample, seq) -> year
stability_checks local stability-check jobs: parameters, adopted sample
                 mappings, frozen reference snapshot, per-window results
                 and misplacement flags (all as JSON evidence)
standardizations   sequence-standardization plans: per-sample detrending
                 methods/parameters, draft|validated|adopted|retired
                 lifecycle, versioned source mapping + parameters and the
                 fitted expected-growth curves and diagnostics
signal_assessments chronology signal-strength assessments: the frozen
                 dating hypothesis, optional standardized version, member
                 sample set and window parameters the assessment rests on,
                 the per-sample index snapshot (source mapping), per-window
                 depth/Rbar/EPS evidence (incl. jackknife deltas) and the
                 adopted reliable interval; draft|completed|adopted|retired
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
    standardization_id        INTEGER,     -- pinned adopted std version
    standardization_version   INTEGER,
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

CREATE TABLE IF NOT EXISTS corrections (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    sample_id           TEXT NOT NULL,
    offset              INTEGER NOT NULL,   -- calendar year of ring 1
    run_id              INTEGER REFERENCES runs(id) ON DELETE SET NULL,
    reference           TEXT NOT NULL DEFAULT 'master',
    hypothesis          TEXT,               -- reference hypothesis context
    min_overlap         INTEGER NOT NULL DEFAULT 20,
    narrow_z            REAL NOT NULL DEFAULT -1.0,
    narrow_q            REAL NOT NULL DEFAULT 0.1,
    note                TEXT NOT NULL DEFAULT '',
    status              TEXT NOT NULL DEFAULT 'draft',
                                        -- draft | adopted | revoked
    adopted_hypothesis  TEXT,
    adopted_version     INTEGER,
    latest_version      INTEGER NOT NULL DEFAULT 1,
    snapshot            TEXT NOT NULL,      -- JSON reference snapshot
    candidate           TEXT,               -- JSON source candidate stats
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS correction_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id    INTEGER NOT NULL REFERENCES corrections(id) ON DELETE CASCADE,
    version     INTEGER NOT NULL,
    events      TEXT NOT NULL,              -- JSON event list
    note        TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    UNIQUE(draft_id, version)
);

CREATE TABLE IF NOT EXISTS correction_map (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    hypothesis_id   INTEGER NOT NULL REFERENCES hypotheses(id) ON DELETE CASCADE,
    sample_id       TEXT NOT NULL,
    seq             INTEGER,                -- NULL: inserted missing year
    year            INTEGER NOT NULL,
    width           REAL NOT NULL,
    role            TEXT NOT NULL,          -- ring | missing | inserted_missing
    draft_id        INTEGER NOT NULL REFERENCES corrections(id) ON DELETE CASCADE,
    draft_version   INTEGER NOT NULL,
    UNIQUE(hypothesis_id, sample_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_corr_events ON correction_events(draft_id);
CREATE INDEX IF NOT EXISTS idx_corr_map ON correction_map(hypothesis_id);

CREATE TABLE IF NOT EXISTS stability_checks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    hypothesis      TEXT NOT NULL,        -- hypothesis whose placements are checked
    sample_id       TEXT,                 -- NULL: every placed sample
    reference       TEXT NOT NULL DEFAULT 'master',
    params          TEXT NOT NULL,        -- JSON window/step/thresholds
    note            TEXT NOT NULL DEFAULT '',
    snapshot        TEXT NOT NULL,        -- JSON frozen reference
    sample_maps     TEXT NOT NULL,        -- JSON adopted year mappings used
    windows         TEXT NOT NULL,        -- JSON per-window results
    flags           TEXT NOT NULL,        -- JSON suspected-misplacement flags
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_stab_hyp ON stability_checks(hypothesis);

CREATE TABLE IF NOT EXISTS standardizations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE,
    hypothesis      TEXT NOT NULL,   -- dating hypothesis supplying year maps
    note            TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'draft',
                                     -- draft | validated | adopted | retired
    latest_version  INTEGER NOT NULL DEFAULT 1,
    adopted_version INTEGER,          -- version the live chronology uses
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS standardization_versions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    std_id          INTEGER NOT NULL
                    REFERENCES standardizations(id) ON DELETE CASCADE,
    version         INTEGER NOT NULL,
    config          TEXT NOT NULL,    -- JSON per-sample method/parameters
    source_mapping  TEXT,             -- JSON year mapping frozen at adoption
    curves          TEXT,             -- JSON expected curves + diagnostics
    note            TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL,
    UNIQUE(std_id, version)
);

CREATE INDEX IF NOT EXISTS idx_std_versions
    ON standardization_versions(std_id);

CREATE TABLE IF NOT EXISTS signal_assessments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE,
    hypothesis      TEXT NOT NULL,   -- dating hypothesis supplying year maps
    note            TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'draft',
                                     -- draft | completed | adopted | retired
    version         INTEGER NOT NULL DEFAULT 1,   -- always 1: assessments are immutable
    params          TEXT NOT NULL,   -- JSON window/step/min_samples/eps_threshold/...
    sources         TEXT NOT NULL,   -- JSON frozen per-sample index (source mapping)
    members         TEXT NOT NULL,   -- JSON ordered member sample ids
    excluded_samples TEXT NOT NULL DEFAULT '[]',  -- JSON [{sample_id, reason}]
    windows         TEXT NOT NULL,   -- JSON per-window depth/Rbar/EPS/jackknife
    reliable_span   TEXT,            -- JSON adopted window/year span (NULL until adopted)
    adopted_at      TEXT,
    standardization_id      INTEGER, -- pinned adopted std version (raw basis: NULL)
    standardization_version INTEGER,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_signal_hyp ON signal_assessments(hypothesis);
CREATE INDEX IF NOT EXISTS idx_signal_status ON signal_assessments(status);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# Columns added after the first release; CREATE TABLE IF NOT EXISTS never
# alters an existing database, so patch older files in place.
_ADDED_COLUMNS = {
    "runs": [("standardization_id", "INTEGER"),
             ("standardization_version", "INTEGER"),
             ("signal_id", "INTEGER"),
             ("signal_version", "INTEGER")],
}


def _migrate(conn: sqlite3.Connection) -> None:
    for table, cols in _ADDED_COLUMNS.items():
        existing = {r["name"] for r in
                    conn.execute(f"PRAGMA table_info({table})")}
        for name, decl in cols:
            if name not in existing:
                conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def connect(db_path: str) -> sqlite3.Connection:
    """Open (and initialise) the lab database.

    ``check_same_thread=False`` is safe here because every request runs
    under the server-wide RLock injected by :mod:`tree_ring_xdate.server`.
    """
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    with conn:
        _migrate(conn)
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
                   min_overlap, narrow_z, narrow_q, tolerance,
                   standardization_id, standardization_version,
                   signal_id, signal_version, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                sample_id, reference,
                params.get("offset_min"), params.get("offset_max"),
                params["min_overlap"], params["narrow_z"], params["narrow_q"],
                params["tolerance"],
                params.get("standardization_id"),
                params.get("standardization_version"),
                params.get("signal_id"),
                params.get("signal_version"), ts,
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


def get_candidate(conn: sqlite3.Connection, run_id: int,
                  offset: int) -> dict | None:
    """One stored candidate of a sliding run (for draft provenance)."""
    r = conn.execute(
        "SELECT * FROM candidates WHERE run_id = ? AND offset = ?",
        (run_id, offset),
    ).fetchone()
    if r is None:
        return None
    d = dict(r)
    d["narrow_hits"] = json.loads(d["narrow_hits"])
    return d


# ---------------------------------------------------------------------------
# Correction drafts
# ---------------------------------------------------------------------------

def save_correction(conn: sqlite3.Connection, *, sample_id: str, offset: int,
                    events: list[dict], run_id, reference: str,
                    hypothesis: str | None, min_overlap: int,
                    narrow_z: float, narrow_q: float, note: str,
                    snapshot: dict, candidate: dict | None) -> int:
    """Persist a new correction draft together with its first version."""
    ts = now_iso()
    with conn:
        cur = conn.execute(
            """INSERT INTO corrections (sample_id, offset, run_id, reference,
                   hypothesis, min_overlap, narrow_z, narrow_q, note, status,
                   latest_version, snapshot, candidate, created_at,
                   updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (sample_id, int(offset), run_id, reference, hypothesis,
             int(min_overlap), float(narrow_z), float(narrow_q), note,
             "draft", 1, json.dumps(snapshot, ensure_ascii=False),
             json.dumps(candidate, ensure_ascii=False)
             if candidate is not None else None, ts, ts),
        )
        draft_id = cur.lastrowid
        conn.execute(
            """INSERT INTO correction_events (draft_id, version, events,
                   note, created_at)
               VALUES (?,?,?,?,?)""",
            (draft_id, 1, json.dumps(events, ensure_ascii=False), note, ts),
        )
    return draft_id


def save_correction_version(conn: sqlite3.Connection, draft_id: int, *,
                            events: list[dict], note: str = "") -> int:
    """Append a new event-list version; returns the new version number."""
    ts = now_iso()
    with conn:
        row = conn.execute(
            "SELECT latest_version FROM corrections WHERE id = ?",
            (draft_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"correction draft not found: {draft_id}")
        version = row["latest_version"] + 1
        conn.execute(
            """INSERT INTO correction_events (draft_id, version, events,
                   note, created_at)
               VALUES (?,?,?,?,?)""",
            (draft_id, version, json.dumps(events, ensure_ascii=False),
             note, ts),
        )
        conn.execute(
            "UPDATE corrections SET latest_version = ?, updated_at = ? "
            "WHERE id = ?", (version, ts, draft_id),
        )
    return version


def _correction_head(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["draft_id"] = d.pop("id")
    d["snapshot"] = json.loads(d["snapshot"])
    d["candidate"] = (json.loads(d["candidate"])
                      if d["candidate"] else None)
    return d


def get_correction(conn: sqlite3.Connection, draft_id: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM corrections WHERE id = ?", (draft_id,),
    ).fetchone()
    return _correction_head(row) if row else None


def list_corrections(conn: sqlite3.Connection,
                     sample_id: str | None = None) -> list[dict]:
    sql = "SELECT * FROM corrections"
    args: tuple = ()
    if sample_id:
        sql += " WHERE sample_id = ?"
        args = (sample_id,)
    sql += " ORDER BY id DESC"
    return [_correction_head(r) for r in conn.execute(sql, args)]


def get_correction_version(conn: sqlite3.Connection, draft_id: int,
                           version: int) -> dict | None:
    head = get_correction(conn, draft_id)
    if head is None:
        return None
    row = conn.execute(
        "SELECT * FROM correction_events WHERE draft_id = ? AND version = ?",
        (draft_id, version),
    ).fetchone()
    if row is None:
        return None
    out = dict(head)
    out["version"] = version
    out["events"] = json.loads(row["events"])
    out["note"] = row["note"]
    out["created_at"] = row["created_at"]
    return out


def mark_correction_status(conn: sqlite3.Connection, draft_id: int,
                           status: str, *, hypothesis: str | None = None,
                           adopted_version: int | None = None) -> None:
    ts = now_iso()
    with conn:
        if status == "adopted":
            conn.execute(
                """UPDATE corrections SET status = ?, adopted_hypothesis = ?,
                       adopted_version = ?, updated_at = ? WHERE id = ?""",
                (status, hypothesis, adopted_version, ts, draft_id),
            )
        else:
            conn.execute(
                """UPDATE corrections SET status = ?, adopted_hypothesis = NULL,
                       adopted_version = NULL, updated_at = ? WHERE id = ?""",
                (status, ts, draft_id),
            )


def save_correction_mapping(conn: sqlite3.Connection, draft_id: int,
                            version: int, mapping: list[dict],
                            hypothesis: str | None = None) -> None:
    """Store the adopted year mapping and expose it to chronology queries.

    ``hypothesis`` falls back to the draft's adopted hypothesis recorded
    by :func:`mark_correction_status` (call order: mark first, or pass the
    name explicitly).
    """
    head = get_correction(conn, draft_id)
    hyp_name = hypothesis or (head or {}).get("adopted_hypothesis")
    h = get_hypothesis(conn, hyp_name, with_locks=False)
    if h is None:
        raise KeyError(f"hypothesis not found: {hyp_name}")
    sample_id = head["sample_id"]
    with conn:
        conn.execute(
            "DELETE FROM correction_map WHERE hypothesis_id = ? "
            "AND sample_id = ?", (h["id"], sample_id),
        )
        conn.executemany(
            """INSERT INTO correction_map (hypothesis_id, sample_id, seq,
                   year, width, role, draft_id, draft_version)
               VALUES (?,?,?,?,?,?,?,?)""",
            [
                (h["id"], sample_id, m["seq"], m["year"], m["width"],
                 m["role"], draft_id, version)
                for m in mapping if m["year"] is not None
            ],
        )


def clear_correction_mapping(conn: sqlite3.Connection,
                             draft_id: int) -> None:
    with conn:
        conn.execute(
            "DELETE FROM correction_map WHERE draft_id = ?", (draft_id,),
        )


def correction_maps_of(conn: sqlite3.Connection,
                       hypothesis: str) -> dict[str, list[dict]]:
    """Adopted correction mappings of one hypothesis: sample -> rows."""
    h = get_hypothesis(conn, hypothesis, with_locks=False)
    if h is None:
        return {}
    rows = conn.execute(
        """SELECT sample_id, seq, year, width, role, draft_id, draft_version
           FROM correction_map WHERE hypothesis_id = ?
           ORDER BY sample_id, year""",
        (h["id"],),
    ).fetchall()
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(r["sample_id"], []).append(dict(r))
    return out


# ---------------------------------------------------------------------------
# Stability checks
# ---------------------------------------------------------------------------

def save_stability_check(conn: sqlite3.Connection, *, hypothesis: str,
                         sample_id: str | None, reference: str,
                         params: dict, note: str, snapshot: dict,
                         sample_maps: dict, windows: list[dict],
                         flags: list[dict]) -> int:
    """Persist one stability-check job together with all its evidence."""
    ts = now_iso()
    with conn:
        cur = conn.execute(
            """INSERT INTO stability_checks (hypothesis, sample_id, reference,
                   params, note, snapshot, sample_maps, windows, flags,
                   created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (hypothesis, sample_id, reference,
             json.dumps(params, ensure_ascii=False), note,
             json.dumps(snapshot, ensure_ascii=False),
             json.dumps(sample_maps, ensure_ascii=False),
             json.dumps(windows, ensure_ascii=False),
             json.dumps(flags, ensure_ascii=False), ts),
        )
        return cur.lastrowid


def _stability_row(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["check_id"] = d.pop("id")
    for k in ("params", "snapshot", "sample_maps", "windows", "flags"):
        d[k] = json.loads(d[k])
    return d


def get_stability_check(conn: sqlite3.Connection,
                        check_id: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM stability_checks WHERE id = ?", (check_id,),
    ).fetchone()
    return _stability_row(row) if row else None


def list_stability_checks(conn: sqlite3.Connection,
                          hypothesis: str | None = None,
                          sample_id: str | None = None) -> list[dict]:
    """Lightweight check list (bulky evidence columns are summarised)."""
    sql = "SELECT * FROM stability_checks"
    cond, args = [], []
    if hypothesis:
        cond.append("hypothesis = ?")
        args.append(hypothesis)
    if sample_id:
        cond.append("(sample_id = ? OR sample_id IS NULL)")
        args.append(sample_id)
    if cond:
        sql += " WHERE " + " AND ".join(cond)
    sql += " ORDER BY id DESC"
    out = []
    for r in conn.execute(sql, args):
        d = _stability_row(r)
        d["n_windows"] = len(d["windows"])
        d["n_flags"] = len(d["flags"])
        for k in ("snapshot", "sample_maps", "windows", "flags"):
            d.pop(k)
        out.append(d)
    return out


# ---------------------------------------------------------------------------
# Sequence-standardization plans
# ---------------------------------------------------------------------------

STD_STATUSES = ("draft", "validated", "adopted", "retired")


def create_standardization(conn, *, name: str, hypothesis: str,
                           config: dict, note: str = "") -> int:
    """Create a plan (status draft) together with its config version 1."""
    ts = now_iso()
    with conn:
        cur = conn.execute(
            """INSERT INTO standardizations (name, hypothesis, note, status,
                   latest_version, adopted_version, created_at, updated_at)
               VALUES (?,?,?, 'draft', 1, NULL, ?, ?)""",
            (name, hypothesis, note, ts, ts),
        )
        std_id = cur.lastrowid
        conn.execute(
            """INSERT INTO standardization_versions (std_id, version, config,
                   note, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (std_id, 1, json.dumps(config, ensure_ascii=False), note, ts),
        )
    return std_id


def save_standardization_version(conn, std_id: int, config: dict, *,
                                 note: str = "") -> int:
    """Append a config version; returns the new version number."""
    ts = now_iso()
    with conn:
        row = conn.execute(
            "SELECT latest_version FROM standardizations WHERE id = ?",
            (std_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"standardization plan not found: {std_id}")
        version = row["latest_version"] + 1
        conn.execute(
            """INSERT INTO standardization_versions (std_id, version, config,
                   note, created_at)
               VALUES (?,?,?,?,?)""",
            (std_id, version, json.dumps(config, ensure_ascii=False), note, ts),
        )
        conn.execute(
            "UPDATE standardizations SET latest_version = ?, updated_at = ? "
            "WHERE id = ?", (version, ts, std_id),
        )
    return version


def _std_head(row: sqlite3.Row) -> dict:
    return dict(row)


def get_standardization(conn, std_id: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM standardizations WHERE id = ?", (std_id,),
    ).fetchone()
    return _std_head(row) if row else None


def get_standardization_by_name(conn, name: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM standardizations WHERE name = ?", (name,),
    ).fetchone()
    return _std_head(row) if row else None


def list_standardizations(conn, *, status: str | None = None,
                          hypothesis: str | None = None) -> list[dict]:
    sql = "SELECT * FROM standardizations"
    cond, args = [], []
    if status:
        cond.append("status = ?")
        args.append(status)
    if hypothesis:
        cond.append("hypothesis = ?")
        args.append(hypothesis)
    if cond:
        sql += " WHERE " + " AND ".join(cond)
    sql += " ORDER BY id DESC"
    out = []
    for r in conn.execute(sql, args):
        d = dict(r)
        # number of configured samples lives in the JSON of latest version
        v = conn.execute(
            "SELECT config FROM standardization_versions WHERE std_id = ? "
            "AND version = ?", (d["id"], d["latest_version"]),
        ).fetchone()
        d["n_samples"] = len(json.loads(v["config"])["samples"]) if v else 0
        out.append(d)
    return out


def get_std_version(conn, std_id: int, version: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM standardization_versions WHERE std_id = ? AND version = ?",
        (std_id, version),
    ).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["config"] = json.loads(d["config"])
    d["source_mapping"] = (json.loads(d["source_mapping"])
                           if d["source_mapping"] else None)
    d["curves"] = json.loads(d["curves"]) if d["curves"] else None
    return d


def list_std_versions(conn, std_id: int) -> list[dict]:
    rows = conn.execute(
        """SELECT version, note, created_at,
                  (source_mapping IS NOT NULL) AS frozen,
                  (curves IS NOT NULL) AS has_curves
           FROM standardization_versions WHERE std_id = ? ORDER BY version""",
        (std_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def freeze_std_version(conn, std_id: int, version: int,
                       source_mapping: dict, curves: dict) -> None:
    """Freeze the adopted source mapping together with the fitted curves."""
    ts = now_iso()
    with conn:
        conn.execute(
            """UPDATE standardization_versions
               SET source_mapping = ?, curves = ?
               WHERE std_id = ? AND version = ?""",
            (json.dumps(source_mapping, ensure_ascii=False),
             json.dumps(curves, ensure_ascii=False), std_id, version),
        )


def mark_standardization_status(conn, std_id: int, status: str, *,
                                adopted_version: int | None = None,
                                hypothesis: str | None = None) -> None:
    ts = now_iso()
    if status == "adopted":
        with conn:
            conn.execute(
                """UPDATE standardizations SET status = ?, adopted_version = ?,
                       updated_at = ? WHERE id = ?""",
                (status, adopted_version, ts, std_id),
            )
    elif status == "retired":
        with conn:
            conn.execute(
                """UPDATE standardizations SET status = ?, adopted_version = NULL,
                       updated_at = ? WHERE id = ?""",
                (status, ts, std_id),
            )
    else:
        with conn:
            conn.execute(
                """UPDATE standardizations SET status = ?, adopted_version = NULL,
                       updated_at = ? WHERE id = ?""",
                (status, ts, std_id),
    )


# ---------------------------------------------------------------------------
# Chronology signal-strength assessments
# ---------------------------------------------------------------------------

SIGNAL_STATUSES = ("draft", "completed", "adopted", "retired")


def create_signal_assessment(conn, *, name: str, hypothesis: str,
                             params: dict, sources: dict, members: list,
                             excluded_samples: list, windows: list,
                             note: str = "",
                             standardization_id: int | None = None,
                             standardization_version: int | None = None
                             ) -> int:
    """Persist one assessment (status draft) with its frozen evidence."""
    ts = now_iso()
    with conn:
        cur = conn.execute(
            """INSERT INTO signal_assessments (name, hypothesis, note, status,
                   version, params, sources, members, excluded_samples, windows,
                   reliable_span, adopted_at, standardization_id,
                   standardization_version, created_at, updated_at)
               VALUES (?,?,?, 'draft', 1, ?,?,?,?,?, NULL, NULL, ?,?, ?, ?)""",
            (name, hypothesis, note,
             json.dumps(params, ensure_ascii=False),
             json.dumps(sources, ensure_ascii=False),
             json.dumps(members, ensure_ascii=False),
             json.dumps(excluded_samples, ensure_ascii=False),
             json.dumps(windows, ensure_ascii=False),
             standardization_id, standardization_version, ts, ts),
        )
        return cur.lastrowid


def _signal_row(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["assessment_id"] = d.pop("id")
    for k in ("params", "sources", "members", "excluded_samples", "windows",
              "reliable_span"):
        d[k] = json.loads(d[k]) if d[k] is not None else None
    return d


def get_signal_assessment(conn, assessment_id: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM signal_assessments WHERE id = ?", (assessment_id,),
    ).fetchone()
    return _signal_row(row) if row else None


def get_signal_assessment_by_name(conn, name: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM signal_assessments WHERE name = ?", (name,),
    ).fetchone()
    return _signal_row(row) if row else None


def list_signal_assessments(conn, *, hypothesis: str | None = None,
                            status: str | None = None,
                            sample_id: str | None = None) -> list[dict]:
    """Lightweight assessment list (per-window evidence is summarised)."""
    sql = "SELECT * FROM signal_assessments"
    cond, args = [], []
    if hypothesis:
        cond.append("hypothesis = ?")
        args.append(hypothesis)
    if status:
        cond.append("status = ?")
        args.append(status)
    if sample_id:
        cond.append("members LIKE ?")
        args.append(f'%"{sample_id}"%')
    if cond:
        sql += " WHERE " + " AND ".join(cond)
    sql += " ORDER BY id DESC"
    out = []
    for r in conn.execute(sql, args):
        d = _signal_row(r)
        d["n_windows"] = len(d["windows"])
        d["n_pass"] = sum(1 for w in d["windows"] if w["eps_pass"])
        d["n_members"] = len(d["members"])
        for k in ("sources", "windows"):
            d.pop(k)
        out.append(d)
    return out


def mark_signal_status(conn, assessment_id: int, status: str, *,
                       reliable_span: dict | None = None) -> None:
    ts = now_iso()
    with conn:
        if status == "adopted":
            conn.execute(
                """UPDATE signal_assessments
                   SET status = ?, reliable_span = ?, adopted_at = ?,
                       updated_at = ? WHERE id = ?""",
                (status, json.dumps(reliable_span, ensure_ascii=False),
                 ts, ts, assessment_id),
            )
        elif status == "retired":
            conn.execute(
                """UPDATE signal_assessments SET status = ?, updated_at = ?
                   WHERE id = ?""",
                (status, ts, assessment_id),
            )
        else:
            conn.execute(
                """UPDATE signal_assessments SET status = ?, updated_at = ?
                   WHERE id = ?""",
                (status, ts, assessment_id),
            )
