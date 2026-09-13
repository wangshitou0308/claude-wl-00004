"""Correction drafts for missing rings (漏记缺失环) and false rings (误记伪环).

A *correction draft* starts from one candidate offset of a sliding
cross-date run and describes, in measurement order, how the lab thinks the
recorded series deviates from the tree's real growth:

``missing_ring`` -- a calendar year the tree grew no (or an unmeasurable)
    ring that the measurement series skipped.  The event is anchored
    *between* two measurements (``after_seq``) and inserts one or more
    missing calendar years (width 0) at that position.

``false_ring`` -- a recorded measurement that does not correspond to a
    real calendar year (a false / double ring, a duplicated row).  The
    measurement keeps its stored width but is excluded from dating: it
    receives no calendar year and never enters correlations or the master
    chronology.

The event list rebuilds a *segmented year mapping*: segments of
consecutive measured rings separated by the events.  Each segment gets its
own overlap interval, Pearson correlation, sign agreement and shared
narrow-ring years against the reference snapshot; the whole corrected
series is scored as well, and every figure is reported next to the
original candidate's so the change is visible at a glance.

Nothing here mutates the stored series: widths, explicit years and
``raw_payload`` stay byte-identical.  Only *adopting* a draft writes
anything beyond the draft tables -- the rebuilt mapping into the chosen
hypothesis (and thereby the master chronology).

Draft lifecycle::

    POST   /api/corrections                      create draft (version 1)
    POST   /api/corrections/{id}/versions        replace events -> new version
    GET    /api/corrections/{id}                 latest version + evaluation
    GET    /api/corrections/{id}/versions/{v}    one version + evaluation
    GET    /api/corrections/{id}/preview         evaluate any version (?version=)
    POST   /api/corrections/{id}/adopt           apply mapping to a hypothesis
    POST   /api/corrections/{id}/revoke          undo the adoption
    GET    /api/corrections/{id}/compare?a=&b=   diff two versions
    GET    /api/corrections/{id}/download        JSON export (?download=1)

Rejection codes (HTTP 422, each localised to a measurement ``seq``):

    E_EVENT_DUPLICATE      same event twice
    E_EVENT_ORDER          events not in strictly increasing seq order
    E_EVENT_RANGE          seq outside 1..n (0..n for after_seq)
    E_EVENT_VS_MISSING     event lands on an already-missing ring
    E_EVENT_VS_YEAR        rebuilt mapping contradicts an explicit year
    E_SEGMENT_TOO_SHORT    a participating segment keeps < min_overlap
                           overlapping years (adoption only)
"""

from __future__ import annotations

import statistics

from . import analysis, db

EVENT_TYPES = ("missing_ring", "false_ring")
DEFAULT_MIN_OVERLAP = 20


class CorrectionError(Exception):
    """One or more validation failures, each localised to a measurement."""

    def __init__(self, errors: list[dict]):
        super().__init__("; ".join(e["message"] for e in errors))
        self.errors = errors


def _err(code, message, *, seq=None, event=None, extra=None):
    d = {"code": code, "message": message, "seq": seq, "event": event}
    if extra:
        d.update(extra)
    return d


# ---------------------------------------------------------------------------
# Event normalisation / validation
# ---------------------------------------------------------------------------

def _as_int(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if text.lstrip("-").isdigit():
            return int(text)
    return None


def normalize_events(raw_events, n_rings: int) -> list[dict]:
    """Validate and canonicalise the submitted event list.

    Returns events sorted by position, each ``{"type", "after_seq",
    "false_seq", "years"}`` (exactly one of after_seq/false_seq set).
    Raises :class:`CorrectionError` listing *every* problem found.
    """
    errors: list[dict] = []
    events: list[dict] = []
    if not isinstance(raw_events, list) or not raw_events:
        raise CorrectionError([_err(
            "E_NO_EVENTS",
            "events must be a non-empty list of correction events")])

    for i, raw in enumerate(raw_events, start=1):
        if not isinstance(raw, dict):
            errors.append(_err("E_EVENT_SHAPE",
                               f"event #{i} is not a JSON object",
                               event=i))
            continue
        etype = raw.get("type")
        if etype not in EVENT_TYPES:
            errors.append(_err(
                "E_EVENT_TYPE",
                f"event #{i}: type must be one of {EVENT_TYPES}, "
                f"got {etype!r}", event=i))
            continue
        if etype == "missing_ring":
            after = _as_int(raw.get("after_seq"))
            if after is None:
                errors.append(_err(
                    "E_EVENT_RANGE",
                    f"event #{i}: missing_ring needs integer after_seq",
                    event=i))
                continue
            if not 0 <= after <= n_rings:
                errors.append(_err(
                    "E_EVENT_RANGE",
                    f"missing_ring after_seq {after} is outside the sample "
                    f"(allowed 0..{n_rings}: 0 = before the first "
                    f"measurement, {n_rings} = after the last)",
                    seq=after, event=i))
                continue
            years_raw = raw.get("years", 1)
            years = _as_int(years_raw)
            if years is None or years < 1 or years > 100:
                errors.append(_err(
                    "E_EVENT_RANGE",
                    f"event #{i}: missing_ring years must be an integer "
                    f"1..100, got {years_raw!r}", seq=after, event=i))
                continue
            events.append({"type": etype, "after_seq": after,
                           "false_seq": None, "years": years,
                           "event": i})
        else:  # false_ring
            seq = _as_int(raw.get("seq"))
            if seq is None:
                errors.append(_err(
                    "E_EVENT_RANGE",
                    f"event #{i}: false_ring needs integer seq", event=i))
                continue
            if not 1 <= seq <= n_rings:
                errors.append(_err(
                    "E_EVENT_RANGE",
                    f"false_ring seq {seq} is outside the sample "
                    f"(1..{n_rings})", seq=seq, event=i))
                continue
            events.append({"type": etype, "after_seq": None,
                           "false_seq": seq, "years": 0, "event": i})

    # -- duplicates ----------------------------------------------------------
    seen = {}
    for e in events:
        key = (e["type"],
               e["after_seq"] if e["type"] == "missing_ring"
               else e["false_seq"])
        if key in seen:
            errors.append(_err(
                "E_EVENT_DUPLICATE",
                f"duplicate {e['type']} event at measurement "
                f"{key[1]} (events #{seen[key]} and #{e['event']})",
                seq=key[1], event=e["event"]))
        else:
            seen[key] = e["event"]
    # a false ring and a gap at the same position contradict each other
    false_at = {e["false_seq"] for e in events if e["type"] == "false_ring"}
    for e in events:
        if (e["type"] == "missing_ring"
                and e["after_seq"] in false_at):
            errors.append(_err(
                "E_EVENT_DUPLICATE",
                f"measurement {e['after_seq']} is both declared a false "
                f"ring and followed by an inserted missing ring; the two "
                f"events contradict each other",
                seq=e["after_seq"], event=e["event"]))

    # -- ordering ------------------------------------------------------------
    def sort_key(e):
        pos = (e["after_seq"] if e["type"] == "missing_ring"
               else e["false_seq"] - 0.5)
        return pos

    ordered = sorted(events, key=sort_key)
    # check the submitted order is non-decreasing (order contradictions are
    # almost always a sign the experimenter mis-numbered a measurement)
    submitted_positions = [sort_key(e) for e in events]
    for prev, cur, cur_e in zip(submitted_positions,
                                submitted_positions[1:], events[1:]):
        if cur < prev:
            errors.append(_err(
                "E_EVENT_ORDER",
                f"event #{cur_e['event']} (measurement {cur:g}) comes after "
                f"an event at measurement {prev:g}; list events in "
                f"increasing measurement order",
                seq=int(cur) if cur == int(cur) else None,
                event=cur_e["event"]))
    if errors:
        raise CorrectionError(errors)
    return ordered


def check_against_series(events: list[dict], series: dict) -> None:
    """Reject events that clash with the stored series itself."""
    errors = []
    missing_seqs = {r["seq"] for r in series["rings"] if r["missing"]}
    explicit_years = {r["seq"]: r["year"] for r in series["rings"]
                      if r["year"] is not None}
    for e in events:
        if e["type"] == "false_ring" and e["false_seq"] in missing_seqs:
            errors.append(_err(
                "E_EVENT_VS_MISSING",
                f"measurement {e['false_seq']} is already recorded as a "
                f"missing ring (width 0); it cannot also be a false ring",
                seq=e["false_seq"], event=e["event"]))
        if e["type"] == "missing_ring" and e["after_seq"] in missing_seqs:
            errors.append(_err(
                "E_EVENT_VS_MISSING",
                f"measurement {e['after_seq']} is already recorded as a "
                f"missing ring; inserting another gap directly after it "
                f"would merge two missing positions -- widen the existing "
                f"gap instead",
                seq=e["after_seq"], event=e["event"]))
    if errors:
        raise CorrectionError(errors)


def check_against_explicit_years(mapping: list[dict], series: dict) -> None:
    """The rebuilt mapping must not move any explicitly dated ring."""
    errors = []
    by_seq = {m["seq"]: m for m in mapping}
    conflicts = []
    for r in series["rings"]:
        if r["year"] is None:
            continue
        m = by_seq[r["seq"]]
        if m["role"] == "false_ring":
            conflicts.append(_err(
                "E_EVENT_VS_YEAR",
                f"measurement {r['seq']} carries explicit year {r['year']} "
                f"but the draft marks it as a false ring (no calendar "
                f"year)",
                seq=r["seq"]))
        elif m["year"] != r["year"]:
            conflicts.append(_err(
                "E_EVENT_VS_YEAR",
                f"measurement {r['seq']} carries explicit year {r['year']} "
                f"but the corrected mapping assigns it {m['year']}",
                seq=r["seq"]))
    if conflicts:
        # one gap shifts every later explicit year; report the first few
        # precisely and summarise the rest instead of flooding the reply
        errors.extend(conflicts[:20])
        if len(conflicts) > 20:
            errors.append(_err(
                "E_EVENT_VS_YEAR",
                f"... and {len(conflicts) - 20} further explicit-year "
                f"conflicts (measurements "
                f"{conflicts[20]['seq']}..{conflicts[-1]['seq']})",
                seq=conflicts[20]["seq"],
                extra={"n_conflicts": len(conflicts)}))
    if errors:
        raise CorrectionError(errors)


# ---------------------------------------------------------------------------
# Mapping rebuild
# ---------------------------------------------------------------------------

def build_mapping(series: dict, events: list[dict],
                  offset: int) -> list[dict]:
    """Rebuild the year assignment for every stored measurement.

    Returns one entry per measurement (seq order)::

        {"seq", "width", "role": "ring"|"missing"|"false_ring",
         "year": int|None, "segment": int}

    Inserted missing years appear as ``role == "inserted_missing"`` rows
    with ``seq == None`` so they can never be confused with stored
    measurements.
    """
    gaps = {}   # after_seq -> extra missing years
    false_seqs = set()
    for e in events:
        if e["type"] == "missing_ring":
            gaps[e["after_seq"]] = gaps.get(e["after_seq"], 0) + e["years"]
        else:
            false_seqs.add(e["false_seq"])

    mapping: list[dict] = []
    year = offset
    segment = 1

    def emit_gap(after_seq):
        nonlocal year, segment
        for _ in range(gaps.get(after_seq, 0)):
            mapping.append({"seq": None, "width": 0.0,
                            "role": "inserted_missing",
                            "year": year, "segment": segment,
                            "after_seq": after_seq})
            year += 1
        if gaps.get(after_seq):
            segment += 1

    emit_gap(0)  # gap before the first measurement
    for r in series["rings"]:
        seq = r["seq"]
        if seq in false_seqs:
            mapping.append({"seq": seq, "width": r["width"],
                            "role": "false_ring", "year": None,
                            "segment": None})
            segment += 1
            continue
        role = "missing" if r["missing"] else "ring"
        mapping.append({"seq": seq, "width": r["width"],
                        "role": role, "year": year, "segment": segment})
        year += 1
        emit_gap(seq)
    return mapping


def mapping_segments(mapping: list[dict]) -> list[dict]:
    """Group dated entries into consecutive segments (1-based ids)."""
    segments: dict[int, list[dict]] = {}
    for m in mapping:
        if m["segment"] is None or m["year"] is None:
            continue
        segments.setdefault(m["segment"], []).append(m)
    out = []
    for seg_id in sorted(segments):
        rows = segments[seg_id]
        out.append({"segment": seg_id,
                    "start_year": rows[0]["year"],
                    "end_year": rows[-1]["year"],
                    "n_years": len(rows),
                    "n_measured": sum(1 for r in rows
                                      if r["role"] == "ring"),
                    "n_missing": sum(1 for r in rows
                                     if r["role"] != "ring"),
                    "rows": rows})
    return out


# ---------------------------------------------------------------------------
# Evaluation against a reference snapshot
# ---------------------------------------------------------------------------

def _score_block(year_widths: dict, ref_yw: dict, ref_narrow: set,
                 narrow_z: float, narrow_q: float,
                 min_overlap: int) -> dict:
    """Overlap statistics of one corrected block vs the reference."""
    years = sorted(y for y in year_widths if y in ref_yw)
    xs = [year_widths[y] for y in years]
    ys = [ref_yw[y] for y in years]
    block = {
        "overlap_start": years[0] if years else None,
        "overlap_end": years[-1] if years else None,
        "n_overlap": len(xs),
        "meets_min_overlap": len(xs) >= min_overlap,
        "correlation": None,
        "sign_agreement": None,
        "narrow_hits": [],
        "n_narrow_hits": 0,
    }
    if not years:
        return block
    block["correlation"] = analysis.pearson(xs, ys)
    sign, _detail = analysis.sign_agreement(xs, ys)
    block["sign_agreement"] = sign
    t_narrow = analysis.narrow_years(
        {y: year_widths[y] for y in years}, z=narrow_z, quantile=narrow_q)
    block["narrow_hits"] = sorted(y for y in t_narrow if y in ref_narrow)
    block["n_narrow_hits"] = len(block["narrow_hits"])
    return block


def evaluate_mapping(mapping: list[dict], ref_yw: dict, *,
                     narrow_z: float = -1.0, narrow_q: float = 0.1,
                     min_overlap: int = DEFAULT_MIN_OVERLAP) -> dict:
    """Score every segment and the whole corrected series."""
    ref_narrow = analysis.narrow_years(ref_yw, z=narrow_z, quantile=narrow_q)
    segments = []
    for seg in mapping_segments(mapping):
        yw = {r["year"]: r["width"] for r in seg["rows"]}
        block = _score_block(yw, ref_yw, ref_narrow, narrow_z, narrow_q,
                             min_overlap)
        segments.append({
            "segment": seg["segment"],
            "start_year": seg["start_year"],
            "end_year": seg["end_year"],
            "n_years": seg["n_years"],
            "n_measured": seg["n_measured"],
            "n_missing": seg["n_missing"],
            **block,
        })
    whole_yw = {m["year"]: m["width"] for m in mapping
                if m["year"] is not None}
    whole = _score_block(whole_yw, ref_yw, ref_narrow, narrow_z, narrow_q,
                         min_overlap)
    return {"segments": segments, "whole": whole,
            "min_overlap": min_overlap,
            "all_segments_meet_min_overlap":
                all(s["meets_min_overlap"] for s in segments)}


def _delta(new, old):
    if new is None or old is None:
        return None
    return round(new - old, 6)


def diff_vs_candidate(evaluation: dict, candidate: dict | None) -> dict:
    """Change of the whole-series figures relative to the run candidate."""
    whole = evaluation["whole"]
    if not candidate:
        return {"available": False}
    return {
        "available": True,
        "candidate_offset": candidate["offset"],
        "candidate": {
            "overlap_start": candidate["overlap_start"],
            "overlap_end": candidate["overlap_end"],
            "n_overlap": candidate["n_overlap"],
            "correlation": candidate["correlation"],
            "sign_agreement": candidate["sign_agreement"],
            "narrow_hits": candidate["narrow_hits"],
            "n_narrow_hits": candidate["n_narrow_hits"],
        },
        "corrected": {
            "overlap_start": whole["overlap_start"],
            "overlap_end": whole["overlap_end"],
            "n_overlap": whole["n_overlap"],
            "correlation": whole["correlation"],
            "sign_agreement": whole["sign_agreement"],
            "narrow_hits": whole["narrow_hits"],
            "n_narrow_hits": whole["n_narrow_hits"],
        },
        "delta": {
            "n_overlap": (whole["n_overlap"] - candidate["n_overlap"]),
            "correlation": _delta(whole["correlation"],
                                  candidate["correlation"]),
            "sign_agreement": _delta(whole["sign_agreement"],
                                     candidate["sign_agreement"]),
            "n_narrow_hits": (whole["n_narrow_hits"]
                              - candidate["n_narrow_hits"]),
            "narrow_hits_added": sorted(
                set(whole["narrow_hits"]) - set(candidate["narrow_hits"])),
            "narrow_hits_removed": sorted(
                set(candidate["narrow_hits"]) - set(whole["narrow_hits"])),
        },
    }


# ---------------------------------------------------------------------------
# Reference snapshot
# ---------------------------------------------------------------------------

def reference_snapshot(conn, reference: str,
                       hypothesis: str | None) -> dict:
    """Freeze the reference so later previews re-score against the same
    chronology the experimenter saw when drafting the correction."""
    ref_yw, meta = analysis.build_reference(conn, reference, hypothesis)
    return {"meta": meta,
            "years": {str(y): ref_yw[y] for y in sorted(ref_yw)}}


def snapshot_year_widths(snapshot: dict) -> dict:
    return {int(y): w for y, w in snapshot["years"].items()}


# ---------------------------------------------------------------------------
# Draft assembly helpers
# ---------------------------------------------------------------------------

def version_public(row: dict, *, with_mapping=False) -> dict:
    """JSON view of one stored draft version."""
    out = {
        "draft_id": row["draft_id"],
        "version": row["version"],
        "sample_id": row["sample_id"],
        "offset": row["offset"],
        "run_id": row["run_id"],
        "reference": row["reference"],
        "hypothesis": row["hypothesis"],
        "min_overlap": row["min_overlap"],
        "narrow_z": row["narrow_z"],
        "narrow_q": row["narrow_q"],
        "events": row["events"],
        "note": row["note"],
        "status": row["status"],
        "created_at": row["created_at"],
    }
    if with_mapping:
        out["mapping"] = row.get("mapping")
        out["evaluation"] = row.get("evaluation")
        out["changes_vs_candidate"] = row.get("changes")
        out["reference_snapshot"] = row.get("snapshot")
        out["candidate"] = row.get("candidate")
    return out


def evaluate_version(conn, row: dict) -> dict:
    """Attach mapping/evaluation/diff to a draft-version row (mutates a
    copy; nothing is written back)."""
    series = db.get_series(conn, row["sample_id"])
    mapping = build_mapping(series, row["events"], row["offset"])
    ref_yw = snapshot_year_widths(row["snapshot"])
    evaluation = evaluate_mapping(mapping, ref_yw,
                                  narrow_z=row["narrow_z"],
                                  narrow_q=row["narrow_q"],
                                  min_overlap=row["min_overlap"])
    row = dict(row)
    row["mapping"] = mapping
    row["evaluation"] = evaluation
    row["changes"] = diff_vs_candidate(evaluation, row.get("candidate"))
    return row


def create_draft(conn, *, sample_id: str, offset: int, events: list[dict],
                 run_id=None, reference: str = "master",
                 hypothesis: str | None = None,
                 min_overlap: int = DEFAULT_MIN_OVERLAP,
                 narrow_z: float = -1.0, narrow_q: float = 0.1,
                 note: str = "") -> dict:
    """Validate, evaluate and persist a new correction draft (version 1)."""
    series = db.get_series(conn, sample_id)
    if series is None:
        raise KeyError(f"sample not found: {sample_id}")
    n_rings = len(series["rings"])
    ev = normalize_events(events, n_rings)
    check_against_series(ev, series)
    mapping = build_mapping(series, ev, offset)
    check_against_explicit_years(mapping, series)

    candidate = None
    if run_id is not None:
        candidate = db.get_candidate(conn, run_id, offset)
        if candidate is None:
            raise KeyError(
                f"run {run_id} has no candidate at offset {offset}")

    snapshot = reference_snapshot(conn, reference, hypothesis)
    ref_yw = snapshot_year_widths(snapshot)
    evaluation = evaluate_mapping(mapping, ref_yw, narrow_z=narrow_z,
                                  narrow_q=narrow_q, min_overlap=min_overlap)
    changes = diff_vs_candidate(evaluation, candidate)
    draft_id = db.save_correction(
        conn, sample_id=sample_id, offset=offset, events=ev, run_id=run_id,
        reference=reference, hypothesis=hypothesis, min_overlap=min_overlap,
        narrow_z=narrow_z, narrow_q=narrow_q, note=note, snapshot=snapshot,
        candidate=candidate)
    row = db.get_correction_version(conn, draft_id, 1)
    row["candidate"] = candidate
    row["snapshot"] = snapshot
    row["mapping"] = mapping
    row["evaluation"] = evaluation
    row["changes"] = changes
    return version_public(row, with_mapping=True)


def add_version(conn, draft_id: int, *, events: list[dict],
                note: str = "") -> dict:
    """Append a new version with a replaced event list.

    Allowed even while the draft is adopted: the live mapping stays
    pinned to the adopted version until an explicit re-adopt, so
    experimenters can prepare and compare alternatives safely.
    """
    head = db.get_correction(conn, draft_id)
    if head is None:
        raise KeyError(f"correction draft not found: {draft_id}")
    series = db.get_series(conn, head["sample_id"])
    ev = normalize_events(events, len(series["rings"]))
    check_against_series(ev, series)
    mapping = build_mapping(series, ev, head["offset"])
    check_against_explicit_years(mapping, series)
    ref_yw = snapshot_year_widths(head["snapshot"])
    evaluation = evaluate_mapping(mapping, ref_yw, narrow_z=head["narrow_z"],
                                  narrow_q=head["narrow_q"],
                                  min_overlap=head["min_overlap"])
    candidate = head.get("candidate")
    changes = diff_vs_candidate(evaluation, candidate)
    version = db.save_correction_version(
        conn, draft_id, events=ev, note=note)
    row = db.get_correction_version(conn, draft_id, version)
    row["candidate"] = candidate
    row["snapshot"] = head["snapshot"]
    row["mapping"] = mapping
    row["evaluation"] = evaluation
    row["changes"] = changes
    return version_public(row, with_mapping=True)


def adopt_draft(conn, draft_id: int, *, hypothesis: str,
                version: int | None = None) -> dict:
    """Apply a draft's rebuilt mapping to a dating hypothesis.

    Every participating segment must keep at least ``min_overlap``
    overlapping years against the reference snapshot; otherwise adoption
    is refused and the offending segments are localised by measurement
    seq.
    """
    head = db.get_correction(conn, draft_id)
    if head is None:
        raise KeyError(f"correction draft not found: {draft_id}")
    if head["status"] == "adopted":
        raise CorrectionError([_err(
            "E_DRAFT_ADOPTED",
            f"draft {draft_id} is already adopted in hypothesis "
            f"{head['adopted_hypothesis']!r}; revoke it first")])
    row = db.get_correction_version(conn, draft_id,
                                    version or head["latest_version"])
    series = db.get_series(conn, head["sample_id"])
    mapping = build_mapping(series, row["events"], row["offset"])
    ref_yw = snapshot_year_widths(head["snapshot"])
    evaluation = evaluate_mapping(mapping, ref_yw, narrow_z=row["narrow_z"],
                                  narrow_q=row["narrow_q"],
                                  min_overlap=row["min_overlap"])
    short = [s for s in evaluation["segments"] if not s["meets_min_overlap"]]
    if short:
        errors = []
        for s in short:
            seqs = [r["seq"] for r in mapping
                    if r["segment"] == s["segment"] and r["seq"] is not None]
            errors.append(_err(
                "E_SEGMENT_TOO_SHORT",
                f"segment {s['segment']} (years {s['start_year']}.."
                f"{s['end_year']}) keeps only {s['n_overlap']} overlapping "
                f"years against the reference, below min_overlap "
                f"{row['min_overlap']}; affected measurements "
                f"{seqs[0]}..{seqs[-1] if seqs else seqs[0]}",
                seq=seqs[0] if seqs else None,
                extra={"segment": s["segment"],
                       "n_overlap": s["n_overlap"],
                       "seqs": seqs}))
        raise CorrectionError(errors)

    if db.get_hypothesis(conn, hypothesis, with_locks=False) is None:
        db.create_hypothesis(conn, hypothesis,
                             "auto-created by correction adoption")
    db.save_correction_mapping(conn, draft_id, row["version"], mapping,
                               hypothesis=hypothesis)
    db.mark_correction_status(conn, draft_id, "adopted",
                              hypothesis=hypothesis,
                              adopted_version=row["version"])
    return {"draft_id": draft_id, "version": row["version"],
            "sample_id": head["sample_id"], "hypothesis": hypothesis,
            "status": "adopted",
            "evaluation": evaluation,
            "mapping": mapping}


def revoke_draft(conn, draft_id: int) -> dict:
    head = db.get_correction(conn, draft_id)
    if head is None:
        raise KeyError(f"correction draft not found: {draft_id}")
    if head["status"] != "adopted":
        raise CorrectionError([_err(
            "E_DRAFT_NOT_ADOPTED",
            f"draft {draft_id} is not adopted (status "
            f"{head['status']!r}); nothing to revoke")])
    db.clear_correction_mapping(conn, draft_id)
    db.mark_correction_status(conn, draft_id, "revoked")
    return {"draft_id": draft_id, "sample_id": head["sample_id"],
            "status": "revoked",
            "hypothesis": head["adopted_hypothesis"],
            "adopted_version": head["adopted_version"]}


def compare_versions(conn, draft_id: int, a: int, b: int) -> dict:
    head = db.get_correction(conn, draft_id)
    if head is None:
        raise KeyError(f"correction draft not found: {draft_id}")
    rows = {}
    for v in (a, b):
        row = db.get_correction_version(conn, draft_id, v)
        if row is None:
            raise KeyError(f"draft {draft_id} has no version {v}")
        row["snapshot"] = head["snapshot"]
        row["candidate"] = head.get("candidate")
        rows[v] = evaluate_version(conn, row)

    def ev_key(e):
        return (e["type"],
                e["after_seq"] if e["type"] == "missing_ring"
                else e["false_seq"],
                e["years"])

    ev_a = {ev_key(e): e for e in rows[a]["events"]}
    ev_b = {ev_key(e): e for e in rows[b]["events"]}
    added = [ev_b[k] for k in ev_b if k not in ev_a]
    removed = [ev_a[k] for k in ev_a if k not in ev_b]
    whole_a = rows[a]["evaluation"]["whole"]
    whole_b = rows[b]["evaluation"]["whole"]
    return {
        "draft_id": draft_id,
        "sample_id": head["sample_id"],
        "version_a": a, "version_b": b,
        "events_added": added,
        "events_removed": removed,
        "whole_a": whole_a,
        "whole_b": whole_b,
        "delta": {
            "n_overlap": whole_b["n_overlap"] - whole_a["n_overlap"],
            "correlation": _delta(whole_b["correlation"],
                                  whole_a["correlation"]),
            "sign_agreement": _delta(whole_b["sign_agreement"],
                                     whole_a["sign_agreement"]),
            "n_narrow_hits": (whole_b["n_narrow_hits"]
                              - whole_a["n_narrow_hits"]),
        },
        "segments_a": rows[a]["evaluation"]["segments"],
        "segments_b": rows[b]["evaluation"]["segments"],
    }


def draft_report(conn, draft_id: int) -> dict:
    """Full JSON export: draft metadata, every version, evaluations."""
    head = db.get_correction(conn, draft_id)
    if head is None:
        raise KeyError(f"correction draft not found: {draft_id}")
    versions = []
    for v in range(1, head["latest_version"] + 1):
        row = db.get_correction_version(conn, draft_id, v)
        row["snapshot"] = head["snapshot"]
        row["candidate"] = head.get("candidate")
        versions.append(version_public(evaluate_version(conn, row),
                                       with_mapping=True))
    return {
        "report_type": "tree_ring_correction_draft",
        "generated_at": db.now_iso(),
        "draft": {k: head[k] for k in
                  ("draft_id", "sample_id", "offset", "run_id", "reference",
                   "hypothesis", "min_overlap", "narrow_z", "narrow_q",
                   "status", "adopted_hypothesis", "adopted_version",
                   "latest_version", "created_at", "updated_at")},
        "reference_snapshot": head["snapshot"],
        "candidate": head.get("candidate"),
        "versions": versions,
    }
