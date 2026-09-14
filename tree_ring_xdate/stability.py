"""Local stability checks for already-dated results (局部稳定性检查).

A *stability check* re-examines placements that are already locked in a
dating hypothesis (or applied through an adopted correction draft) and
asks a narrow question: does every local window of the dated series
still match the reference best at its current position, or do
consecutive windows consistently favour the same small displacement?

Workflow
--------
The experimenter starts a check for one locked sample or for every
sample placed in a hypothesis, choosing

* ``window``           -- length of the sliding window (years)
* ``step``             -- distance between consecutive window starts
* ``min_valid_years``  -- minimum mapped years a window must keep
* ``run_threshold``    -- consecutive windows needed for a flag
* ``search_radius``    -- shifts examined on both sides of the current
                          position (± years)

The system slices the *adopted* year mapping (lock or adopted correction
draft) into overlapping windows; the final window's ``end_year`` is
clamped to the last year actually present in the mapping, so no window
or flag ever reports years the mapping does not have.  Missing years
keep their zero width and participate in the statistics; false rings
never received a calendar year and therefore never enter them.  Every
window is compared against the chosen reference at the current position
and at each neighbouring shift; Pearson correlation, sign agreement and
shared narrow years are reported per shift, and shifts scoring within
``tolerance`` of the best are kept side by side.  Only shifts sharing at
least ``min_valid_years`` common years with the reference may conclude
anything: a window where *no* shift qualifies is marked
``insufficient_coverage`` with the best overlap as evidence.

The reference must be independent of the check target.  Against the
master chronology the target itself is always excluded
(leave-one-out); a designated reference sample must not be a check
target (``E_SELF_REFERENCE``).  A target left without any independent
reference members is skipped, and when no target can be checked at all
the check is refused (``E_NO_INDEPENDENT_REFERENCE``) -- there is no
such thing as a stability conclusion without an independent reference.

When at least ``run_threshold`` consecutive windows favour the same
non-zero displacement, the affected interval is flagged together with
the measurement sequence numbers involved.

A check never mutates anything: locks, correction drafts and the stored
series stay untouched.  The parameters, the adopted sample mappings and
a frozen reference snapshot are persisted with the check job, so a later
review can tell exactly which evidence a flag rests on.  When the live
reference or a sample mapping has drifted since creation, the check only
says so (``reference_status`` / ``mapping_status``) and keeps reporting
against the frozen snapshot -- insufficient coverage or a stale
reference is explained, never "repaired".

Endpoints::

    POST /api/stability                  create a check
    GET  /api/stability                  list checks (?hypothesis=&sample_id=)
    GET  /api/stability/{id}             detail; window filters:
                                         ?sample_id=&year_from=&year_to=
                                         &shift=&status=
    GET  /api/stability/compare?a=&b=    diff two checks
    GET  /api/stability/{id}/download    JSON export (?download=0 to inline)
"""

from __future__ import annotations

from . import analysis, corrections, db

DEFAULT_WINDOW = 30
DEFAULT_STEP = 10
DEFAULT_MIN_VALID_YEARS = 15
DEFAULT_RUN_THRESHOLD = 3
DEFAULT_SEARCH_RADIUS = 3
DEFAULT_TOLERANCE = 0.05

READ_ONLY_NOTE = ("stability checks never modify locks or correction "
                  "drafts; flags and staleness notes are advisory only")


class StabilityError(Exception):
    """One or more validation failures when creating a check (HTTP 422)."""

    def __init__(self, errors: list[dict]):
        super().__init__("; ".join(e["message"] for e in errors))
        self.errors = errors


def _err(code, message, **extra):
    return {"code": code, "message": message, **extra}


# ---------------------------------------------------------------------------
# Parameter validation
# ---------------------------------------------------------------------------

def _validate_params(window, step, min_valid_years, run_threshold,
                     search_radius, tolerance) -> list[dict]:
    errors = []
    if not isinstance(window, int) or window < 5:
        errors.append(_err("E_PARAM", "window must be an integer >= 5",
                           param="window", value=window))
    if not isinstance(step, int) or step < 1:
        errors.append(_err("E_PARAM", "step must be an integer >= 1",
                           param="step", value=step))
    if (not isinstance(min_valid_years, int) or min_valid_years < 3
            or (isinstance(window, int) and min_valid_years > window)):
        errors.append(_err(
            "E_PARAM",
            "min_valid_years must be an integer between 3 and window",
            param="min_valid_years", value=min_valid_years))
    if not isinstance(run_threshold, int) or run_threshold < 2:
        errors.append(_err("E_PARAM",
                           "run_threshold must be an integer >= 2",
                           param="run_threshold", value=run_threshold))
    if (not isinstance(search_radius, int)
            or not 1 <= search_radius <= 25):
        errors.append(_err("E_PARAM",
                           "search_radius must be an integer 1..25",
                           param="search_radius", value=search_radius))
    if not isinstance(tolerance, (int, float)) or not 0 <= tolerance <= 1:
        errors.append(_err("E_PARAM", "tolerance must be within 0..1",
                           param="tolerance", value=tolerance))
    return errors


# ---------------------------------------------------------------------------
# Adopted year mapping of a sample inside a hypothesis
# ---------------------------------------------------------------------------

def adopted_mapping(conn, hypothesis: str, sample_id: str) -> dict | None:
    """The year mapping currently in force for ``sample_id``.

    An adopted correction draft (segmented mapping) wins over a plain
    lock.  Returns ``{"placement", ..., "mapping"}`` where mapping rows
    are ``{"year", "width", "seq", "role"}``; false rings carry no
    calendar year and are therefore absent.  ``None`` when the sample is
    neither locked nor corrected in this hypothesis.
    """
    corr = db.correction_maps_of(conn, hypothesis).get(sample_id)
    if corr:
        mapping = [{"year": r["year"], "width": r["width"], "seq": r["seq"],
                    "role": r["role"]} for r in corr]
        return {"placement": "correction",
                "draft_ids": sorted({r["draft_id"] for r in corr}),
                "mapping": mapping}
    locks = db.locks_of(conn, hypothesis)
    if sample_id in locks:
        series = db.get_series(conn, sample_id)
        if series is None:
            return None
        offset = locks[sample_id]
        mapping = [{"year": offset + r["seq"] - 1, "width": r["width"],
                    "seq": r["seq"],
                    "role": "missing" if r["missing"] else "ring"}
                   for r in series["rings"]]
        return {"placement": "lock", "offset": offset, "mapping": mapping}
    return None


def _targets(conn, hypothesis: str, sample_id: str | None) -> list[str]:
    """Samples whose placement this hypothesis actually decides."""
    locks = db.locks_of(conn, hypothesis)
    corr_maps = db.correction_maps_of(conn, hypothesis)
    placed = sorted(set(locks) | set(corr_maps))
    if sample_id is None:
        return placed
    if db.get_series(conn, sample_id) is None:
        raise KeyError(f"sample not found: {sample_id}")
    if sample_id not in placed:
        raise KeyError(
            f"sample {sample_id!r} has no lock or adopted correction in "
            f"hypothesis {hypothesis!r}")
    return [sample_id]


# ---------------------------------------------------------------------------
# Window slicing
# ---------------------------------------------------------------------------

def cut_windows(mapping: list[dict], window: int, step: int) -> list[dict]:
    """Slice the adopted year mapping into overlapping windows.

    Missing years keep their zero width and stay in the rows; false
    rings never received a calendar year, so they cannot appear.  The
    final window may be shorter than ``window`` when the mapping ends;
    its ``end_year`` is clamped to the last year actually present, so
    windows (and the flags derived from them) never report years the
    mapping does not have.
    """
    years = [m["year"] for m in mapping]
    if not years:
        return []
    by_year = {m["year"]: m for m in mapping}
    y0, y1 = min(years), max(years)
    out = []
    index = 1
    start = y0
    while start <= y1:
        end = start + window - 1
        rows = [by_year[y] for y in range(start, end + 1) if y in by_year]
        if rows:
            seqs = [r["seq"] for r in rows if r["seq"] is not None]
            out.append({
                "window_index": index,
                "start_year": start,
                "end_year": rows[-1]["year"],   # clamp to years that exist
                "n_years": len(rows),
                "n_valid": len(rows),   # every mapped year participates
                "rows": rows,
                "seq_start": min(seqs) if seqs else None,
                "seq_end": max(seqs) if seqs else None,
            })
            index += 1
        start += step
    return out


# ---------------------------------------------------------------------------
# Sliding comparison of one window against the reference
# ---------------------------------------------------------------------------

def score_window(rows: list[dict], ref_yw: dict, ref_narrow: set, *,
                 search_radius: int, min_valid_years: int,
                 narrow_z: float, narrow_q: float,
                 tolerance: float) -> dict:
    """Slide one window around its current position.

    Shift ``d`` means: the sample years of this window would have to
    move by ``+d`` years to match the reference (``d = 0`` is the locked
    position).  Only shifts sharing at least ``min_valid_years`` common
    years with the reference are qualified to conclude anything; shifts
    scoring within ``tolerance`` of the best qualified correlation are
    kept side by side in ``tied_shifts``.  ``sufficient`` is False when
    no shift qualifies -- the per-shift candidates are still returned as
    evidence of *why* the window cannot be judged.
    """
    years = [r["year"] for r in rows]
    widths = [r["width"] for r in rows]
    t_narrow = analysis.narrow_years(dict(zip(years, widths)),
                                     z=narrow_z, quantile=narrow_q)
    candidates = []
    for d in range(-search_radius, search_radius + 1):
        xs, ys = [], []
        for y, w in zip(years, widths):
            ry = y + d
            if ry in ref_yw:
                xs.append(w)
                ys.append(ref_yw[ry])
        cand = {"shift": d, "n_overlap": len(xs), "correlation": None,
                "sign_agreement": None, "narrow_hits": [],
                "n_narrow_hits": 0}
        if len(xs) >= 3:
            cand["correlation"] = analysis.pearson(xs, ys)
            sign, _detail = analysis.sign_agreement(xs, ys)
            cand["sign_agreement"] = sign
            hits = sorted(y + d for y in t_narrow
                          if y + d in ref_yw and y + d in ref_narrow)
            cand["narrow_hits"] = hits
            cand["n_narrow_hits"] = len(hits)
        candidates.append(cand)

    qualified = [c for c in candidates
                 if c["n_overlap"] >= min_valid_years
                 and c["correlation"] is not None]
    best_shift = None
    tied: list[int] = []
    if qualified:
        # best correlation first; near-ties prefer the smaller |shift|
        qualified.sort(key=lambda c: (-c["correlation"], abs(c["shift"]),
                                      c["shift"]))
        best = qualified[0]
        best_shift = best["shift"]
        tied = sorted(c["shift"] for c in qualified
                      if c["correlation"] >= best["correlation"] - tolerance)
    current = next(c for c in candidates if c["shift"] == 0)
    return {"sufficient": bool(qualified),
            "max_overlap": max((c["n_overlap"] for c in candidates),
                               default=0),
            "best_shift": best_shift, "tied_shifts": tied,
            "current": current, "candidates": candidates}


# ---------------------------------------------------------------------------
# Misplacement flags: consecutive windows favouring one displacement
# ---------------------------------------------------------------------------

def detect_flags(sample_id: str, windows: list[dict],
                 run_threshold: int) -> list[dict]:
    """Flag runs of >= ``run_threshold`` adjacent windows whose best
    shift is the *same* non-zero displacement.

    Windows with insufficient coverage break a run: the evidence chain
    is only as strong as its weakest gap.
    """
    flags = []
    run: list[dict] = []

    def flush():
        if len(run) < run_threshold:
            return
        shift = run[0]["best_shift"]
        seq_lo = [w["seq_start"] for w in run if w["seq_start"] is not None]
        seq_hi = [w["seq_end"] for w in run if w["seq_end"] is not None]
        flags.append({
            "sample_id": sample_id,
            "shift": shift,
            "start_year": run[0]["start_year"],
            "end_year": run[-1]["end_year"],
            "n_windows": len(run),
            "window_indexes": [w["window_index"] for w in run],
            "seq_start": min(seq_lo) if seq_lo else None,
            "seq_end": max(seq_hi) if seq_hi else None,
            "message": (
                f"{len(run)} consecutive windows "
                f"({run[0]['start_year']}..{run[-1]['end_year']}) of "
                f"{sample_id} favour shift {shift:+d} yr; measurements "
                f"{min(seq_lo) if seq_lo else '?'}.."
                f"{max(seq_hi) if seq_hi else '?'} may be misplaced"),
        })

    for w in windows:
        contiguous = bool(run) and \
            w["window_index"] == run[-1]["window_index"] + 1
        if (w["status"] == "ok" and w["best_shift"] not in (None, 0)
                and contiguous
                and w["best_shift"] == run[-1]["best_shift"]):
            run.append(w)
        else:
            flush()
            run = []
            if w["status"] == "ok" and w["best_shift"] not in (None, 0):
                run = [w]
    flush()
    return flags


# ---------------------------------------------------------------------------
# Check creation
# ---------------------------------------------------------------------------

def _is_master(reference) -> bool:
    return reference in (None, "", "master", "MASTER", "@master")


def _reference_snapshot_for(conn, reference: str, hypothesis: str,
                            sample_id: str,
                            std: dict | None = None) -> dict:
    """Frozen reference for one target sample.

    Against the master chronology the target itself is excluded
    (leave-one-out): a stability check must never compare a sample with
    a chronology it belongs to.  With an adopted standardization plan
    the leave-one-out master is rebuilt from the other members' frozen
    indices.
    """
    exclude = {sample_id} if _is_master(reference) else None
    return corrections.reference_snapshot(conn, reference, hypothesis,
                                          exclude=exclude, std=std)


def create_check(conn, *, hypothesis: str, sample_id: str | None = None,
                 reference: str = "master", window: int = DEFAULT_WINDOW,
                 step: int = DEFAULT_STEP,
                 min_valid_years: int = DEFAULT_MIN_VALID_YEARS,
                 run_threshold: int = DEFAULT_RUN_THRESHOLD,
                 search_radius: int = DEFAULT_SEARCH_RADIUS,
                 tolerance: float = DEFAULT_TOLERANCE,
                 narrow_z: float = -1.0, narrow_q: float = 0.1,
                 note: str = "", std: dict | None = None) -> dict:
    """Run a stability check and persist job + evidence in SQLite."""
    errors = _validate_params(window, step, min_valid_years, run_threshold,
                              search_radius, tolerance)
    if errors:
        raise StabilityError(errors)
    if db.get_hypothesis(conn, hypothesis, with_locks=False) is None:
        raise KeyError(f"hypothesis not found: {hypothesis}")
    targets = _targets(conn, hypothesis, sample_id)
    if not targets:
        raise StabilityError([_err(
            "E_NO_TARGETS",
            f"hypothesis {hypothesis!r} has no locked or corrected "
            f"samples to check")])
    master = _is_master(reference)
    if not master and reference in targets and len(targets) == 1:
        raise StabilityError([_err(
            "E_SELF_REFERENCE",
            f"reference {reference!r} is the check target itself; a "
            f"sample cannot be stability-checked against its own "
            f"series -- choose master or an independent reference "
            f"sample")])

    # One frozen reference per target.  Against the master chronology the
    # target itself is always excluded (leave-one-out); a target left
    # without any independent reference is skipped, never "checked"
    # against itself.
    snapshots: dict[str, dict] = {}
    skipped: dict[str, str] = {}
    for sid in targets:
        if not master and sid == reference:
            skipped[sid] = (
                f"target is the designated reference {reference!r} "
                f"itself; no independent reference to compare against")
            continue
        snap = _reference_snapshot_for(conn, reference, hypothesis, sid,
                                       std=std)
        if not snap["years"]:
            skipped[sid] = (
                "no independent reference members remain after "
                "excluding the target from the master chronology"
                if master else
                f"reference {reference!r} has no dated years")
            continue
        snapshots[sid] = snap
    checked = [sid for sid in targets if sid in snapshots]
    if not checked:
        raise StabilityError([_err(
            "E_NO_INDEPENDENT_REFERENCE",
            "no independent reference members remain for any check "
            "target; a stability conclusion is impossible")])

    sample_maps: dict[str, dict] = {}
    all_windows: list[dict] = []
    all_flags: list[dict] = []
    for sid in targets:
        adopted = adopted_mapping(conn, hypothesis, sid)
        if adopted is None:
            raise KeyError(f"sample not found: {sid}")
        if sid in skipped:
            sample_maps[sid] = {**adopted, "skipped": True,
                                "reason": skipped[sid]}
            continue
        sample_maps[sid] = adopted
        ref_yw = corrections.snapshot_year_widths(snapshots[sid])
        ref_narrow = analysis.narrow_years(ref_yw, z=narrow_z,
                                           quantile=narrow_q)
        windows = []
        for w in cut_windows(adopted["mapping"], window, step):
            rec = {"sample_id": sid,
                   "window_index": w["window_index"],
                   "start_year": w["start_year"],
                   "end_year": w["end_year"],
                   "n_years": w["n_years"],
                   "n_valid": w["n_valid"],
                   "seq_start": w["seq_start"],
                   "seq_end": w["seq_end"]}
            if w["n_valid"] < min_valid_years:
                rec.update({
                    "status": "insufficient_coverage",
                    "reason": (f"window keeps {w['n_valid']} mapped "
                               f"years, below min_valid_years "
                               f"{min_valid_years}; comparison skipped"),
                    "best_shift": None, "tied_shifts": [],
                    "current": None, "candidates": []})
                windows.append(rec)
                continue
            scored = score_window(w["rows"], ref_yw, ref_narrow,
                                  search_radius=search_radius,
                                  min_valid_years=min_valid_years,
                                  narrow_z=narrow_z, narrow_q=narrow_q,
                                  tolerance=tolerance)
            if not scored["sufficient"]:
                rec.update({
                    "status": "insufficient_coverage",
                    "reason": (f"no shift within ±{search_radius} yr "
                               f"reaches min_valid_years "
                               f"{min_valid_years} common years with "
                               f"the reference (best overlap "
                               f"{scored['max_overlap']}); comparison "
                               f"skipped"),
                    "best_shift": None, "tied_shifts": [],
                    "current": scored["current"],
                    "candidates": scored["candidates"]})
            else:
                rec.update({"status": "ok", "reason": None,
                            "best_shift": scored["best_shift"],
                            "tied_shifts": scored["tied_shifts"],
                            "current": scored["current"],
                            "candidates": scored["candidates"]})
            windows.append(rec)
        all_windows.extend(windows)
        all_flags.extend(detect_flags(sid, windows, run_threshold))

    params = {"window": window, "step": step,
              "min_valid_years": min_valid_years,
              "run_threshold": run_threshold,
              "search_radius": search_radius,
              "tolerance": tolerance,
              "narrow_z": narrow_z, "narrow_q": narrow_q,
              "standardization_id": std["id"] if std else None,
              "standardization_version": std["version"] if std else None}
    check_id = db.save_stability_check(
        conn, hypothesis=hypothesis, sample_id=sample_id,
        reference=reference, params=params, note=note, snapshot=snapshots,
        sample_maps=sample_maps, windows=all_windows, flags=all_flags)
    return check_detail(conn, check_id)


# ---------------------------------------------------------------------------
# Staleness: frozen snapshot vs the live reference / mappings
# ---------------------------------------------------------------------------

def _same_yw(a: dict, b: dict, tol: float = 1e-9) -> bool:
    if set(a) != set(b):
        return False
    return all(abs(a[y] - b[y]) <= tol for y in a)


def _mapping_equal(a: list[dict], b: list[dict]) -> bool:
    ka = {(m["year"], m["seq"]): (m["width"], m["role"]) for m in a}
    kb = {(m["year"], m["seq"]): (m["width"], m["role"]) for m in b}
    return ka == kb


def _reference_status(conn, row: dict) -> dict:
    """Frozen per-target snapshots vs the references as they are now."""
    from . import standardization as std_mod
    std_param = row["params"].get("standardization_id")
    std_live = None
    if std_param is not None:
        try:
            std_live = std_mod.resolve_adopted(
                conn, std_param,
                row["params"].get("standardization_version"),
                hypothesis=row["hypothesis"])
        except (KeyError, std_mod.StandardizationError) as e:
            std_live = None
    per = {}
    for sid in sorted(row["snapshot"]):
        frozen = corrections.snapshot_year_widths(row["snapshot"][sid])
        try:
            now = _reference_snapshot_for(conn, row["reference"],
                                          row["hypothesis"], sid,
                                          std=std_live)
            now_yw = corrections.snapshot_year_widths(now)
        except (KeyError, ValueError) as e:
            per[sid] = {"stale": True,
                        "reason": (f"reference {row['reference']!r} can "
                                   f"no longer be built ({e}); the "
                                   f"results are scored against the "
                                   f"snapshot frozen at creation")}
            continue
        if _same_yw(frozen, now_yw):
            per[sid] = {"stale": False, "reason": None}
        else:
            per[sid] = {"stale": True,
                        "reason": ("the live reference differs from the "
                                   "frozen snapshot (locks or "
                                   "corrections changed since "
                                   "creation); the results are scored "
                                   "against the snapshot")}
    stale = any(p["stale"] for p in per.values())
    reasons = [f"{sid}: {p['reason']}" for sid, p in per.items()
               if p["stale"]]
    return {"stale": stale,
            "reason": "; ".join(reasons) if reasons else None,
            "per_sample": per}


def _mapping_status(conn, row: dict, sample_id: str) -> dict:
    snap = row["sample_maps"].get(sample_id)
    if snap and snap.get("skipped"):
        return {"stale": False, "skipped": True,
                "placement": snap.get("placement"),
                "reason": snap["reason"]}
    current = adopted_mapping(conn, row["hypothesis"], sample_id)
    if current is None:
        return {"stale": True,
                "placement": (snap or {}).get("placement"),
                "reason": ("sample is no longer locked or corrected in "
                           "this hypothesis")}
    same = (current["placement"] == snap["placement"]
            and _mapping_equal(current["mapping"], snap["mapping"]))
    return {"stale": not same,
            "placement": current["placement"],
            "reason": None if same else
            "the adopted year mapping changed since the check was "
            "created"}


# ---------------------------------------------------------------------------
# Reading a check back (with window filters)
# ---------------------------------------------------------------------------

def _filter_windows(windows: list[dict], f: dict) -> list[dict]:
    out = windows
    if f.get("sample_id"):
        out = [w for w in out if w["sample_id"] == f["sample_id"]]
    if f.get("year_from") is not None:
        out = [w for w in out if w["end_year"] >= f["year_from"]]
    if f.get("year_to") is not None:
        out = [w for w in out if w["start_year"] <= f["year_to"]]
    if f.get("shift") is not None:
        out = [w for w in out if w["best_shift"] == f["shift"]]
    if f.get("status"):
        out = [w for w in out if w["status"] == f["status"]]
    return out


def check_detail(conn, check_id: int, *, filters: dict | None = None
                 ) -> dict:
    row = db.get_stability_check(conn, check_id)
    if row is None:
        raise KeyError(f"stability check not found: {check_id}")
    f = filters or {}
    windows = _filter_windows(row["windows"], f)
    flags = row["flags"]
    if f.get("sample_id"):
        flags = [fl for fl in flags if fl["sample_id"] == f["sample_id"]]
    skipped = [{"sample_id": sid, "reason": m["reason"]}
               for sid, m in sorted(row["sample_maps"].items())
               if m.get("skipped")]
    return {
        "check_id": row["check_id"],
        "hypothesis": row["hypothesis"],
        "sample_id": row["sample_id"],
        "reference": row["reference"],
        "params": row["params"],
        "note": row["note"],
        "created_at": row["created_at"],
        "targets": sorted(row["sample_maps"]),
        "skipped_targets": skipped,
        "n_windows": len(row["windows"]),
        "n_ok": sum(1 for w in row["windows"] if w["status"] == "ok"),
        "n_insufficient": sum(1 for w in row["windows"]
                              if w["status"] == "insufficient_coverage"),
        "n_flags": len(row["flags"]),
        "reference_status": _reference_status(conn, row),
        "mapping_status": {sid: _mapping_status(conn, row, sid)
                           for sid in sorted(row["sample_maps"])},
        "filters": dict(f),
        "windows": windows,
        "flags": flags,
        "read_only": True,
        "read_only_note": READ_ONLY_NOTE,
    }


# ---------------------------------------------------------------------------
# Comparing two checks
# ---------------------------------------------------------------------------

def compare_checks(conn, a_id: int, b_id: int) -> dict:
    """Diff two checks window-by-window and flag-by-flag.

    Windows align on ``(sample_id, start_year)`` so checks with
    different parameters can still be compared where they overlap.
    """
    ca = db.get_stability_check(conn, a_id)
    cb = db.get_stability_check(conn, b_id)
    if ca is None:
        raise KeyError(f"stability check not found: {a_id}")
    if cb is None:
        raise KeyError(f"stability check not found: {b_id}")
    pa, pb = ca["params"], cb["params"]
    param_diff = {k: {"a": pa.get(k), "b": pb.get(k)}
                  for k in sorted(set(pa) | set(pb))
                  if pa.get(k) != pb.get(k)}
    wa = {(w["sample_id"], w["start_year"]): w for w in ca["windows"]}
    wb = {(w["sample_id"], w["start_year"]): w for w in cb["windows"]}
    common = sorted(set(wa) & set(wb))
    rows = []
    n_agree = 0
    for key in common:
        x, y = wa[key], wb[key]
        agree = x["best_shift"] == y["best_shift"]
        n_agree += 1 if agree else 0
        rows.append({
            "sample_id": key[0],
            "start_year": key[1],
            "best_shift_a": x["best_shift"],
            "best_shift_b": y["best_shift"],
            "agree": agree,
            "current_correlation_a":
                (x.get("current") or {}).get("correlation"),
            "current_correlation_b":
                (y.get("current") or {}).get("correlation"),
        })
    fa = {(f["sample_id"], f["shift"], f["start_year"]): f
          for f in ca["flags"]}
    fb = {(f["sample_id"], f["shift"], f["start_year"]): f
          for f in cb["flags"]}
    return {
        "check_a": a_id, "check_b": b_id,
        "hypothesis_a": ca["hypothesis"],
        "hypothesis_b": cb["hypothesis"],
        "reference_a": ca["reference"], "reference_b": cb["reference"],
        "param_diff": param_diff,
        "n_windows_a": len(ca["windows"]),
        "n_windows_b": len(cb["windows"]),
        "n_common_windows": len(common),
        "n_best_shift_agree": n_agree,
        "windows": rows,
        "flags_in_both": [fa[k] for k in sorted(set(fa) & set(fb))],
        "flags_only_in_a": [fa[k] for k in sorted(set(fa) - set(fb))],
        "flags_only_in_b": [fb[k] for k in sorted(set(fb) - set(fa))],
    }


# ---------------------------------------------------------------------------
# JSON export
# ---------------------------------------------------------------------------

def check_report(conn, check_id: int) -> dict:
    """Full self-contained export: job, snapshot, mappings, evidence."""
    row = db.get_stability_check(conn, check_id)
    if row is None:
        raise KeyError(f"stability check not found: {check_id}")
    detail = check_detail(conn, check_id)
    return {
        "report_type": "tree_ring_stability_check",
        "generated_at": db.now_iso(),
        "check": {k: detail[k] for k in
                  ("check_id", "hypothesis", "sample_id", "reference",
                   "params", "note", "created_at", "targets",
                   "skipped_targets", "n_windows", "n_ok",
                   "n_insufficient", "n_flags",
                   "reference_status", "mapping_status",
                   "read_only_note")},
        "reference_snapshots": row["snapshot"],
        "sample_maps": row["sample_maps"],
        "windows": row["windows"],
        "flags": row["flags"],
    }
