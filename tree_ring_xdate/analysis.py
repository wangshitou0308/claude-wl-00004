"""Cross-dating statistics and chronology building.

Everything here is pure Python (statistics module from the standard
library), so the whole service stays importable on an offline machine.

Placement convention
--------------------
An *offset* is the calendar year assigned to ring position 1 of an undated
sample.  Sliding a sample therefore only changes its offset; the widths
keep positions ``1..n``.

Missing rings are stored as width ``0`` at the position where the ring
should have been, so they participate in every comparison (a zero in the
sample never matches a positive ring in the reference, which is exactly
what a dendrochronologist wants to see).
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict

from . import db

# ---------------------------------------------------------------------------
# Placement / series loading
# ---------------------------------------------------------------------------

def placed_year_widths(series: dict, offset: int | None) -> dict[int, float]:
    """Map calendar year -> width for a series placed at ``offset``."""
    if offset is None:
        return {}
    return {offset + r["seq"] - 1: r["width"] for r in series["rings"]}


def corrected_year_widths(corr_rows: list[dict]) -> dict[int, float]:
    """Year -> width from an adopted correction mapping."""
    return {r["year"]: r["width"] for r in corr_rows}


def resolve_placements(conn, hypothesis: str | None = None
                       ) -> tuple[dict[str, int], dict[str, int | None],
                                  dict[str, int], dict[str, list[dict]]]:
    """Return ``(placements, known_starts, locks, correction_maps)``.

    A lock in ``hypothesis`` always wins over the ingested known start --
    a re-dating decision must never be silently ignored.  Samples with
    neither are left out (undated and unlocked).  Samples with an adopted
    correction draft in this hypothesis appear in ``correction_maps``
    (their segmented year mapping replaces the dense offset placement).
    """
    locks = db.locks_of(conn, hypothesis) if hypothesis else {}
    corr_maps = (db.correction_maps_of(conn, hypothesis)
                 if hypothesis else {})
    placements: dict[str, int] = {}
    known: dict[str, int | None] = {}
    for s in db.list_series(conn):
        sid = s["sample_id"]
        full = db.get_series(conn, sid)
        known[sid] = full["known_start"]
        if sid in corr_maps:
            placements[sid] = min(r["year"] for r in corr_maps[sid])
        elif sid in locks:
            placements[sid] = locks[sid]
        elif full["known_start"] is not None:
            placements[sid] = full["known_start"]
    return placements, known, locks, corr_maps


def known_start_conflicts(known: dict[str, int | None],
                          locks: dict[str, int]) -> list[dict]:
    """Locks that contradict the start year recorded at ingestion time."""
    out = []
    for sid, off in locks.items():
        ks = known.get(sid)
        if ks is not None and off != ks:
            out.append({
                "type": "LOCK_VS_KNOWN",
                "sample_id": sid,
                "known_start": ks,
                "locked_offset": off,
                "shift": off - ks,
                "message": (f"{sid} was ingested with known start {ks} but "
                            f"the hypothesis locks it at {off} "
                            f"(shift {off - ks:+d} yr); the locked position "
                            f"is used for this hypothesis"),
            })
    return sorted(out, key=lambda c: c["sample_id"])


def _std_indices(conn, std):
    """sample_id -> {year: standardized index} from a resolved std plan.

    ``std`` is the dict produced by
    :func:`tree_ring_xdate.standardization.resolve_adopted` (or None).
    """
    if std is None:
        return {}
    return std["indices"]


def build_reference(conn, reference: str,
                    hypothesis: str | None = None,
                    exclude: set[str] | None = None,
                    std: dict | None = None
                    ) -> tuple[dict[int, float], dict]:
    """Return ``(year_widths, meta)`` for a dated series or the master.

    The master chronology is the per-year mean of dimensionless indices
    (width / sample mean) of every placed sample.  Its values are not
    physical widths, but their year-to-year signs/magnitudes are what the
    correlation statistics need.

    When ``std`` (a resolved adopted standardization version) is given,
    the master is built from the frozen standardized indices instead, and
    only samples covered by the plan contribute; a designated reference
    series is read through the plan's index curve when it covers the
    sample.  Missing-ring indices stay zero; false rings never had a
    calendar year.

    ``exclude`` drops samples from the master chronology (leave-one-out
    stability checks must never compare a sample with a chronology it
    belongs to); excluding the designated reference series itself is an
    error.
    """
    exclude = exclude or set()
    indices = _std_indices(conn, std)
    if reference not in (None, "", "master", "MASTER", "@master"):
        if reference in exclude:
            raise ValueError(
                f"reference series {reference!r} cannot be excluded from "
                f"its own reference")
        s = db.get_series(conn, reference)
        if s is None:
            raise KeyError(f"reference series not found: {reference}")
        # A lock, when present, wins over the ingested known start so a
        # re-dated sample can itself serve as the reference.  An adopted
        # correction mapping wins over both (segmented placement).
        corr = (db.correction_maps_of(conn, hypothesis).get(reference)
                if hypothesis else None)
        locked = (db.locks_of(conn, hypothesis).get(reference)
                  if hypothesis else None)
        if corr:
            yw = corrected_year_widths(corr)
            offset = min(yw)
        elif locked is not None:
            offset = locked
            yw = placed_year_widths(s, offset)
        elif s["known_start"] is not None:
            offset = s["known_start"]
            yw = placed_year_widths(s, offset)
        else:
            raise ValueError(
                f"reference series {reference!r} is not dated and has no "
                f"lock in hypothesis {hypothesis!r}")
        if reference in indices:
            yw = dict(indices[reference])
        meta = {"type": "series", "sample_id": reference,
                "unit": s["unit"], "start": min(yw), "end": max(yw),
                "n_years": len(yw), "offset": offset,
                "known_start": s["known_start"],
                "hypothesis": hypothesis,
                "corrected": bool(corr),
                "standardized": reference in indices}
        if std is not None:
            meta["standardization_id"] = std["id"]
            meta["standardization_version"] = std["version"]
        if (s["known_start"] is not None
                and locked is not None and locked != s["known_start"]):
            meta["known_start_conflicts"] = known_start_conflicts(
                {reference: s["known_start"]}, {reference: locked})
        return yw, meta

    placements, known, locks, corr_maps = resolve_placements(conn,
                                                             hypothesis)
    years, per_year = set(), defaultdict(list)
    members = []
    excluded = []
    for sid, off in placements.items():
        if sid in exclude:
            excluded.append(sid)
            continue
        s = db.get_series(conn, sid)
        if sid in indices:
            # frozen standardized index -- no further division by mean
            yw = dict(indices[sid])
            unit_value = 1.0
            standardized = True
        else:
            if std is not None:
                # a standardized chronology contains plan members only
                continue
            if sid in corr_maps:
                yw = corrected_year_widths(corr_maps[sid])
            else:
                yw = placed_year_widths(s, off)
            unit_value = None
            standardized = False
        positives = [w for w in yw.values() if w > 0]
        if len(positives) < 3:
            continue
        if unit_value is None:
            mean_w = statistics.fmean(positives)
        members.append({"sample_id": sid, "start": min(yw), "end": max(yw),
                        "corrected": sid in corr_maps,
                        "standardized": standardized})
        for y, w in yw.items():
            if standardized:
                per_year[y].append(w)
            else:
                per_year[y].append(w / mean_w if w > 0 else 0.0)
            years.add(y)
    meta = {"type": "master", "members": members,
            "hypothesis": hypothesis,
            "start": min(years) if years else None,
            "end": max(years) if years else None,
            "n_years": len(years),
            "known_start_conflicts":
                known_start_conflicts(known, locks)}
    if std is not None:
        meta["standardized"] = True
        meta["standardization_id"] = std["id"]
        meta["standardization_version"] = std["version"]
    if excluded:
        meta["excluded_samples"] = sorted(excluded)
    return ({y: statistics.fmean(per_year[y]) for y in sorted(years)}, meta)


# ---------------------------------------------------------------------------
# Pair statistics
# ---------------------------------------------------------------------------

def pearson(xs: list[float], ys: list[float]) -> float | None:
    """Pearson correlation; None when either side has zero variance."""
    n = len(xs)
    if n < 3:
        return None
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sx = sum((x - mx) ** 2 for x in xs)
    sy = sum((y - my) ** 2 for y in ys)
    if sx <= 0 or sy <= 0:
        return None
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return cov / math.sqrt(sx * sy)


def sign_agreement(xs: list[float], ys: list[float]) -> tuple[float, dict]:
    """Gleichlaeufigkeit-style sign agreement of first differences.

    A pair of steps counts 1 when both rise, both fall, or both are flat;
    half credit is impossible between discrete values.  Returns
    ``(rate, detail)`` where detail carries the raw counts.
    """
    agree = compared = 0
    for i in range(1, len(xs)):
        dx = xs[i] - xs[i - 1]
        dy = ys[i] - ys[i - 1]
        if dx == 0 and dy == 0:
            agree += 1
        elif dx == 0 or dy == 0:
            pass  # flat vs changing is non-informative, still "disagree"
        elif (dx > 0) == (dy > 0):
            agree += 1
        compared += 1
    rate = agree / compared if compared else None
    return rate, {"agree": agree, "compared": compared}


def narrow_years(year_widths: dict[int, float], *, z: float = -1.0,
                 quantile: float = 0.1) -> set[int]:
    """Years that are *both* below the z threshold and among the narrowest.

    Zero-width entries (missing rings) are excluded when estimating mean
    and standard deviation, then automatically qualify.
    """
    positives = sorted(w for w in year_widths.values() if w > 0)
    if len(positives) < 3:
        return {y for y, w in year_widths.items() if w <= 0}
    mean_w = statistics.fmean(positives)
    sd_w = statistics.pstdev(positives)
    cutoff = _quantile_sorted(positives, quantile)
    out = set()
    for y, w in year_widths.items():
        if w <= 0:
            out.add(y)
            continue
        if w <= cutoff and (sd_w == 0 or (w - mean_w) / sd_w <= z):
            out.add(y)
    return out


def _quantile_sorted(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * q
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return sorted_vals[lo]
    return sorted_vals[lo] * (hi - k) + sorted_vals[hi] * (k - lo)


# ---------------------------------------------------------------------------
# Cross-dating
# ---------------------------------------------------------------------------

def default_offset_window(target: dict, ref_years: set[int]
                          ) -> tuple[int, int]:
    """Generous ±(n+span) window so a plausible alignment always fits."""
    n = len(target["rings"])
    if not ref_years:
        return 1900, 2000
    lo, hi = min(ref_years), max(ref_years)
    return lo - n, hi + n


def crossdate(conn, sample_id: str, *, reference: str = "master",
              offset_min: int | None = None, offset_max: int | None = None,
              min_overlap: int = 20, narrow_z: float = -1.0,
              narrow_q: float = 0.1, tolerance: float = 0.05,
              top_k: int = 10, hypothesis: str | None = None,
              std: dict | None = None) -> dict:
    target = db.get_series(conn, sample_id)
    if target is None:
        raise KeyError(f"sample not found: {sample_id}")
    t_widths = [r["width"] for r in target["rings"]]
    n = len(t_widths)

    ref_yw, ref_meta = build_reference(conn, reference, hypothesis,
                                       std=std)
    # A standardized reference expects a dimensionless target: a target
    # covered by an adopted plan uses its frozen index curve; any other
    # sample is divided by its own positive mean so the two sides are
    # comparable.
    indices = _std_indices(conn, std)
    target_std = indices.get(sample_id) if std is not None else None
    if std is not None and target_std is None:
        positives = [w for w in t_widths if w > 0]
        mean_w = statistics.fmean(positives) if positives else 1.0
        t_widths = [w / mean_w if w > 0 else 0.0 for w in t_widths]
    ref_years = set(ref_yw)
    if offset_min is None or offset_max is None:
        dmin, dmax = default_offset_window(target, ref_years)
        offset_min = dmin if offset_min is None else offset_min
        offset_max = dmax if offset_max is None else offset_max

    ref_narrow = narrow_years(ref_yw, z=narrow_z, quantile=narrow_q)
    candidates = []
    for off in range(int(offset_min), int(offset_max) + 1):
        # overlap between [off, off+n-1] and reference years
        lo = max(off, min(ref_years)) if ref_years else off
        hi = min(off + n - 1, max(ref_years)) if ref_years else off - 1
        overlap = hi - lo + 1
        if overlap < min_overlap:
            continue
        xs, ys = [], []
        for y in range(lo, hi + 1):
            if y not in ref_yw:
                continue  # sparse reference years (missing rings outside)
            if target_std is not None:
                w = target_std.get(y, 0.0)
            else:
                w = t_widths[y - off]
            xs.append(w)
            ys.append(ref_yw[y])
        if len(xs) < min_overlap:
            continue
        r = pearson(xs, ys)
        sign, sign_detail = sign_agreement(xs, ys)
        if target_std is not None:
            t_block = {y: target_std.get(y, 0.0)
                       for y in range(lo, hi + 1)}
        else:
            t_block = {off + i: w for i, w in enumerate(t_widths)
                       if lo <= off + i <= hi}
        t_narrow = narrow_years(t_block, z=narrow_z, quantile=narrow_q)
        hits = sorted(y for y in t_narrow if y in ref_narrow)
        candidates.append({
            "offset": off,
            "overlap_start": lo,
            "overlap_end": hi,
            "n_overlap": len(xs),
            "correlation": r,
            "sign_agreement": sign,
            "narrow_hits": hits,
            "n_narrow_hits": len(hits),
        })

    candidates = _rank_and_select(candidates, tolerance, top_k)

    params = {"offset_min": offset_min, "offset_max": offset_max,
              "min_overlap": min_overlap, "narrow_z": narrow_z,
              "narrow_q": narrow_q, "tolerance": tolerance,
              "standardization_id": std["id"] if std else None,
              "standardization_version": std["version"] if std else None}
    return {
        "sample_id": sample_id,
        "reference": reference,
        "reference_meta": ref_meta,
        "target_standardized": target_std is not None,
        "params": params,
        "n_scanned": int(offset_max) - int(offset_min) + 1,
        "candidates": candidates,
    }


def _score(c: dict) -> float:
    # primary: correlation; tie-breakers: sign agreement, narrow-ring hits
    r = c["correlation"] if c["correlation"] is not None else -9.0
    s = c["sign_agreement"] if c["sign_agreement"] is not None else 0.0
    return r * 100 + s * 10 + c["n_narrow_hits"]


def _rank_and_select(candidates: list[dict], tolerance: float,
                     top_k: int) -> list[dict]:
    candidates.sort(key=lambda c: (-_score(c), c["offset"]))
    if not candidates:
        return []
    best_r = candidates[0]["correlation"]
    chosen = []
    for c in candidates[:top_k]:
        r = c["correlation"]
        if r is None:
            keep = best_r is None
        elif best_r is None:
            keep = True
        else:
            keep = r >= best_r - tolerance
        if not keep:
            break
        c["rank"] = len(chosen) + 1
        chosen.append(c)
    return chosen


# ---------------------------------------------------------------------------
# Chronology
# ---------------------------------------------------------------------------

def build_chronology(conn, hypothesis: str | None = None, *,
                     min_samples: int = 3, outlier_sd: float = 2.0,
                     weak_correlation: float = 0.3,
                     std: dict | None = None
                     ) -> dict:
    """Year-by-year sample count, mean/median index and dispersion.

    With a resolved adopted standardization ``std`` the per-year index
    series come from the frozen standardized indices (plan members only,
    no further division by sample mean); otherwise indices are width /
    sample positive mean as before.

    Flags:
      LOW_COVERAGE   -- fewer than ``min_samples`` samples that year
      OUTLIER        -- a sample index deviates from the yearly mean by
                        more than ``outlier_sd`` yearly SD
      LOCK_CONFLICT  -- two locked/dated samples physically overlap at a
                        calendar year but disagree strongly, or a locked
                        sample correlates weakly with all the others
                        (leave-one-out over the common interval)
    """
    indices = _std_indices(conn, std)
    placements, known, locks, corr_maps = resolve_placements(conn,
                                                             hypothesis)
    per_year = defaultdict(list)   # year -> [(sid, index)]
    sample_yw = {}
    units = defaultdict(set)
    for sid, off in placements.items():
        s = db.get_series(conn, sid)
        if sid in indices:
            yw = dict(indices[sid])
            standardized = True
        else:
            if std is not None:
                continue
            if sid in corr_maps:
                yw = corrected_year_widths(corr_maps[sid])
            else:
                yw = placed_year_widths(s, off)
            standardized = False
        sample_yw[sid] = yw
        positives = [w for w in yw.values() if w > 0]
        if len(positives) < 3:
            continue
        if not standardized:
            mean_w = statistics.fmean(positives)
        for y, w in yw.items():
            value = w if standardized else (w / mean_w if w > 0 else 0.0)
            per_year[y].append((sid, value))
            units[y].add(s["unit"])

    years = sorted(per_year)
    yearly = []
    outliers, unit_mismatches = [], []
    for y in years:
        vals = [v for _sid, v in per_year[y]]
        mean_v = statistics.fmean(vals)
        median_v = statistics.median(vals)
        sd_v = statistics.pstdev(vals) if len(vals) > 1 else 0.0
        flags = []
        if len(vals) < min_samples:
            flags.append("LOW_COVERAGE")
        year_outliers = []
        if sd_v > 0:
            for sid, v in per_year[y]:
                if abs(v - mean_v) > outlier_sd * sd_v:
                    year_outliers.append({
                        "sample_id": sid, "year": y,
                        "index": round(v, 4),
                        "deviation_sd": round(abs(v - mean_v) / sd_v, 3)})
        if year_outliers:
            flags.append("OUTLIER")
            outliers.extend(year_outliers)
        if len(units[y]) > 1:
            unit_mismatches.append({"year": y,
                                    "units": sorted(units[y])})
        yearly.append({
            "year": y,
            "n_samples": len(vals),
            "mean_index": round(mean_v, 5),
            "median_index": round(median_v, 5),
            "stdev_index": round(sd_v, 5),
            "mad": round(statistics.fmean(
                [abs(v - median_v) for v in vals]), 5),
            "members": [sid for sid, _v in per_year[y]],
            "flags": flags,
        })

    conflicts = known_start_conflicts(known, locks)
    conflicts.extend(_lock_conflicts(conn, placements, sample_yw,
                                     weak_correlation))
    return {
        "hypothesis": hypothesis,
        "start": years[0] if years else None,
        "end": years[-1] if years else None,
        "n_years": len(years),
        "n_samples": len(sample_yw),
        "placements": placements,
        "known_starts": known,
        "corrected_samples": sorted(corr_maps),
        "correction_drafts": {
            sid: sorted({r["draft_id"] for r in corr_maps[sid]})
            for sid in sorted(corr_maps)},
        "standardized": std is not None,
        "standardization_id": std["id"] if std else None,
        "standardization_version": std["version"] if std else None,
        "standardized_samples": sorted(indices) if std else [],
        "yearly": yearly,
        "outliers": outliers,
        "unit_mismatches": unit_mismatches,
        "lock_conflicts": conflicts,
        "n_known_start_conflicts":
            sum(1 for c in conflicts if c["type"] == "LOCK_VS_KNOWN"),
        "thresholds": {"min_samples": min_samples,
                       "outlier_sd": outlier_sd,
                       "weak_correlation": weak_correlation},
    }


def _lock_conflicts(conn, placements, sample_yw, weak_r,
                    missing_share=0.6, min_cover=3, minority_cap=2):
    """Physical-year contradictions plus weak leave-one-out matches.

    A single sample missing a ring where others grow is normal biology.
    MISSING_VS_PRESENT only fires when at least ``min_cover`` samples cover
    the year, at least ``missing_share`` of them agree on "missing" (or on
    "present"), and the dissenting minority is at most ``minority_cap``
    samples -- that pattern means one placement is almost certainly off.
    """
    conflicts = []
    missing_at = defaultdict(list)   # year -> [sid]
    present_at = defaultdict(list)
    for sid, yw in sample_yw.items():
        for y, w in yw.items():
            (missing_at if w <= 0 else present_at)[y].append(sid)
    for y in sorted(set(missing_at) | set(present_at)):
        miss, have = missing_at.get(y, []), present_at.get(y, [])
        total = len(miss) + len(have)
        if total < min_cover:
            continue
        # One sample missing a ring where others grow is normal biology,
        # so only the reverse direction counts as a physical contradiction:
        # a strong majority has NO ring in this calendar year while one or
        # two samples nevertheless show growth -- their placement is off.
        if len(miss) / total >= missing_share and 0 < len(have) <= minority_cap:
            conflicts.append({
                "type": "MISSING_VS_PRESENT",
                "subtype": "GROWS_WHILE_MOST_MISS",
                "year": y,
                "minority_samples": sorted(have),
                "majority_n": len(miss),
                "covering_n": total,
                "message": (f"year {y}: {', '.join(sorted(have))} show growth "
                            f"while {len(miss)} samples have a missing ring "
                            f"there"),
            })

    # 2) leave-one-out correlation over each sample's common interval.
    for sid, yw in sample_yw.items():
        positives = [w for w in yw.values() if w > 0]
        if len(positives) < 3:
            continue
        mean_w = statistics.fmean(positives)
        other_means = {}
        for y in yw:
            vals = []
            for sid2, yw2 in sample_yw.items():
                if sid2 == sid or y not in yw2:
                    continue
                p2 = [x for x in yw2.values() if x > 0]
                if len(p2) < 3:
                    continue
                vals.append(yw2[y] / statistics.fmean(p2)
                            if yw2[y] > 0 else 0.0)
            if vals:
                other_means[y] = statistics.fmean(vals)
        paired_years = [y for y in yw if y in other_means]
        if len(paired_years) < 10:
            continue
        xs = [(yw[y] / mean_w if yw[y] > 0 else 0.0)
              for y in paired_years]
        ys = [other_means[y] for y in paired_years]
        r = pearson(xs, ys)
        if r is not None and r < weak_r:
            conflicts.append({
                "type": "WEAK_MATCH",
                "sample_id": sid,
                "offset": placements[sid],
                "correlation_vs_others": round(r, 4),
                "n_overlap": len(paired_years),
                "message": (f"locked placement of {sid} at "
                            f"{placements[sid]} correlates only r={r:.3f} "
                            f"with the other samples"),
            })
    return conflicts


# ---------------------------------------------------------------------------
# Hypothesis comparison / reports
# ---------------------------------------------------------------------------

def compare_hypotheses(conn, name_a: str, name_b: str) -> dict:
    a = db.locks_of(conn, name_a)
    b = db.locks_of(conn, name_b)
    rows = []
    for sid in sorted(set(a) | set(b)):
        off_a, off_b = a.get(sid), b.get(sid)
        rows.append({
            "sample_id": sid,
            "offset_a": off_a,
            "offset_b": off_b,
            "shift": (off_b - off_a) if (off_a is not None
                                         and off_b is not None) else None,
            "in_a_only": off_a is not None and off_b is None,
            "in_b_only": off_b is not None and off_a is None,
        })
    return {"hypothesis_a": name_a, "hypothesis_b": name_b,
            "n_a": len(a), "n_b": len(b),
            "differences": [r for r in rows
                            if r["shift"] not in (None, 0)
                            or r["in_a_only"] or r["in_b_only"]],
            "all_samples": rows}


def compare_to_master(conn, hypothesis: str) -> dict:
    """Per-sample shift of a hypothesis vs the current dated master."""
    locks = db.locks_of(conn, hypothesis)
    rows = []
    for s in db.list_series(conn):
        sid = s["sample_id"]
        locked = locks.get(sid)
        known = s["known_start"]
        if locked is None and known is None:
            continue
        rows.append({
            "sample_id": sid,
            "dated_start": known,
            "locked_offset": locked,
            "shift": (locked - known)
            if (locked is not None and known is not None) else None,
            "status": ("consistent" if locked == known else
                       "shifted" if locked is not None and known is not None
                       else "locked_only" if locked is not None
                       else "dated_only"),
        })
    return {"hypothesis": hypothesis, "samples": rows}
