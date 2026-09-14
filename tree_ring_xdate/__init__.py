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
    stability   -- local stability checks of locked placements
                   (sliding windows, misplacement flags, read-only)
    standardization -- per-sample detrending plans (mean / negative
                   exponential / centred moving average), versioned and
                   frozen at adoption for chronologies and jobs
    signal       -- chronology signal-strength assessments (sample depth,
                   pair correlation, Rbar, EPS, jackknife), frozen basis,
                   draft|completed|adopted|retired lifecycle and a reliable
                   interval that adopted chronologies/matches are pinned to
    server      -- http.server based HTTP API
    cli         -- command line entry point (``python -m tree_ring_xdate``)
"""

__version__ = "1.0.0"
