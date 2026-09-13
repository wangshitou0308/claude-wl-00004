"""Offline cross-dating API for tree-ring laboratories.

A self-contained toolkit (Python standard library only) for ingesting
tree-ring width series, sliding them against a reference chronology,
recording cross-dating hypotheses/locks, and exporting JSON reports.

Modules:
    db          -- SQLite schema and persistence helpers
    validate    -- JSON/CSV parsing and per-sample, per-row validation
    analysis    -- Pearson correlation, sign agreement, narrow rings,
                   chronology statistics and conflict detection
    corrections -- missing-ring / false-ring correction drafts
                   (segmented year mapping, preview, adopt, revoke)
    server      -- http.server based HTTP API
    cli         -- command line entry point (``python -m tree_ring_xdate``)
"""

__version__ = "1.0.0"
