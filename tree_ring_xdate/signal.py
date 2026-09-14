"""Chronology signal-strength assessment (年表信号强度评估).

An *assessment* measures how strong the common signal of a chronology is,
window by window, with the classic dendrochronological quantities:

* **sample depth**     -- number of member samples that share a given year;
* **pair correlation** -- Pearson correlation of every member pair over
                          the years the two series share inside the window;
* **Rbar** (R̄)        -- mean of the *valid* pair correlations -- a pair is
                          only valid when it shares at least
                          ``min_pair_years`` common years and both sides
                          keep a non-zero variance over those years;
* **EPS**              -- expressed population signal
                          ``N·R̄ / (N·R̄ + 1 − R̄)`` with ``N`` the *mean*
                          sample depth over the window years.

The assessment **freezes** its basis the moment it is created: the dating
hypothesis, an optional adopted standardization version, the explicit
member-sample set and the window parameters.  The per-sample indices used
in every statistic are snapshotted then, so later changes to a lock, an
adopted correction or the standardization plan never silently move the
numbers -- such changes only mark the stored assessment *stale*.

For every window the participating samples, the valid pair count and the
exact formula inputs (per-year depths, every pair with its common years /
correlation and eligibility reason) are listed.  Afterwards each member is
removed in turn and Rbar/EPS are recomputed (jackknife); the deltas are
reported for information but **no series is ever excluded automatically**.

A window that cannot conclude does not conclude: with too little coverage,
no valid pair, a zero Rbar variance situation or an otherwise
incalculable EPS it is returned with ``status`` ``insufficient_coverage`` /
``no_valid_pairs`` / ``eps_incalculable`` and the evidence only.  Nothing
is repaired, dropped or assumed.

Lifecycle::

    draft -> completed -> adopted -> retired
                         \-> retired

A created assessment is a ``draft``; finishing it (POST .../complete) moves
it to ``completed``; adopting chooses *consecutive* windows whose EPS
reaches ``eps_threshold`` as the reliable interval -- windows below the
threshold may never be adopted; retiring takes the assessment (and any
range it restricted) out of service.  Only an adopted version can be
pinned by the master chronology (``signal_id``) and by sliding-match runs,
which then restrict their reference to the reliable interval; older jobs
keep the range they were created with.

Endpoints::

    POST /api/signal                      create an assessment (draft)
    GET  /api/signal                      list (?hypothesis=&status=&sample_id=)
    GET  /api/signal/{id}                 detail; window filters:
                                          ?year_from=&year_to=&status=&eps_pass=
    POST /api/signal/{id}/complete        draft -> completed
    POST /api/signal/{id}/adopt           adopt consecutive passing windows
    POST /api/signal/{id}/retire          retire the assessment
    GET  /api/signal/compare?a=&b=        diff two assessments
    GET  /api/signal/{id}/download        JSON export (?download=0 inline)
"""

from __future__ import annotations

import statistics

from . import db

DEFAULT_WINDOW = 50
DEFAULT_STEP = 25
DEFAULT_MIN_SAMPLES = 3
DEFAULT_EPS_THRESHOLD = 0.85
DEFAULT_MIN_PAIR_YEARS = 5

# A member needs at least this many positive-width years for its index
# curve to be defined (the index is width / mean of the positives).
MIN_POSITIVE_YEARS = 3

STATUSES = ("draft", "completed", "adopted", "retired")


class SignalError(Exception):
    """One or more validation failures when handling an assessment (422)."""

    def __init__(self, errors: list[dict]):
        super().__init__("; ".join(e["message"] for e in errors))
        self.errors = errors


def _err(code, message, **extra):
    return {"code": code, "message": message, **extra}


# ---------------------------------------------------------------------------
# Parameter validation
# ---------------------------------------------------------------------------

def _validate_params(window, step, min_samples, eps_threshold,
                     min_pair_years) -> list[dict]:
    errors = []
    if not isinstance(window, int) or window < 5:
        errors.append(_err("E_PARAM", "window must be an integer >= 5",
                           param="window", value=window))
    if not isinstance(step, int) or step < 1:
        errors.append(_err("E_PARAM", "step must be an integer >= 1",
                           param="step", value=step))
    if not isinstance(min_samples, int) or min_samples < 2:
        errors.append(_err("E_PARAM", "min_samples must be an integer >= 2",
                           param="min_samples", value=min_samples))
    if (not isinstance(eps_threshold, (int, float))
            or isinstance(eps_threshold, bool)
            or not 0.0 < eps_threshold < 1.0):
        errors.append(_err("E_PARAM",
                           "eps_threshold must be a number strictly within "
                           "0..1", param="eps_threshold",
                           value=eps_threshold))
    if not isinstance(min_pair_years, int) or min_pair_years < 3:
        errors.append(_err("E_PARAM",
                           "min_pair_years must be an integer >= 3",
                           param="min_pair_years", value=min_pair_years))
    return errors


# ---------------------------------------------------------------------------
# Frozen member index curves (the source mapping)
# ---------------------------------------------------------------------------

def _raw_member_index(conn, hypothesis: str, sample_id: str):
    """Year -> dimensionless index on the raw-width basis.

    The index is width / mean of the member's positive widths; zero-width
    missing years keep index 0.  A plain lock supplies a dense placement;
    an adopted correction draft supplies its segmented mapping.
    """
    series = db.get_series(conn, sample_id)
    if series is None:
        raise KeyError(f"sample not found: {sample_id}")
    corr = db.correction_maps_of(conn, hypothesis).get(sample_id)
    if corr:
        yw = {r["year"]: r["width"] for r in corr}
        placement = "correction"
        correction_versions = sorted(
            {r["draft_id"]: r["draft_version"] for r in corr}.items())
    else:
        offset = db.locks_of(conn, hypothesis).get(sample_id)
        if offset is None:
            if series["known_start"] is not None:
                offset = series["known_start"]
                placement = "known_start"
            else:
                raise SignalError([_err(
                    "E_NO_MAPPING",
                    f"sample {sample_id!r} has no lock, adopted correction "
                    f"or ingested start year in hypothesis {hypothesis!r}; "
                    f"it cannot be assessed", sample_id=sample_id)])
        else:
            placement = "lock"
        yw = {offset + r["seq"] - 1: r["width"] for r in series["rings"]}
        correction_versions = []
    positives = [w for w in yw.values() if w > 0]
    if len(positives) < MIN_POSITIVE_YEARS:
        raise SignalError([_err(
            "E_TOO_SHORT",
            f"sample {sample_id!r} keeps only {len(positives)} positive "
            f"dated year(s); at least {MIN_POSITIVE_YEARS} are required to "
            f"form an index curve", sample_id=sample_id,
            n_positive=len(positives))])
    mean_w = statistics.fmean(positives)
    index = {y: (w / mean_w if w > 0 else 0.0) for y, w in yw.items()}
    return {"sample_id": sample_id, "basis": "raw", "placement": placement,
            "unit": series["unit"], "index": index,
            "mean_width": mean_w, "n_years": len(yw),
            "n_positive": len(positives),
            "correction_versions": [
                {"draft_id": d, "adopted_version": v}
                for d, v in correction_versions]}


def build_sources(conn, hypothesis: str, sample_ids: list[str],
                  std: dict | None) -> tuple[dict, list[dict]]:
    """Freeze every member's year->index curve plus provenance.

    With an adopted standardization version the frozen indices are read
    straight from that version (only plan members can be assessed on that
    basis); otherwise the dimensionless raw-width/mean index is built from
    the current placement.  Returns ``(sources, excluded)`` where sources
    maps sample_id -> ``{"index", ...provenance}`` and excluded lists
    members that could not be frozen with the reason.
    """
    sources, excluded = {}, []
    for sid in sample_ids:
        if std is not None:
            # on a standardized basis every member must come from the
            # adopted plan's frozen indices -- the basis is never mixed
            curve = std["indices"].get(sid)
            if curve is None:
                raise SignalError([_err(
                    "E_STD_NOT_COVERED",
                    f"sample {sid!r} is not a member of adopted "
                    f"standardization {std['id']} v{std['version']}; a "
                    f"standardized assessment can only use its frozen "
                    f"indices", sample_id=sid)])
            src_map = (std.get("source_mapping") or {}).get(sid) or {}
            sources[sid] = {
                "sample_id": sid, "basis": "standardized",
                "placement": src_map.get("placement"),
                "index": {int(y): v for y, v in curve.items()},
                "n_years": len(curve),
                "n_positive": sum(1 for v in curve.values() if v > 0),
                "standardization_id": std["id"],
                "standardization_version": std["version"],
                "correction_versions":
                    src_map.get("correction_versions", [])}
            continue
        try:
            sources[sid] = _raw_member_index(conn, hypothesis, sid)
        except SignalError as e:
            excluded.append({"sample_id": sid,
                             "reason": e.errors[0]["message"],
                             "code": e.errors[0]["code"]})
    return sources, excluded


def default_member_set(conn, hypothesis: str) -> list[str]:
    """All samples placed in the hypothesis (lock or adopted correction)."""
    locks = db.locks_of(conn, hypothesis)
    corr = db.correction_maps_of(conn, hypothesis)
    return sorted(set(locks) | set(corr))


# ---------------------------------------------------------------------------
# Window statistics: depth, pair correlations, Rbar, EPS
# ---------------------------------------------------------------------------

def _eps(rbar: float, depth: float) -> float | None:
    """EPS = N·R̄ / (N·R̄ + 1 − R̄); None when the denominator is not > 0."""
    denom = depth * rbar + (1.0 - rbar)
    if denom <= 0:
        return None
    return depth * rbar / denom


def window_stats(years: list[int], members: dict[str, dict], *,
                 min_samples: int, eps_threshold: float,
                 min_pair_years: int) -> dict:
    """All signal statistics for one calendar-year window.

    ``members`` maps sample_id -> ``{"index": {year: value}}``.  Pair
    correlations use the years two members share; only pairs with at least
    ``min_pair_years`` common years and non-zero variance on both sides are
    *valid* and feed Rbar.  EPS uses the mean sample depth over the window
    years that at least one member covers.  Every formula input is kept.
    """
    member_ids = sorted(members)
    per_year = []
    for y in years:
        present = [sid for sid in member_ids if y in members[sid]["index"]]
        if present:
            per_year.append({"year": y, "n_samples": len(present),
                             "members": present})
    depth_values = [py["n_samples"] for py in per_year]
    participating = sorted({sid for py in per_year for sid in py["members"]})
    mean_depth = (statistics.fmean(depth_values) if depth_values else 0.0)
    max_depth = max(depth_values) if depth_values else 0

    pairs = []
    valid_r = []
    # pairs are always recorded as evidence, even when ineligible
    for i in range(len(member_ids)):
        for j in range(i + 1, len(member_ids)):
            a, b = member_ids[i], member_ids[j]
            ia, ib = members[a]["index"], members[b]["index"]
            common = sorted(y for y in ia if y in ib)
            xs = [ia[y] for y in common]
            ys = [ib[y] for y in common]
            pair = {"sample_a": a, "sample_b": b,
                    "n_common": len(common),
                    "common_start": common[0] if common else None,
                    "common_end": common[-1] if common else None,
                    "correlation": None, "valid": False,
                    "reason": None}
            if len(common) < min_pair_years:
                pair["reason"] = (f"only {len(common)} common year(s) in "
                                  f"the window (< min_pair_years "
                                  f"{min_pair_years})")
            else:
                var_x = sum((v - statistics.fmean(xs)) ** 2 for v in xs)
                var_y = sum((v - statistics.fmean(ys)) ** 2 for v in ys)
                if var_x <= 0 or var_y <= 0:
                    pair["reason"] = ("zero variance on one side over the "
                                      "common years; Pearson correlation is "
                                      "undefined")
                else:
                    mx, my = statistics.fmean(xs), statistics.fmean(ys)
                    cov = sum((x - mx) * (y - my)
                              for x, y in zip(xs, ys))
                    r = cov / (var_x * var_y) ** 0.5
                    pair["correlation"] = r
                    pair["valid"] = True
                    valid_r.append(r)
            pairs.append(pair)

    # Coverage is judged on the *mean* depth over the window years: EPS is
    # driven by the replication actually shared across the window, so two
    # members that only coexist in a few years do not clear min_samples even
    # though their max depth briefly reaches 2.  When coverage is
    # insufficient the window does not conclude: pair correlations stay as
    # evidence, but Rbar and EPS are deliberately not reported.
    insufficient = (len(participating) < min_samples
                    or mean_depth + 1e-9 < min_samples)
    n_valid_pairs = len(valid_r)
    rbar = statistics.fmean(valid_r) if valid_r else None
    eps = (_eps(rbar, mean_depth)
           if rbar is not None and mean_depth > 0 else None)

    if insufficient:
        status = "insufficient_coverage"
        reason = (f"window keeps insufficient sample depth for "
                  f"min_samples {min_samples} (max depth {max_depth}, "
                  f"mean depth {mean_depth:.2f}, {len(participating)} "
                  f"member(s) present); Rbar/EPS not calculated")
        # no signal conclusion from an under-replicated window
        rbar, eps, n_valid_pairs_conclude = None, None, 0
    elif n_valid_pairs == 0:
        status = "no_valid_pairs"
        reason = ("no member pair keeps enough non-constant common years; "
                  "Rbar and EPS are undefined")
        n_valid_pairs_conclude = 0
    elif eps is None:
        status = "eps_incalculable"
        reason = (f"EPS denominator is not positive at Rbar={rbar:.4f}, "
                  f"mean depth {mean_depth:.2f}")
        n_valid_pairs_conclude = n_valid_pairs
    else:
        status = "ok"
        reason = None
        n_valid_pairs_conclude = n_valid_pairs

    return {
        "n_years_present": len(per_year),
        "years": per_year,
        "participating_samples": participating,
        "n_participating": len(participating),
        "max_depth": max_depth,
        "mean_depth": round(mean_depth, 4),
        "depth_inputs": [n for n in depth_values],
        "n_pairs": len(pairs),
        "n_pair_correlations": n_valid_pairs,
        "n_valid_pairs": n_valid_pairs_conclude,
        "pairs": pairs,
        "rbar": (round(rbar, 6) if rbar is not None else None),
        "eps": (round(eps, 6) if eps is not None else None),
        "eps_threshold": eps_threshold,
        "eps_pass": bool(eps is not None and eps >= eps_threshold),
        "eps_formula": "EPS = N*Rbar / (N*Rbar + 1 - Rbar), "
                       "N = mean sample depth",
        "status": status,
        "reason": reason,
    }


def cut_year_windows(year_min: int, year_max: int, window: int, step: int
                     ) -> list[tuple[int, int]]:
    """Calendar-year windows over the member span; the last end is clamped
    to the largest year any member actually covers."""
    out = []
    start = year_min
    index = 1
    while start <= year_max:
        end = min(start + window - 1, year_max)
        out.append((index, start, end))
        if end >= year_max:
            break
        start += step
        index += 1
    return out


def assess_windows(sources: dict, *, window: int, step: int,
                   min_samples: int, eps_threshold: float,
                   min_pair_years: int) -> list[dict]:
    """Slice the frozen member span and score every window, then jackknife.

    The jackknife removes each participating sample in turn and recomputes
    Rbar/EPS; the deltas are advisory -- the full member set is never
    modified and no series is excluded because of its delta.
    """
    all_years = [y for s in sources.values() for y in s["index"]]
    if not all_years:
        return []
    year_min, year_max = min(all_years), max(all_years)
    out = []
    for index, start, end in cut_year_windows(year_min, year_max,
                                              window, step):
        years = list(range(start, end + 1))
        stats = window_stats(years, sources, min_samples=min_samples,
                             eps_threshold=eps_threshold,
                             min_pair_years=min_pair_years)
        rec = {"window_index": index, "start_year": start,
               "end_year": end, **stats}
        # jackknife over the samples the full window actually used
        jackknife = []
        if rec["status"] == "ok":
            for sid in rec["participating_samples"]:
                reduced = {k: v for k, v in sources.items() if k != sid}
                sub = window_stats(years, reduced, min_samples=min_samples,
                                   eps_threshold=eps_threshold,
                                   min_pair_years=min_pair_years)
                jackknife.append({
                    "removed_sample": sid,
                    "status": sub["status"],
                    "n_participating": sub["n_participating"],
                    "mean_depth": sub["mean_depth"],
                    "n_valid_pairs": sub["n_valid_pairs"],
                    "rbar": sub["rbar"],
                    "eps": sub["eps"],
                    "eps_pass": sub["eps_pass"],
                    "rbar_delta": (round(sub["rbar"] - rec["rbar"], 6)
                                   if sub["rbar"] is not None else None),
                    "eps_delta": (round(sub["eps"] - rec["eps"], 6)
                                  if sub["eps"] is not None else None),
                })
        rec["jackknife"] = jackknife
        out.append(rec)
    return out


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------

def create_assessment(conn, *, name: str, hypothesis: str,
                      sample_ids: list[str] | None = None,
                      window: int = DEFAULT_WINDOW,
                      step: int = DEFAULT_STEP,
                      min_samples: int = DEFAULT_MIN_SAMPLES,
                      eps_threshold: float = DEFAULT_EPS_THRESHOLD,
                      min_pair_years: int = DEFAULT_MIN_PAIR_YEARS,
                      note: str = "", std: dict | None = None) -> dict:
    """Freeze the basis, run every window, and persist the assessment."""
    errors = _validate_params(window, step, min_samples, eps_threshold,
                              min_pair_years)
    if not isinstance(name, str) or not name.strip():
        errors.append(_err("E_PARAM", "name is required", param="name"))
    if errors:
        raise SignalError(errors)
    if db.get_hypothesis(conn, hypothesis, with_locks=False) is None:
        raise KeyError(f"hypothesis not found: {hypothesis}")
    if db.get_signal_assessment_by_name(conn, name) is not None:
        raise SignalError([_err("E_SIGNAL_EXISTS",
                                f"signal assessment already exists: {name}")])

    if sample_ids is None:
        sample_ids = default_member_set(conn, hypothesis)
    sample_ids = list(dict.fromkeys(sample_ids))   # de-dup, keep order
    if not sample_ids:
        raise SignalError([_err(
            "E_NO_MEMBERS",
            f"hypothesis {hypothesis!r} has no placed samples to assess")])
    for sid in sample_ids:
        if db.get_series(conn, sid) is None:
            raise KeyError(f"sample not found: {sid}")

    sources, excluded = build_sources(conn, hypothesis, sample_ids, std)
    members = sorted(sources)
    if not members:
        raise SignalError([_err(
            "E_NO_ASSESSABLE_MEMBERS",
            "none of the chosen samples can be frozen into an index "
            "curve", excluded=excluded)])

    windows = assess_windows(
        sources, window=window, step=step, min_samples=min_samples,
        eps_threshold=eps_threshold, min_pair_years=min_pair_years)
    params = {"window": window, "step": step, "min_samples": min_samples,
              "eps_threshold": eps_threshold,
              "min_pair_years": min_pair_years,
              "standardization_id": std["id"] if std else None,
              "standardization_version": std["version"] if std else None}
    assessment_id = db.create_signal_assessment(
        conn, name=name.strip(), hypothesis=hypothesis, params=params,
        sources=sources, members=members, excluded_samples=excluded,
        windows=windows, note=note,
        standardization_id=std["id"] if std else None,
        standardization_version=std["version"] if std else None)
    return assessment_detail(conn, assessment_id)


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

def _require_assessment(conn, assessment_id: int) -> dict:
    row = db.get_signal_assessment(conn, int(assessment_id))
    if row is None:
        raise KeyError(f"signal assessment not found: {assessment_id}")
    return row


def complete_assessment(conn, assessment_id: int) -> dict:
    """Draft -> completed.  The frozen evidence itself never changes."""
    row = _require_assessment(conn, assessment_id)
    if row["status"] == "retired":
        raise SignalError([_err("E_SIGNAL_RETIRED",
                                f"assessment {assessment_id} is retired")])
    if row["status"] == "adopted":
        raise SignalError([_err(
            "E_SIGNAL_ALREADY_ADOPTED",
            f"assessment {assessment_id} is already adopted; it is "
            f"finished")])
    if row["status"] == "draft":
        db.mark_signal_status(conn, assessment_id, "completed")
    return assessment_detail(conn, assessment_id)


def _pass_runs(windows: list[dict]) -> list[list[int]]:
    """Groups of consecutive window indexes with EPS >= threshold."""
    runs, cur = [], []
    for w in windows:
        if w["eps_pass"] and w["status"] == "ok":
            if cur and w["window_index"] == cur[-1] + 1:
                cur.append(w["window_index"])
            else:
                if cur:
                    runs.append(cur)
                cur = [w["window_index"]]
        else:
            if cur:
                runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)
    return runs


def adopt_assessment(conn, assessment_id: int, *,
                     windows: list[int] | None = None) -> dict:
    """Adopt consecutive EPS-passing windows as the reliable interval.

    A completed assessment is required (finish it first); every chosen
    window must reach ``eps_threshold`` and the chosen indexes must be
    consecutive.  With no explicit choice the longest passing run is used;
    when no window passes, adoption is refused -- below-threshold years can
    never be adopted.
    """
    row = _require_assessment(conn, assessment_id)
    if row["status"] == "retired":
        raise SignalError([_err("E_SIGNAL_RETIRED",
                                f"assessment {assessment_id} is retired")])
    if row["status"] == "draft":
        raise SignalError([_err(
            "E_SIGNAL_DRAFT",
            f"assessment {assessment_id} is still a draft; finish it "
            f"(POST .../complete) before adopting a reliable interval")])
    by_index = {w["window_index"]: w for w in row["windows"]}
    runs = _pass_runs(row["windows"])

    if windows is None:
        if not runs:
            raise SignalError([_err(
                "E_NO_RELIABLE_RANGE",
                "no window reaches the EPS threshold; a reliable interval "
                "cannot be adopted")])
        chosen_indexes = max(runs, key=len)
    else:
        chosen_indexes = sorted(set(int(i) for i in windows))
        bad = [i for i in chosen_indexes if i not in by_index]
        if bad:
            raise SignalError([_err(
                "E_WINDOW_NOT_FOUND",
                f"window index(es) {bad} do not exist in assessment "
                f"{assessment_id}", window_indexes=bad)])
        failing = [i for i in chosen_indexes
                   if not by_index[i]["eps_pass"]]
        if failing:
            raise SignalError([_err(
                "E_WINDOW_BELOW_THRESHOLD",
                f"window index(es) {failing} do not reach eps_threshold "
                f"{row['params']['eps_threshold']}; below-threshold ranges "
                f"may never be adopted", window_indexes=failing)])
        # the chosen windows must be physically adjacent (the next window
        # starts exactly one ``step`` years later) and all pass
        chosen_sorted = sorted(chosen_indexes,
                               key=lambda i: by_index[i]["start_year"])
        step = row["params"]["step"]
        adjacent = all(
            by_index[b]["start_year"]
            == by_index[a]["start_year"] + step
            for a, b in zip(chosen_sorted, chosen_sorted[1:]))
        if not adjacent:
            raise SignalError([_err(
                "E_WINDOWS_NOT_CONSECUTIVE",
                "the adopted reliable interval must be adjacent "
                "consecutive windows all reaching eps_threshold",
                window_indexes=chosen_indexes)])
        chosen_indexes = chosen_sorted

    chosen = [by_index[i] for i in chosen_indexes]
    span = {"window_indexes": chosen_indexes,
            "start_year": chosen[0]["start_year"],
            "end_year": chosen[-1]["end_year"],
            "n_windows": len(chosen),
            "min_eps": min(w["eps"] for w in chosen),
            "eps_threshold": row["params"]["eps_threshold"]}
    if row["status"] != "adopted":
        db.mark_signal_status(conn, assessment_id, "adopted",
                              reliable_span=span)
    else:
        db.mark_signal_status(conn, assessment_id, "adopted",
                              reliable_span=span)
    return assessment_detail(conn, assessment_id)


def retire_assessment(conn, assessment_id: int) -> dict:
    row = _require_assessment(conn, assessment_id)
    if row["status"] == "retired":
        raise SignalError([_err("E_SIGNAL_RETIRED",
                                f"assessment {assessment_id} is already "
                                f"retired")])
    db.mark_signal_status(conn, assessment_id, "retired")
    out = assessment_detail(conn, assessment_id)
    return out


# ---------------------------------------------------------------------------
# Staleness: frozen indices vs the hypothesis / plan as they are now
# ---------------------------------------------------------------------------

def _source_status(conn, row: dict) -> dict:
    """Compare the frozen per-member indices with the current basis.

    A change in the source hypothesis (lock / adopted correction) or in
    the standardization version only *marks* the assessment stale -- the
    stored numbers are never recomputed.
    """
    from . import standardization as std_mod
    std_id = row["params"].get("standardization_id")
    std_live = None
    std_unavailable = None
    if std_id is not None:
        try:
            std_live = std_mod.resolve_adopted(
                conn, std_id, row["params"].get("standardization_version"),
                hypothesis=row["hypothesis"])
        except (KeyError, std_mod.StandardizationError) as e:
            std_unavailable = str(e)
    per = {}
    for sid in sorted(row["sources"]):
        frozen = row["sources"][sid]
        try:
            if std_id is not None:
                if std_unavailable is not None:
                    raise SignalError([_err("x", std_unavailable)])
                now_idx = std_live["indices"].get(sid)
                if now_idx is None:
                    per[sid] = {
                        "stale": True,
                        "reason": f"sample {sid!r} is no longer covered by "
                                  f"the frozen standardization version"}
                    continue
                now_idx = {int(y): v for y, v in now_idx.items()}
            else:
                cur = _raw_member_index(conn, row["hypothesis"], sid)
                now_idx = cur["index"]
            fro_idx = {int(y): v for y, v in frozen["index"].items()}
            same = (set(now_idx) == set(fro_idx)
                    and all(abs(now_idx[y] - fro_idx[y]) <= 1e-9
                            for y in fro_idx))
            per[sid] = {"stale": not same,
                        "reason": None if same else
                        "the live index differs from the index frozen at "
                        "creation (placement, correction or standardization "
                        "changed); stored statistics keep using the frozen "
                        "basis"}
        except (SignalError, KeyError):
            per[sid] = {"stale": True,
                        "reason": "the member can no longer be rebuilt from "
                                  "the hypothesis/standardization as it "
                                  "stands now; stored statistics keep using "
                                  "the frozen basis"}
    stale = any(p["stale"] for p in per.values())
    reasons = [f"{sid}: {p['reason']}" for sid, p in per.items()
               if p["stale"]]
    return {"stale": stale,
            "reason": "; ".join(reasons) if reasons else None,
            "per_sample": per}


# ---------------------------------------------------------------------------
# Reading back (with window filters)
# ---------------------------------------------------------------------------

def _filter_windows(windows: list[dict], f: dict) -> list[dict]:
    out = windows
    if f.get("year_from") is not None:
        out = [w for w in out if w["end_year"] >= f["year_from"]]
    if f.get("year_to") is not None:
        out = [w for w in out if w["start_year"] <= f["year_to"]]
    if f.get("status"):
        out = [w for w in out if w["status"] == f["status"]]
    if f.get("eps_pass") is not None:
        out = [w for w in out if w["eps_pass"] is f["eps_pass"]]
    if f.get("sample_id"):
        out = [w for w in out
               if f["sample_id"] in w["participating_samples"]]
    return out


def _public_sources(sources: dict) -> dict:
    """JSON view: indices become sorted year/index lists."""
    out = {}
    for sid, s in sources.items():
        out[sid] = {k: v for k, v in s.items() if k != "index"}
        out[sid]["index"] = [
            {"year": y, "index": round(s["index"][y], 6)}
            for y in sorted(s["index"], key=int)]
    return out


def assessment_detail(conn, assessment_id: int, *,
                      filters: dict | None = None) -> dict:
    row = _require_assessment(conn, assessment_id)
    f = filters or {}
    windows = _filter_windows(row["windows"], f)
    passes = [w for w in row["windows"] if w["eps_pass"]]
    return {
        "assessment_id": row["assessment_id"],
        "name": row["name"],
        "hypothesis": row["hypothesis"],
        "note": row["note"],
        "status": row["status"],
        "version": row["version"],
        "params": row["params"],
        "standardization_id": row["standardization_id"],
        "standardization_version": row["standardization_version"],
        "members": row["members"],
        "excluded_samples": row["excluded_samples"],
        "n_members": len(row["members"]),
        "n_windows": len(row["windows"]),
        "n_ok": sum(1 for w in row["windows"] if w["status"] == "ok"),
        "n_insufficient": sum(
            1 for w in row["windows"]
            if w["status"] in ("insufficient_coverage", "no_valid_pairs",
                               "eps_incalculable")),
        "n_eps_pass": len(passes),
        "pass_runs": _pass_runs(row["windows"]),
        "reliable_span": row["reliable_span"],
        "adopted_at": row["adopted_at"],
        "source_status": _source_status(conn, row),
        "filters": dict(f),
        "windows": windows,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


# ---------------------------------------------------------------------------
# Comparing two assessments
# ---------------------------------------------------------------------------

def compare_assessments(conn, a_id: int, b_id: int) -> dict:
    """Diff two assessments window-by-window (aligned on start_year)."""
    ca = _require_assessment(conn, a_id)
    cb = _require_assessment(conn, b_id)
    pa, pb = ca["params"], cb["params"]
    param_diff = {k: {"a": pa.get(k), "b": pb.get(k)}
                  for k in sorted(set(pa) | set(pb))
                  if pa.get(k) != pb.get(k)}
    wa = {w["start_year"]: w for w in ca["windows"]}
    wb = {w["start_year"]: w for w in cb["windows"]}
    common = sorted(set(wa) & set(wb))
    rows, n_agree = [], 0
    for start in common:
        x, y = wa[start], wb[start]
        agree = x["eps_pass"] == y["eps_pass"]
        n_agree += 1 if agree else 0
        rows.append({
            "start_year": start,
            "end_year_a": x["end_year"], "end_year_b": y["end_year"],
            "rbar_a": x["rbar"], "rbar_b": y["rbar"],
            "eps_a": x["eps"], "eps_b": y["eps"],
            "mean_depth_a": x["mean_depth"], "mean_depth_b": y["mean_depth"],
            "eps_pass_a": x["eps_pass"], "eps_pass_b": y["eps_pass"],
            "status_a": x["status"], "status_b": y["status"],
            "pass_agree": agree,
        })
    return {
        "assessment_a": a_id, "assessment_b": b_id,
        "name_a": ca["name"], "name_b": cb["name"],
        "hypothesis_a": ca["hypothesis"], "hypothesis_b": cb["hypothesis"],
        "status_a": ca["status"], "status_b": cb["status"],
        "members_a": ca["members"], "members_b": cb["members"],
        "param_diff": param_diff,
        "n_windows_a": len(ca["windows"]),
        "n_windows_b": len(cb["windows"]),
        "n_common_windows": len(common),
        "n_pass_agree": n_agree,
        "reliable_span_a": ca["reliable_span"],
        "reliable_span_b": cb["reliable_span"],
        "windows": rows,
        "windows_only_in_a": [
            {"window_index": wa[s]["window_index"], "start_year": s,
             "end_year": wa[s]["end_year"]}
            for s in sorted(set(wa) - set(wb))],
        "windows_only_in_b": [
            {"window_index": wb[s]["window_index"], "start_year": s,
             "end_year": wb[s]["end_year"]}
            for s in sorted(set(wb) - set(wa))],
    }


# ---------------------------------------------------------------------------
# JSON export
# ---------------------------------------------------------------------------

def assessment_report(conn, assessment_id: int) -> dict:
    """Full self-contained export: job, frozen sources, every window."""
    row = _require_assessment(conn, assessment_id)
    detail = assessment_detail(conn, assessment_id)
    return {
        "report_type": "tree_ring_signal_strength",
        "generated_at": db.now_iso(),
        "assessment": {k: detail[k] for k in
                       ("assessment_id", "name", "hypothesis", "note",
                        "status", "version", "params", "members",
                        "excluded_samples", "n_members", "n_windows",
                        "n_ok", "n_insufficient", "n_eps_pass", "pass_runs",
                        "reliable_span", "adopted_at", "source_status",
                        "standardization_id", "standardization_version",
                        "created_at", "updated_at")},
        "sources": _public_sources(row["sources"]),
        "windows": row["windows"],
    }


# ---------------------------------------------------------------------------
# Adopted assessment for downstream jobs (master / crossdate)
# ---------------------------------------------------------------------------

def resolve_adopted(conn, assessment_id: int, *,
                    hypothesis: str | None = None) -> dict:
    """Load an adopted assessment for restricting a reference range.

    Returns ``{"id", "version", "hypothesis", "sources", "span"}`` where
    ``span`` is the adopted ``[start_year, end_year]`` reliable interval.
    Only adopted assessments resolve; a hypothesis mismatch is a 422 and a
    missing assessment a 404.
    """
    row = db.get_signal_assessment(conn, int(assessment_id))
    if row is None:
        raise KeyError(f"signal assessment not found: {assessment_id}")
    if row["status"] != "adopted" or not row["reliable_span"]:
        raise SignalError([_err(
            "E_SIGNAL_NOT_ADOPTED",
            f"signal assessment {assessment_id} is not adopted (status "
            f"{row['status']}); only an adopted assessment can restrict a "
            f"chronology or match reference", status=row["status"])])
    if hypothesis is not None and hypothesis != row["hypothesis"]:
        raise SignalError([_err(
            "E_SIGNAL_HYPOTHESIS_MISMATCH",
            f"signal assessment {assessment_id} reads hypothesis "
            f"{row['hypothesis']!r} but was requested under "
            f"{hypothesis!r}",
            assessment_hypothesis=row["hypothesis"],
            request_hypothesis=hypothesis)])
    span = row["reliable_span"]
    return {"id": row["assessment_id"], "version": row["version"],
            "hypothesis": row["hypothesis"], "sources": row["sources"],
            "members": row["members"],
            "span": (span["start_year"], span["end_year"]),
            "reliable_span": span,
            "standardization_id": row["standardization_id"],
            "standardization_version": row["standardization_version"]}


def signal_member_indices(signal: dict) -> dict[str, dict[int, float]]:
    """Frozen member ``sample_id -> {year: index}`` curves of an adopted
    assessment, with every year restricted to the reliable span."""
    lo, hi = signal["span"]
    return {sid: {y: v for y, v in
                  ({int(yy): vv for yy, vv in s["index"].items()}).items()
                  if lo <= y <= hi}
            for sid, s in signal["sources"].items()}


def signal_reference_years(signal: dict) -> set[int]:
    """Calendar years inside the adopted reliable interval (inclusive)."""
    lo, hi = signal["span"]
    return set(range(lo, hi + 1))
