"""Per-sample sequence standardization (序列标准化方案).

A *standardization plan* picks a set of dated samples and, independently
for every sample, one detrending method and its parameters:

``mean``                -- horizontal mean (水平均值): the expected growth
                           curve is the arithmetic mean of the valid
                           widths; the index is width / mean.
``negative_exponential``-- negative exponential curve (负指数曲线)
                           ``f(year) = a * exp(b * year) + d`` fitted to
                           the valid widths by a damped Gauss-Newton
                           iteration.
``moving_average``      -- fixed-window *centred* moving average
                           (固定窗口居中移动平均, odd window >= 3): the
                           expected value of every dated position is the
                           mean of the valid widths inside the symmetric
                           window.

A plan reads its calendar years from the *currently adopted year mapping*
inside one dating hypothesis -- a plain lock, or an adopted correction
draft (segmented mapping).  Missing rings keep their zero width (their
index is 0); false rings never received a calendar year, so they take no
part in the fitting at all.

The valid (positive-width, dated) points are the only fit data.  Nothing
is ever silently switched: too few valid points, a negative-exponential
fit that does not converge, non-positive expected values, or moving
windows without enough data are reported per sample **with the
measurement seq(s)** and the sample stays on the method the
experimenter chose.

Lifecycle::

    draft -> validated -> adopted -> retired

Every config replacement creates a new immutable version.  Only a plan
whose *all* samples validate can leave ``draft``; adopting freezes the
source mapping (incl. correction versions) and every fitted curve into
the adopted version, so master chronologies, sliding-match runs and
stability checks can later pin ``standardization_id`` /
``standardization_version`` -- old jobs keep the calculation basis they
were created with.

Endpoints::

    POST /api/standardizations                 create plan (version 1)
    GET  /api/standardizations                 list plans
    GET  /api/standardizations/{id}            plan + latest version preview
    POST /api/standardizations/{id}/preview    ad-hoc/version preview
    POST /api/standardizations/{id}/versions   new config version
    GET  /api/standardizations/{id}/versions   version list
    POST /api/standardizations/{id}/validate   all-samples check
    POST /api/standardizations/{id}/adopt      freeze + adopt a version
    POST /api/standardizations/{id}/retire     retire the plan
    GET  /api/standardizations/{id}/compare    diff two versions
    GET  /api/standardizations/{id}/download   full JSON export
"""

from __future__ import annotations

import math
import statistics

from . import db

METHODS = ("mean", "negative_exponential", "moving_average")
METHOD_ALIASES = {
    "mean": "mean",
    "horizontal_mean": "mean",
    "horizontal": "mean",
    "水平均值": "mean",
    "negative_exponential": "negative_exponential",
    "negative_exp": "negative_exponential",
    "exp": "negative_exponential",
    "负指数": "negative_exponential",
    "负指数曲线": "negative_exponential",
    "moving_average": "moving_average",
    "moving_avg": "moving_average",
    "ma": "moving_average",
    "移动平均": "moving_average",
    "居中移动平均": "moving_average",
    "固定窗口居中移动平均": "moving_average",
}

MIN_VALID_POINTS = 3          # positive dated widths needed for any fit
MA_MIN_WINDOW_POINTS = 3      # positives a centred window must contain
MAX_FIT_ITERATIONS = 200
FIT_TOLERANCE = 1e-10
GRADIENT_TOLERANCE = 1e-8


class StandardizationError(Exception):
    """One or more validation failures (HTTP 422)."""

    def __init__(self, errors: list[dict]):
        super().__init__("; ".join(e["message"] for e in errors))
        self.errors = errors


def _err(code, message, *, sample_id=None, seq=None, method=None,
         extra=None):
    d = {"code": code, "message": message, "sample_id": sample_id,
         "seq": seq, "method": method}
    if extra:
        d.update(extra)
    return d


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


# ---------------------------------------------------------------------------
# Config normalisation
# ---------------------------------------------------------------------------

def _normalize_sample(raw, index, errors):
    if not isinstance(raw, dict):
        errors.append(_err(
            "E_STD_SHAPE",
            f"samples[{index}] is not a JSON object"))
        return None
    sid = raw.get("sample_id")
    if not isinstance(sid, str) or not sid:
        errors.append(_err(
            "E_STD_SHAPE",
            f"samples[{index}]: sample_id is required"))
        sid = sid if isinstance(sid, str) else None
    method_raw = raw.get("method", "mean")
    method = METHOD_ALIASES.get(method_raw)
    if method is None:
        errors.append(_err(
            "E_STD_METHOD",
            f"samples[{index}] ({sid}): method must be one of "
            f"mean / negative_exponential / moving_average, got "
            f"{method_raw!r}", sample_id=sid))
        return None
    params_raw = raw.get("parameters") or {}
    if not isinstance(params_raw, dict):
        errors.append(_err(
            "E_STD_PARAM",
            f"samples[{index}] ({sid}): parameters must be an object",
            sample_id=sid, method=method))
        return None
    params: dict = {}
    if method == "moving_average":
        window = _as_int(params_raw.get("window",
                                        params_raw.get("window_years")))
        if window is None or window < 3 or window % 2 == 0:
            errors.append(_err(
                "E_STD_PARAM",
                f"{sid or f'samples[{index}]'}: moving_average needs an odd "
                f"integer window >= 3, got "
                f"{params_raw.get('window')!r}",
                sample_id=sid, method=method, extra={"param": "window"}))
            return None
        params["window"] = window
    return {"sample_id": sid, "method": method, "parameters": params,
            "note": raw.get("note", "")}


def normalize_config(raw_config) -> dict:
    """Validate and canonicalise a submitted plan config.

    Raises :class:`StandardizationError` listing every structural problem
    (sample existence and mapping availability are checked separately,
    while building a preview).
    """
    errors: list[dict] = []
    if not isinstance(raw_config, dict):
        raise StandardizationError([_err(
            "E_STD_SHAPE",
            "config must be an object with a 'samples' list")])
    raw_samples = raw_config.get("samples")
    if not isinstance(raw_samples, list) or not raw_samples:
        raise StandardizationError([_err(
            "E_STD_SHAPE",
            "samples must be a non-empty list of per-sample choices")])
    samples = []
    seen = set()
    for i, raw in enumerate(raw_samples):
        norm = _normalize_sample(raw, i, errors)
        if norm is not None:
            if norm["sample_id"] in seen:
                errors.append(_err(
                    "E_STD_DUPLICATE_SAMPLE",
                    f"sample {norm['sample_id']} is configured more than "
                    f"once; a sample has exactly one detrending choice",
                    sample_id=norm["sample_id"],
                    method=norm["method"]))
            seen.add(norm["sample_id"])
            samples.append(norm)
    if errors:
        raise StandardizationError(errors)
    return {"samples": samples}


# ---------------------------------------------------------------------------
# Source year mapping (from the live hypothesis / adopted correction)
# ---------------------------------------------------------------------------

def source_mapping(conn, hypothesis: str, sample_id: str) -> dict:
    """The mapping the standardization reads for one sample.

    ``{"placement", "mapping", "correction_versions", "false_rings"}``;
    dated rows only for ``mapping`` (false rings cannot be dated but are
    listed separately so the frozen source is complete).  Raises KeyError
    when the sample is unknown and :class:`StandardizationError` when the
    hypothesis does not place it.
    """
    series = db.get_series(conn, sample_id)
    if series is None:
        raise KeyError(f"sample not found: {sample_id}")
    corr_rows = db.correction_maps_of(conn, hypothesis).get(sample_id)
    if corr_rows:
        from . import corrections
        mapping = [{"year": r["year"], "seq": r["seq"],
                    "width": r["width"], "role": r["role"]}
                   for r in corr_rows]
        drafts = {}
        false_rings = []
        for r in corr_rows:
            drafts.setdefault(r["draft_id"], r["draft_version"])
        # rebuild the false-ring rows straight from each contributing draft
        for draft_id, dv in sorted(drafts.items()):
            head = db.get_correction(conn, draft_id)
            ver = db.get_correction_version(conn, draft_id, dv)
            rebuilt = corrections.build_mapping(series, ver["events"],
                                                head["offset"])
            false_rings.extend(
                {"seq": m["seq"], "width": m["width"]}
                for m in rebuilt if m["role"] == "false_ring")
        return {"placement": "correction",
                "correction_versions": [
                    {"draft_id": did, "adopted_version": ver}
                    for did, ver in sorted(drafts.items())],
                "false_rings": false_rings,
                "mapping": mapping}
    locks = db.locks_of(conn, hypothesis)
    if sample_id in locks:
        offset = locks[sample_id]
        mapping = [{"year": offset + r["seq"] - 1, "seq": r["seq"],
                    "width": r["width"],
                    "role": "missing" if r["missing"] else "ring"}
                   for r in series["rings"]]
        return {"placement": "lock", "offset": offset,
                "correction_versions": [], "false_rings": [],
                "mapping": mapping}
    raise StandardizationError([_err(
        "E_NO_MAPPING",
        f"sample {sample_id!r} has no lock or adopted correction mapping "
        f"in hypothesis {hypothesis!r}; date it before standardizing",
        sample_id=sample_id)])


def dated_rows(source: dict) -> list[dict]:
    """Dated mapping rows in calendar order (false rings are absent)."""
    return sorted((r for r in source["mapping"]
                   if r["year"] is not None),
                  key=lambda r: r["year"])


# ---------------------------------------------------------------------------
# Detrending methods
# ---------------------------------------------------------------------------

def _valid_points(rows: list[dict]) -> list[dict]:
    """Positive-width dated rows -- the only points the fit is allowed to
    see.  Zero-width missing rings stay in the index but never fit."""
    return [r for r in rows if r["width"] > 0]


def _fit_mean(rows, valid, *, sample_id):
    expected_value = statistics.fmean(r["width"] for r in valid)
    if not expected_value > 0:
        raise StandardizationError([_err(
            "E_EXPECTED_NONPOSITIVE",
            f"{sample_id}: horizontal mean expected value is "
            f"{expected_value}, not positive",
            sample_id=sample_id, method="mean")])
    curve = {r["year"]: expected_value for r in rows}
    diagnostics = {"method": "mean",
                   "expected": expected_value,
                   "n_valid": len(valid)}
    return curve, diagnostics


def _solve3(a, b):
    """Gaussian elimination for a 3x3 linear system ``a x = b``.

    Returns None when the matrix is (numerically) singular.
    """
    m = [list(a[i]) + [b[i]] for i in range(3)]
    for col in range(3):
        piv = max(range(col, 3), key=lambda r: abs(m[r][col]))
        if abs(m[piv][col]) < 1e-14:
            return None
        m[col], m[piv] = m[piv], m[col]
        pivval = m[col][col]
        for r in range(col + 1, 3):
            factor = m[r][col] / pivval
            if factor:
                for c in range(col, 4):
                    m[r][c] -= factor * m[col][c]
    x = [0.0, 0.0, 0.0]
    for i in range(2, -1, -1):
        rest = sum(m[i][j] * x[j] for j in range(i + 1, 3))
        x[i] = (m[i][3] - rest) / m[i][i]
    return x


def _fit_negative_exponential(rows, valid, *, sample_id):
    """Fit ``a*exp(b*year) + d`` (b <= 0) with damped Gauss-Newton.

    The constrained parameters are reparameterised as ``a = exp(p1)``,
    ``b = -exp(p2)`` (strictly declining), ``d = softplus(p3)``, so every
    parameter vector stays feasible.  Non-convergence after
    :data:`MAX_FIT_ITERATIONS` (or an unbounded / non-finite fit) is
    reported as ``E_EXP_NOT_CONVERGED`` -- no fallback curve is fitted.
    """
    years = [float(r["year"]) for r in valid]
    widths = [float(r["width"]) for r in valid]
    y0 = years[0]
    span = max(max(years) - y0, 1.0)
    w_mean = statistics.fmean(widths)

    # Normalise the design so Gauss-Newton stays well-conditioned:
    # dimensionless time u=(year-y0)/span and widths v=width/mean.
    # Fit v = A*exp(B*u) + D with A,D > 0 and B <= 0 (strictly declining),
    # then convert back to the physical curve.  B can take any negative
    # magnitude, so it is kept unconstrained (its sign is forced in the
    # model) rather than reparameterised through an exponential.
    us = [(y - y0) / span for y in years]
    vs = [w / w_mean for w in widths]

    # initial guess: A ~ 0.25 (v starts ~1.15 and D ~0.9), decline
    # exp(-0.25) across the span, asymptote D ~ 0.9 of the mean
    p = [math.log(0.25), -0.25, math.log(math.expm1(0.9))]

    def unpack(par):
        A = math.exp(max(min(par[0], 700), -700))
        B = -math.exp(max(min(par[1], 700), -700)) if par[1] < 20 \
            else -par[1]
        # allow arbitrary negative magnitude through a sign-forced free B:
        B = -math.exp(max(min(par[1], 700), -700))
        D = math.log1p(math.exp(max(min(par[2], 700), -700)))
        return A, B, D

    def model(par, u):
        A, B, D = unpack(par)
        return A * math.exp(B * u) + D

    def sse(par):
        return sum((model(par, u) - v) ** 2
                   for u, v in zip(us, vs))

    best_sse = sse(p)
    converged = False
    improved_once = False
    lam = 1e-3
    _iteration = 0
    for _iteration in range(MAX_FIT_ITERATIONS):
        A, B, D = unpack(p)
        # Jacobian of the residual w.r.t. (p1, p2, p3)
        jac, resid = [], []
        for u, v in zip(us, vs):
            e = math.exp(B * u)
            f = A * e + D
            dA = A * e                 # df / dp1
            dB = A * e * B * u         # df / dp2 (B = -exp(p2))
            dD = 1.0 / (1.0 + math.exp(-max(min(p[2], 700), -700)))
            jac.append([dA, dB, dD])
            resid.append(v - f)
        jtj = [[sum(jac[r][i] * jac[r][k] for r in range(len(jac)))
                for k in range(3)] for i in range(3)]
        jtr = [sum(jac[r][i] * resid[r] for r in range(len(jac)))
               for i in range(3)]
        # scaled gradient: g_i = (J'r)_i / max(1, diag_i) -- the right
        # convergence measure in the Marquardt-scaled parameter space
        grad_norm = max(abs(g) / max(1.0, abs(jtj[i][i]))
                        for i, g in enumerate(jtr))
        if grad_norm < GRADIENT_TOLERANCE and improved_once:
            converged = True
            break
        # Marquardt damping on the *normalised* normal equations so all
        # three parameters share one scale despite the flat A/D valley
        diag = [math.sqrt(max(jtj[i][i], 1e-300)) for i in range(3)]

        def scaled_solve(damping):
            reg = [[jtj[i][k] / (diag[i] * diag[k]) for k in range(3)]
                   for i in range(3)]
            for i in range(3):
                reg[i][i] += damping
            rhs = [jtr[i] / diag[i] for i in range(3)]
            step = _solve3(reg, rhs)
            if step is None:
                return None
            return [step[i] / diag[i] for i in range(3)]

        step_lam = lam
        solved = None
        for _trial in range(40):
            delta = scaled_solve(step_lam)
            if delta is not None:
                # capped over-relaxation helps traverse shallow valleys
                scale = 1.0
                for _relax in range(6):
                    cand = [p[i] + scale * delta[i] for i in range(3)]
                    if all(math.isfinite(x) for x in cand):
                        try:
                            cand_sse = sse(cand)
                        except (OverflowError, ValueError):
                            cand_sse = float("inf")
                        if (math.isfinite(cand_sse)
                                and cand_sse < best_sse):
                            solved = (cand, cand_sse, scale)
                            break
                    scale *= 0.5
                if solved is not None:
                    break
            step_lam *= 10.0
        if solved is None:
            # no feasible descent direction remains: with a finite fit and
            # at least one improvement the stationary point is the answer
            converged = improved_once and math.isfinite(best_sse)
            break
        new_p, new_sse, used_scale = solved
        rel = (best_sse - new_sse) / max(best_sse, 1e-300)
        p, best_sse = new_p, new_sse
        improved_once = True
        # a full step that helped -> trust Gauss-Newton more; a shortened
        # step -> lean back on steepest descent
        lam = max(lam * (1.0 if used_scale == 1.0 else 3.0) / 5.0, 1e-12)
        if rel < FIT_TOLERANCE:
            converged = True
            break

    A, B, D = unpack(p)
    if (not converged or not math.isfinite(best_sse)
            or not all(math.isfinite(v) for v in (A, B, D))):
        raise StandardizationError([_err(
            "E_EXP_NOT_CONVERGED",
            f"{sample_id}: negative exponential fit did not converge "
            f"within {MAX_FIT_ITERATIONS} iterations from {len(valid)} "
            f"valid points; the method is kept unchanged",
            sample_id=sample_id, method="negative_exponential",
            extra={"n_valid": len(valid),
                   "iterations": _iteration + 1})])
    # convert back to physical widths, anchored at the first dated year
    # (the a*exp(b*year) form itself can overflow for large calendar
    # years, so the curve is always evaluated from year - origin_year):
    # f(year) = a0*exp(b*(year-y0)) + d with a0 = w_mean*A
    b = B / span
    a0 = w_mean * A
    d = w_mean * D
    curve = {}
    for r in rows:
        val = a0 * math.exp(b * (r["year"] - y0)) + d
        curve[r["year"]] = val

    fitted = [w_mean * model(p, u) for u in us]
    ss_res = sum((w - f) ** 2 for w, f in zip(widths, fitted))
    ss_tot = sum((w - w_mean) ** 2 for w in widths)
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0 else None
    rmse = math.sqrt(ss_res / len(widths))
    diagnostics = {"method": "negative_exponential",
                   "n_valid": len(valid),
                   "iterations": _iteration + 1,
                   "parameters": {"a0": a0, "b_per_year": b, "d": d,
                                  "origin_year": y0,
                                  "form": "a0*exp(b*(year-origin_year))+d",
                                  "normalized": {"A": A, "B": B, "D": D,
                                                 "scale_width": w_mean,
                                                 "span_years": span}},
                   "r_squared": r_squared,
                   "rmse": rmse,
                   "first_expected": curve[min(curve)],
                   "last_expected": curve[max(curve)]}
    return curve, diagnostics


def _fit_moving_average(rows, valid, *, sample_id, window: int):
    """Fixed-window centred moving average of the valid widths.

    Every dated position takes the mean of the valid (positive) points in
    the symmetric ``[y-h, y+h]`` window.  A position whose window reaches
    beyond the dated span, or whose window keeps fewer than
    :data:`MA_MIN_WINDOW_POINTS` valid points, is reported as
    ``E_WINDOW_TOO_SHORT`` with its measurement seq -- the curve is not
    shortened and the method is not replaced.
    """
    half = window // 2
    by_year = {r["year"]: r for r in rows}
    valid_by_year = {r["year"]: r["width"] for r in valid}
    years = [r["year"] for r in rows]
    y_min, y_max = min(years), max(years)
    curve = {}
    short: list[int] = []
    truncated: list[int] = []
    for r in rows:
        y = r["year"]
        lo, hi = y - half, y + half
        vals = [valid_by_year[yy] for yy in range(lo, hi + 1)
                if yy in valid_by_year]
        if lo < y_min or hi > y_max:
            # note truncation but only fail when the data inside are too
            # few (a short edge window with enough points still fits)
            if r["seq"] is not None:
                truncated.append(r["seq"])
        if len(vals) < MA_MIN_WINDOW_POINTS:
            if r["seq"] is not None:
                short.append(r["seq"])
            continue
        curve[y] = statistics.fmean(vals)
    if short:
        shown = short[:20]
        extra = {"n_seqs": len(short), "seqs": shown, "window": window,
                 "min_points": MA_MIN_WINDOW_POINTS}
        msg = (f"{sample_id}: {len(short)} position(s) keep fewer than "
               f"{MA_MIN_WINDOW_POINTS} valid points inside the centred "
               f"{window}-year window (measurement seqs {shown}"
               f"{', ...' if len(short) > 20 else ''}); the method is "
               f"kept unchanged")
        raise StandardizationError([_err(
            "E_WINDOW_TOO_SHORT", msg, sample_id=sample_id,
            seq=short[0], method="moving_average", extra=extra)])
    diagnostics = {"method": "moving_average",
                   "window": window, "half_window": half,
                   "n_valid": len(valid),
                   "n_truncated_edge_positions": len(truncated),
                   "truncated_edge_seqs": truncated[:20]}
    return curve, diagnostics


def evaluate_sample(source: dict, choice: dict) -> dict:
    """Detrend one sample.

    Returns ``{"sample_id", "method", "parameters", "placement",
    "n_dated", "n_valid", "rows", "curve", "diagnostics", "indices"}`` or
    raises :class:`StandardizationError` localised to the sample.
    """
    sample_id = choice["sample_id"]
    method = choice["method"]
    rows = dated_rows(source)
    valid = _valid_points(rows)
    if len(valid) < MIN_VALID_POINTS:
        raise StandardizationError([_err(
            "E_TOO_FEW_VALID_POINTS",
            f"{sample_id}: only {len(valid)} valid (positive, dated) "
            f"point(s), at least {MIN_VALID_POINTS} are required to fit "
            f"{method}; missing rings keep zero width and false rings do "
            f"not fit",
            sample_id=sample_id, method=method,
            extra={"n_valid": len(valid),
                   "min_valid": MIN_VALID_POINTS})])
    if method == "mean":
        curve, diagnostics = _fit_mean(rows, valid, sample_id=sample_id)
    elif method == "negative_exponential":
        curve, diagnostics = _fit_negative_exponential(
            rows, valid, sample_id=sample_id)
    else:
        curve, diagnostics = _fit_moving_average(
            rows, valid, sample_id=sample_id,
            window=choice["parameters"]["window"])

    # explicit positivity scan over every dated position (incl. gaps)
    bad_seqs = [r["seq"] for r in rows
                if curve.get(r["year"], 0.0) <= 0 and r["seq"] is not None]
    if bad_seqs:
        raise StandardizationError([_err(
            "E_EXPECTED_NONPOSITIVE",
            f"{sample_id}: fitted expected growth is non-positive at "
            f"{len(bad_seqs)} position(s) (measurement seqs "
            f"{bad_seqs[:20]}); the index cannot be formed",
            sample_id=sample_id, method=method,
            extra={"seqs": bad_seqs[:20], "n_seqs": len(bad_seqs)})])

    out_rows = []
    indices = {}
    for r in rows:
        expected = curve[r["year"]]
        width = r["width"]
        index = width / expected if width > 0 else 0.0
        indices[r["year"]] = index
        out_rows.append({"year": r["year"], "seq": r["seq"],
                         "width": width, "role": r["role"],
                         "expected": expected, "index": index})
    positives = [row["index"] for row in out_rows if row["width"] > 0]
    diagnostics = {**diagnostics,
                   "index_mean": (statistics.fmean(positives)
                                  if positives else None),
                   "index_min": (min(positives) if positives else None),
                   "index_max": (max(positives) if positives else None)}
    return {"sample_id": sample_id, "method": method,
            "parameters": choice.get("parameters", {}),
            "note": choice.get("note", ""),
            "placement": source["placement"],
            "offset": source.get("offset"),
            "correction_versions": source.get("correction_versions", []),
            "false_rings": source.get("false_rings", []),
            "n_dated": len(rows), "n_valid": len(valid),
            "rows": out_rows, "curve": curve,
            "indices": indices, "diagnostics": diagnostics,
            "valid": True}


# ---------------------------------------------------------------------------
# Whole-plan preview / validation
# ---------------------------------------------------------------------------

def preview_config(conn, hypothesis: str, config: dict) -> dict:
    """Evaluate every configured sample against the live mapping.

    Samples that fail appear in ``samples`` with ``valid: false`` and an
    ``error`` object; nothing is raised, so the caller can render the
    failures.  ``all_valid`` is the gate for leaving the draft state.
    """
    results, errors = [], []
    for choice in config["samples"]:
        sid = choice["sample_id"]
        try:
            source = source_mapping(conn, hypothesis, sid)
            result = evaluate_sample(source, choice)
            results.append(result)
        except StandardizationError as e:
            errors.extend(e.errors)
            results.append({"sample_id": sid, "method": choice["method"],
                            "parameters": choice.get("parameters", {}),
                            "valid": False, "error": e.errors[0],
                            "errors": e.errors, "rows": [],
                            "n_dated": 0, "n_valid": 0})
        except KeyError:
            err = _err("E_SAMPLE_NOT_FOUND",
                       f"sample not found: {sid}", sample_id=sid,
                       method=choice["method"])
            errors.append(err)
            results.append({"sample_id": sid, "method": choice["method"],
                            "valid": False, "error": err,
                            "errors": [err], "rows": [],
                            "n_dated": 0, "n_valid": 0})
    return {"hypothesis": hypothesis, "samples": results,
            "all_valid": not errors, "errors": errors,
            "n_samples": len(results), "n_valid_samples":
                sum(1 for r in results if r.get("valid"))}


def _public_sample(result: dict) -> dict:
    """JSON view: rows stay, internal lookup dicts are dropped."""
    out = {k: v for k, v in result.items() if k not in ("curve", "indices")}
    out["curve"] = [{"year": y, "expected": result["curve"][y]}
                    for y in sorted(result["curve"])]
    return out


def preview_public(conn, hypothesis: str, config: dict) -> dict:
    preview = preview_config(conn, hypothesis, config)
    preview["samples"] = [
        _public_sample(r) if r.get("valid")
        else {k: v for k, v in r.items() if k not in ("curve", "indices")}
        for r in preview["samples"]]
    return preview


# ---------------------------------------------------------------------------
# Plan lifecycle
# ---------------------------------------------------------------------------

def _require_plan(conn, std_id) -> dict:
    plan = db.get_standardization(conn, std_id)
    if plan is None:
        raise KeyError(f"standardization plan not found: {std_id}")
    return plan


def create_plan(conn, *, name: str, hypothesis: str, samples: list[dict],
                note: str = "") -> dict:
    if db.get_standardization_by_name(conn, name) is not None:
        raise StandardizationError([_err(
            "E_STD_EXISTS", f"standardization plan already exists: {name}")])
    if db.get_hypothesis(conn, hypothesis, with_locks=False) is None:
        raise KeyError(f"hypothesis not found: {hypothesis}")
    config = normalize_config({"samples": samples})
    # sample existence / mapping problems do not block a draft, but the
    # chosen samples must at least be known to the lab
    for choice in config["samples"]:
        if db.get_series(conn, choice["sample_id"]) is None:
            raise KeyError(f"sample not found: {choice['sample_id']}")
    std_id = db.create_standardization(conn, name=name, hypothesis=hypothesis,
                                       config=config, note=note)
    return plan_detail(conn, std_id)


def add_version(conn, std_id, *, samples: list[dict],
                note: str = "") -> dict:
    plan = _require_plan(conn, std_id)
    if plan["status"] in ("adopted", "retired"):
        raise StandardizationError([_err(
            "E_STD_IMMUTABLE",
            f"plan {std_id} is {plan['status']}; its configuration is "
            f"frozen -- create a new plan instead",
            extra={"status": plan["status"]})])
    config = normalize_config({"samples": samples})
    for choice in config["samples"]:
        if db.get_series(conn, choice["sample_id"]) is None:
            raise KeyError(f"sample not found: {choice['sample_id']}")
    version = db.save_standardization_version(
        conn, std_id, config, note=note)
    # editing a validated plan sends it back to the drafting table
    if plan["status"] == "validated":
        db.mark_standardization_status(conn, std_id, "draft")
    return plan_detail(conn, std_id, version=version)


def validate_plan(conn, std_id, *, version: int | None = None) -> dict:
    """All-samples gate; the plan becomes ``validated`` only when clean."""
    plan = _require_plan(conn, std_id)
    if plan["status"] == "retired":
        raise StandardizationError([_err(
            "E_STD_RETIRED", f"plan {std_id} is retired",
            extra={"status": "retired"})])
    ver = version or plan["latest_version"]
    row = db.get_std_version(conn, std_id, ver)
    if row is None:
        raise KeyError(f"plan {std_id} has no version {ver}")
    preview = preview_public(conn, plan["hypothesis"], row["config"])
    if not preview["all_valid"]:
        raise StandardizationError(preview["errors"])
    new_status = plan["status"]
    if new_status == "draft" and ver == plan["latest_version"]:
        db.mark_standardization_status(conn, std_id, "validated")
        new_status = "validated"
    return {"standardization_id": std_id, "version": ver,
            "status": new_status,
            "n_samples": preview["n_samples"],
            "n_valid_samples": preview["n_valid_samples"],
            "preview": preview}


def _build_frozen(conn, plan: dict, config: dict) -> tuple[dict, dict]:
    """Freezable source mapping + fitted curves for an all-valid config."""
    preview = preview_config(conn, plan["hypothesis"], config)
    if not preview["all_valid"]:
        raise StandardizationError(preview["errors"])
    source, curves = {}, {}
    for result in preview["samples"]:
        sid = result["sample_id"]
        src = source_mapping(conn, plan["hypothesis"], sid)
        source[sid] = src
        curves[sid] = {
            "method": result["method"],
            "parameters": result["parameters"],
            "placement": result["placement"],
            "offset": result.get("offset"),
            "correction_versions": result["correction_versions"],
            "false_rings": result.get("false_rings", []),
            "n_dated": result["n_dated"],
            "n_valid": result["n_valid"],
            "diagnostics": result["diagnostics"],
            "expected": {str(y): result["curve"][y]
                         for y in sorted(result["curve"])},
            "indices": {str(y): result["indices"][y]
                        for y in sorted(result["indices"])},
            "rows": result["rows"],
        }
    return source, curves


def adopt_plan(conn, std_id, *, version: int | None = None) -> dict:
    plan = _require_plan(conn, std_id)
    if plan["status"] == "retired":
        raise StandardizationError([_err(
            "E_STD_RETIRED", f"plan {std_id} is retired",
            extra={"status": "retired"})])
    if plan["status"] == "adopted":
        raise StandardizationError([_err(
            "E_STD_ADOPTED",
            f"plan {std_id} is already adopted at version "
            f"{plan['adopted_version']}; retire it before adopting a "
            f"different plan",
            extra={"adopted_version": plan["adopted_version"]})])
    ver = version or plan["latest_version"]
    row = db.get_std_version(conn, std_id, ver)
    if row is None:
        raise KeyError(f"plan {std_id} has no version {ver}")
    source, curves = _build_frozen(conn, plan, row["config"])
    db.freeze_std_version(conn, std_id, ver, source, curves)
    db.mark_standardization_status(conn, std_id, "adopted",
                                   adopted_version=ver)
    out = plan_detail(conn, std_id, version=ver)
    out["status"] = "adopted"
    return out


def retire_plan(conn, std_id) -> dict:
    plan = _require_plan(conn, std_id)
    if plan["status"] == "retired":
        raise StandardizationError([_err(
            "E_STD_RETIRED", f"plan {std_id} is already retired",
            extra={"status": "retired"})])
    db.mark_standardization_status(conn, std_id, "retired")
    return {"standardization_id": std_id, "status": "retired",
            "adopted_version": plan["adopted_version"]}


# ---------------------------------------------------------------------------
# Reading back
# ---------------------------------------------------------------------------

def plan_detail(conn, std_id, *, version: int | None = None,
                config_override: dict | None = None) -> dict:
    """Plan metadata plus the evaluated version (frozen curves if
    adopted, otherwise a live recomputation)."""
    plan = _require_plan(conn, std_id)
    if config_override is not None:
        config = normalize_config(config_override)
        version = None
    else:
        ver = version or plan["latest_version"]
        row = db.get_std_version(conn, std_id, ver)
        if row is None:
            raise KeyError(f"plan {std_id} has no version {ver}")
        config = row["config"]
        version = ver

    if (config_override is None and plan["status"] == "adopted"
            and version == plan["adopted_version"] and row["curves"]):
        frozen = row["curves"]
        samples = []
        for sid, c in frozen.items():
            samples.append({
                "sample_id": sid, "method": c["method"],
                "parameters": c["parameters"], "note": c.get("note", ""),
                "valid": True, "frozen": True,
                "placement": c["placement"], "offset": c.get("offset"),
                "correction_versions": c.get("correction_versions", []),
                "n_dated": c["n_dated"], "n_valid": c["n_valid"],
                "diagnostics": c["diagnostics"],
                "rows": c["rows"],
                "curve": [{"year": int(y), "expected": v}
                          for y, v in sorted(c["expected"].items(),
                                             key=lambda kv: int(kv[0]))]})
        preview = {"hypothesis": plan["hypothesis"], "samples": samples,
                   "all_valid": True, "errors": [],
                   "n_samples": len(samples),
                   "n_valid_samples": len(samples), "frozen": True}
    else:
        preview = preview_public(conn, plan["hypothesis"], config)
        preview["frozen"] = False

    source_status = _source_status(conn, plan, config) if (
        plan["status"] == "adopted" and plan["adopted_version"] == version
        and config_override is None) else None
    return {
        "standardization_id": std_id,
        "name": plan["name"],
        "hypothesis": plan["hypothesis"],
        "note": plan["note"],
        "status": plan["status"],
        "latest_version": plan["latest_version"],
        "adopted_version": plan["adopted_version"],
        "version": version,
        "config": config,
        "preview": preview,
        "source_status": source_status,
        "created_at": plan["created_at"],
        "updated_at": plan["updated_at"],
    }


def _source_status(conn, plan: dict, config: dict) -> dict:
    """Frozen source mappings vs the hypothesis as it stands now."""
    version = plan["adopted_version"]
    row = db.get_std_version(conn, plan["id"], version)
    frozen = (row or {}).get("source_mapping") or {}
    per = {}
    for sid in sorted(frozen):
        try:
            live = source_mapping(conn, plan["hypothesis"], sid)
        except StandardizationError:
            per[sid] = {"stale": True,
                        "reason": "sample no longer has an adopted year "
                                  "mapping in the hypothesis"}
            continue
        same = (live["placement"] == frozen[sid]["placement"]
                and live["mapping"] == frozen[sid]["mapping"]
                and live.get("correction_versions")
                == frozen[sid].get("correction_versions"))
        per[sid] = {"stale": not same, "placement": live["placement"],
                    "reason": None if same else
                    "the live year mapping (lock/correction) differs from "
                    "the mapping frozen at adoption; stored indices keep "
                    "using the frozen mapping"}
    stale = any(p["stale"] for p in per.values())
    return {"stale": stale,
            "reason": ("at least one source mapping changed since "
                       "adoption; stored indices keep using the frozen "
                       "mapping"
                       if stale else None),
            "per_sample": per}


def compare_versions(conn, std_id, a: int, b: int) -> dict:
    plan = _require_plan(conn, std_id)
    rows = {}
    for v in (a, b):
        row = db.get_std_version(conn, std_id, v)
        if row is None:
            raise KeyError(f"plan {std_id} has no version {v}")
        rows[v] = {c["sample_id"]: c for c in row["config"]["samples"]}
    sids = sorted(set(rows[a]) | set(rows[b]))
    added, removed, changed = [], [], []
    for sid in sids:
        ca, cb = rows[a].get(sid), rows[b].get(sid)
        if ca is None:
            added.append({"sample_id": sid, "method": cb["method"],
                          "parameters": cb["parameters"]})
        elif cb is None:
            removed.append({"sample_id": sid, "method": ca["method"],
                            "parameters": ca["parameters"]})
        elif (ca["method"] != cb["method"]
                or ca["parameters"] != cb["parameters"]):
            changed.append({"sample_id": sid,
                            "method_a": ca["method"],
                            "method_b": cb["method"],
                            "method_changed": ca["method"] != cb["method"],
                            "parameters_a": ca["parameters"],
                            "parameters_b": cb["parameters"]})

    def diag(v):
        if plan["status"] == "adopted" and plan["adopted_version"] == v:
            frozen = db.get_std_version(conn, std_id, v)["curves"] or {}
            return {sid: c.get("diagnostics") for sid, c in frozen.items()}
        return {r["sample_id"]: r.get("diagnostics")
                for r in preview_config(
                    conn, plan["hypothesis"],
                    {"samples": list(rows[v].values())})["samples"]
                if r.get("valid")}

    da, dbg = diag(a), diag(b)
    method_set_a = sorted({c["method"] for c in rows[a].values()})
    method_set_b = sorted({c["method"] for c in rows[b].values()})
    return {
        "standardization_id": std_id,
        "version_a": a, "version_b": b,
        "samples_added": added, "samples_removed": removed,
        "samples_changed": changed,
        "methods_a": method_set_a, "methods_b": method_set_b,
        "diagnostics_a": da, "diagnostics_b": dbg,
    }


def plan_report(conn, std_id) -> dict:
    """Full JSON export of a plan and every version."""
    plan = _require_plan(conn, std_id)
    versions = []
    for v in range(1, plan["latest_version"] + 1):
        versions.append(plan_detail(conn, std_id, version=v))
    return {"report_type": "tree_ring_standardization",
            "generated_at": db.now_iso(),
            "plan": {k: plan[k] for k in
                     ("id", "name", "hypothesis", "note", "status",
                      "latest_version", "adopted_version",
                      "created_at", "updated_at")},
            "versions": versions}


# ---------------------------------------------------------------------------
# Adopted indices for downstream jobs (master / crossdate / stability)
# ---------------------------------------------------------------------------

def resolve_adopted(conn, std_id, version: int | None = None, *,
                    hypothesis: str | None = None) -> dict:
    """Load a frozen adopted version for use by other calculations.

    Returns ``{"id", "version", "hypothesis", "indices", "curves",
    "source_mapping"}``.  Only adopted plans resolve; a hypothesis mismatch
    and a non-adopted status are 422s, a missing plan/version a 404.
    """
    plan = db.get_standardization(conn, int(std_id))
    if plan is None:
        raise KeyError(f"standardization plan not found: {std_id}")
    ver = version or plan["adopted_version"]
    if plan["status"] != "adopted" or not ver:
        raise StandardizationError([_err(
            "E_STD_NOT_ADOPTED",
            f"standardization plan {std_id} is not adopted (status "
            f"{plan['status']}); only an adopted version can back a "
            f"chronology or job",
            extra={"status": plan["status"]})])
    row = db.get_std_version(conn, plan["id"], ver)
    if row is None:
        raise KeyError(f"plan {std_id} has no version {ver}")
    if hypothesis is not None and hypothesis != plan["hypothesis"]:
        raise StandardizationError([_err(
            "E_STD_HYPOTHESIS_MISMATCH",
            f"standardization plan {std_id} reads hypothesis "
            f"{plan['hypothesis']!r} but it was requested under "
            f"{hypothesis!r}",
            extra={"plan_hypothesis": plan["hypothesis"],
                   "requested_hypothesis": hypothesis})])
    curves = row["curves"] or {}
    indices = {sid: {int(y): v for y, v in c["indices"].items()}
               for sid, c in curves.items()}
    return {"id": plan["id"], "version": ver,
            "hypothesis": plan["hypothesis"], "indices": indices,
            "curves": curves,
            "source_mapping": row["source_mapping"],
            "config": row["config"]}
