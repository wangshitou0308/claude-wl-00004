"""Offline HTTP API (http.server based, standard library only).

Start with::

    python -m tree_ring_xdate --db tree_ring.db --host 127.0.0.1 --port 8000

All endpoints return JSON (except ``/`` which serves the built-in docs and
the ``?download=1`` report links).  There is no authentication: the API is
designed for a trusted lab network / single workstation.
"""

from __future__ import annotations

import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import (analysis, corrections, db, stability, standardization,
               validate)
from .docs import OPENAPI, HTML_DOCS

DEFAULT_HYPOTHESIS = "default"


# ---------------------------------------------------------------------------
# JSON helpers (NaN must never leak into JSON output)
# ---------------------------------------------------------------------------

def _json_default(o):
    raise TypeError(f"not JSON serialisable: {type(o).__name__}")


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, allow_nan=False,
                      default=_json_default, indent=2)


def _finite(obj):
    """Strip NaN/Inf defensively (statistics should never produce them)."""
    if isinstance(obj, float):
        return obj if obj == obj and obj not in (float("inf"),
                                                 float("-inf")) else None
    if isinstance(obj, dict):
        return {k: _finite(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_finite(v) for v in obj]
    return obj


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, **extra):
        super().__init__(message)
        self.status = status
        self.body = {"error": {"code": code, "message": message, **extra}}


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "TreeRingXDate/1.0"

    # injected by make_server:
    conn = None  # type: ignore[assignment]
    db_lock = None  # type: ignore[assignment]

    # -- logging: keep it quiet but useful --------------------------------
    def log_message(self, fmt, *args):
        self.server.log and self.server.log("%s - %s"
                                            % (self.address_string(),
                                               fmt % args))

    # -- transport ---------------------------------------------------------
    def _send(self, status, body: dict | list, *, kind="json",
              download_name=None):
        if kind == "html":
            data = body.encode("utf-8") if isinstance(body, str) else body
            ctype = "text/html; charset=utf-8"
        else:
            data = dumps(_finite(body)).encode("utf-8")
            ctype = "application/json; charset=utf-8"
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        if download_name:
            self.send_header(
                "Content-Disposition",
                f'attachment; filename="{download_name}"')
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        if length > 8 * 1024 * 1024:
            raise ApiError(413, "PAYLOAD_TOO_LARGE",
                           "request body exceeds 8 MiB")
        return self.rfile.read(length) if length else b""

    def _query(self) -> dict:
        parsed = urllib.parse.urlparse(self.path)
        q = {k: v[-1] for k, v in
             urllib.parse.parse_qs(parsed.query).items()}
        return parsed.path.rstrip("/") or "/", q

    def _json_body(self) -> dict:
        raw = self._read_body().decode("utf-8")
        if not raw.strip():
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ApiError(400, "E_BAD_JSON",
                           f"request body is not valid JSON: {e.msg}",
                           line=e.lineno)
        if not isinstance(data, dict):
            raise ApiError(400, "E_BAD_JSON",
                           "request body must be a JSON object")
        return data

    def _payload(self, query) -> tuple[str, str, str | None]:
        raw = self._read_body()
        ctype = self.headers.get("Content-Type", "")
        fname = query.get("filename")
        if "json" in ctype:
            fmt = "json"
        elif "csv" in ctype:
            fmt = "csv"
        elif fname and fname.lower().endswith(".json"):
            fmt = "json"
        else:
            stripped = raw.lstrip()
            fmt = "json" if stripped[:1] in (b"{", b"[") else "csv"
        return raw.decode("utf-8"), fmt, fname

    # -- entry point -------------------------------------------------------
    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods",
                         "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _dispatch(self, method):
        try:
            path, query = self._query()
            with self.db_lock:
                route(method, path, query, self)
        except ApiError as e:
            self._send(e.status, e.body)
        except corrections.CorrectionError as e:
            self._send(422, {"error": {"code": "CORRECTION_REJECTED",
                                       "message": str(e)},
                             "errors": e.errors})
        except stability.StabilityError as e:
            self._send(422, {"error": {"code": "STABILITY_REJECTED",
                                       "message": str(e)},
                             "errors": e.errors})
        except standardization.StandardizationError as e:
            self._send(422, {"error": {"code": "STANDARDIZATION_REJECTED",
                                       "message": str(e)},
                             "errors": e.errors})
        except KeyError as e:
            self._send(404, {"error": {"code": "NOT_FOUND",
                                      "message": str(e).strip("'")}})
        except ValueError as e:
            self._send(400, {"error": {"code": "BAD_REQUEST",
                                      "message": str(e)}})
        except Exception as e:  # never crash the server on one request
            self._send(500, {"error": {"code": "INTERNAL",
                                      "message": f"{type(e).__name__}: {e}"}})

    # ------------------------------------------------------------------
    # Endpoint implementations (called under db_lock)
    # ------------------------------------------------------------------
    def ep_root(self, query):
        self._send(200, HTML_DOCS, kind="html")

    def ep_openapi(self, query):
        self._send(200, OPENAPI)

    def ep_help(self, query):
        self._send(200, {"endpoints": _ENDPOINT_HELP})

    def ep_series_post(self, query):
        content, fmt, fname = self._payload(query)
        strict = query.get("strict", "0").lower() in ("1", "true", "yes")
        parsed = validate.parse_payload(
            content,
            content_type="application/" + fmt,
            filename=fname)
        # Strict mode: validate the *whole* batch first; never persist a
        # single ring when any sample was rejected.
        if strict and parsed["errors"]:
            self._send(422, {
                "saved": [],
                "n_saved": 0,
                "n_errors": len(parsed["errors"]),
                "errors": parsed["errors"],
                "message": ("strict mode: request rejected, nothing was "
                            "saved")})
            return
        saved = []
        for record in parsed["accepted"]:
            sid = db.upsert_series(self.conn, record)
            saved.append({"sample_id": record["sample_id"],
                          "series_id": sid,
                          "n_rings": record["n_rings"]
                          if "n_rings" in record else len(record["rings"]),
                          "known_start": record["known_start"],
                          "unit": record["unit"],
                          "has_missing": record["has_missing"],
                          "warnings": record["warnings"]})
        self._send(201 if saved else 200, {
            "format": parsed["format"],
            "saved": saved,
            "n_saved": len(saved),
            "n_errors": len(parsed["errors"]),
            "errors": parsed["errors"]})

    def ep_series_list(self, query):
        self._send(200, {"series": db.list_series(self.conn)})

    def ep_series_one(self, query, sid):
        s = db.get_series(self.conn, sid)
        if s is None:
            raise ApiError(404, "NOT_FOUND", f"sample not found: {sid}")
        self._send(200, s)

    def ep_series_delete(self, query, sid):
        ok = db.delete_series(self.conn, sid)
        if not ok:
            raise ApiError(404, "NOT_FOUND", f"sample not found: {sid}")
        self._send(200, {"deleted": sid})

    def ep_runs(self, query):
        self._send(200, {"runs": db.list_runs(
            self.conn, limit=int(query.get("limit", 50)))})

    # -- standardization pinning ------------------------------------------
    def _std(self, body, query, *, hypothesis=None):
        """Resolve an adopted standardization version for a calculation.

        Accepts standardization_id (or standardization) + optional
        standardization_version from either JSON body or query string.
        Old requests without a pin return None and keep their raw basis.
        """
        raw_id = (body.get("standardization_id")
                  if isinstance(body, dict) else None)
        if raw_id is None and isinstance(body, dict):
            raw_id = body.get("standardization")
        if raw_id is None:
            raw_id = query.get("standardization_id") \
                or query.get("standardization")
        if raw_id in (None, "", "none", "null"):
            return None
        ver = (body.get("standardization_version")
               if isinstance(body, dict) else None)
        if ver is None:
            ver = query.get("standardization_version")
        return standardization.resolve_adopted(
            self.conn, int(raw_id),
            int(ver) if ver not in (None, "") else None,
            hypothesis=hypothesis)

    def ep_crossdate(self, query):
        body = self._json_body() if self.command == "POST" else {}
        sid = body.get("sample_id") or query.get("sample_id")
        if not sid:
            raise ApiError(400, "MISSING_PARAM",
                           "sample_id is required")
        hypothesis = body.get("hypothesis", query.get("hypothesis"))
        std = self._std(body, query, hypothesis=hypothesis)
        params = {
            "reference": body.get("reference",
                                  query.get("reference", "master")),
            "min_overlap": int(body.get("min_overlap",
                                        query.get("min_overlap", 20))),
            "narrow_z": float(body.get("narrow_z",
                                       query.get("narrow_z", -1.0))),
            "narrow_q": float(body.get("narrow_q",
                                       query.get("narrow_q", 0.1))),
            "tolerance": float(body.get("tolerance",
                                        query.get("tolerance", 0.05))),
            "top_k": int(body.get("top_k", query.get("top_k", 10))),
            "hypothesis": hypothesis,
        }
        for k in ("offset_min", "offset_max"):
            v = body.get(k, query.get(k))
            params[k] = int(v) if v not in (None, "") else None
        if params["min_overlap"] < 3:
            raise ApiError(400, "BAD_REQUEST",
                           "min_overlap must be >= 3")
        result = analysis.crossdate(self.conn, sid, std=std, **params)
        run_id = db.save_run(self.conn, sid, params["reference"],
                             result["params"], result["candidates"])
        result["run_id"] = run_id
        self._send(200, result)

    def ep_hypothesis_list(self, query):
        self._send(200, {"hypotheses": db.list_hypotheses(self.conn)})

    def ep_hypothesis_create(self, query):
        body = self._json_body()
        name = body.get("name") or query.get("name")
        if not name:
            raise ApiError(400, "MISSING_PARAM", "hypothesis name required")
        if db.get_hypothesis(self.conn, name, with_locks=False):
            raise ApiError(409, "CONFLICT",
                           f"hypothesis already exists: {name}")
        hid = db.create_hypothesis(self.conn, name,
                                   body.get("note", ""))
        self._send(201, {"id": hid, "name": name,
                         "note": body.get("note", ""), "locks": []})

    def ep_hypothesis_get(self, query, hyp_name):
        h = db.get_hypothesis(self.conn, hyp_name)
        if h is None:
            raise ApiError(404, "NOT_FOUND",
                           f"hypothesis not found: {hyp_name}")
        self._send(200, h)

    def ep_hypothesis_delete(self, query, hyp_name):
        if not db.delete_hypothesis(self.conn, hyp_name):
            raise ApiError(404, "NOT_FOUND",
                           f"hypothesis not found: {hyp_name}")
        self._send(200, {"deleted": hyp_name})

    def ep_lock(self, query, hyp_name):
        body = self._json_body()
        sid = body.get("sample_id")
        if not sid:
            raise ApiError(400, "MISSING_PARAM", "sample_id is required")
        offset = body.get("offset", body.get("start_year",
                                             body.get("first_year")))
        if offset is None:
            raise ApiError(400, "MISSING_PARAM",
                           "offset (calendar year of ring 1) is required")
        offset = int(offset)
        s = db.get_series(self.conn, sid)
        if s is None:
            raise ApiError(404, "NOT_FOUND", f"sample not found: {sid}")
        if offset + len(s["rings"]) - 1 < 0:
            raise ApiError(400, "BAD_REQUEST",
                           "locked placement ends before year 1")
        run_id = body.get("run_id")
        lock = db.upsert_lock(self.conn, hyp_name, sid, offset, run_id)
        self._send(200, {"locked": lock,
                         "hypothesis": db.get_hypothesis(self.conn, hyp_name)})

    def ep_unlock(self, query, hyp_name):
        sid = query.get("sample_id")
        if not sid:
            raise ApiError(400, "MISSING_PARAM",
                           "sample_id query parameter is required")
        ok = db.remove_lock(self.conn, hyp_name, sid)
        self._send(200 if ok else 404,
                   {"unlocked": sid, "existed": ok})

    def ep_chronology_default(self, query):
        hypothesis = query.get("hypothesis", DEFAULT_HYPOTHESIS)
        if not db.get_hypothesis(self.conn, hypothesis, with_locks=False):
            db.create_hypothesis(self.conn, hypothesis,
                                 "auto-created working hypothesis")
        self._serve_chronology(hypothesis, query)

    def ep_chronology(self, query, hyp_name):
        if not db.get_hypothesis(self.conn, hyp_name, with_locks=False):
            raise ApiError(404, "NOT_FOUND",
                           f"hypothesis not found: {hyp_name}")
        self._serve_chronology(hyp_name, query)

    def _serve_chronology(self, hypothesis, query):
        std = self._std({}, query, hypothesis=hypothesis)
        chrono = analysis.build_chronology(
            self.conn, hypothesis,
            min_samples=int(query.get("min_samples", 3)),
            outlier_sd=float(query.get("outlier_sd", 2.0)),
            weak_correlation=float(query.get("weak_correlation", 0.3)),
            std=std)
        self._send(200, chrono)

    def ep_master(self, query):
        hypothesis = query.get("hypothesis", DEFAULT_HYPOTHESIS)
        if not db.get_hypothesis(self.conn, hypothesis, with_locks=False):
            if "hypothesis" in query:
                raise ApiError(404, "NOT_FOUND",
                               f"hypothesis not found: {hypothesis}")
            db.create_hypothesis(self.conn, hypothesis,
                                 "auto-created working hypothesis")
        std = self._std({}, query, hypothesis=hypothesis)
        yw, meta = analysis.build_reference(self.conn, "master", hypothesis,
                                            std=std)
        self._send(200, {"meta": meta,
                         "years": [{"year": y, "index": yw[y]}
                                   for y in sorted(yw)]})

    def ep_compare(self, query):
        a, b = query.get("a"), query.get("b")
        if not a or not b:
            raise ApiError(400, "MISSING_PARAM",
                           "query parameters 'a' and 'b' are required")
        self._send(200, analysis.compare_hypotheses(self.conn, a, b))

    def ep_compare_master(self, query, hyp_name):
        self._send(200, analysis.compare_to_master(self.conn, hyp_name))

    def ep_report(self, query, hyp_name):
        h = db.get_hypothesis(self.conn, hyp_name)
        if h is None:
            raise ApiError(404, "NOT_FOUND",
                           f"hypothesis not found: {hyp_name}")
        chrono = analysis.build_chronology(
            self.conn, hyp_name,
            min_samples=int(query.get("min_samples", 3)),
            outlier_sd=float(query.get("outlier_sd", 2.0)),
            weak_correlation=float(query.get("weak_correlation", 0.3)),
            std=self._std({}, query, hypothesis=hyp_name))
        report = {
            "report_type": "tree_ring_crossdating",
            "generated_at": db.now_iso(),
            "hypothesis": h,
            "chronology_summary": {
                "start": chrono["start"], "end": chrono["end"],
                "n_years": chrono["n_years"],
                "n_samples": chrono["n_samples"]},
            "yearly": chrono["yearly"],
            "flags": {
                "outliers": chrono["outliers"],
                "unit_mismatches": chrono["unit_mismatches"],
                "lock_conflicts": chrono["lock_conflicts"],
            },
            "series": db.list_series(self.conn),
            "thresholds": chrono["thresholds"],
        }
        download = query.get("download", "0").lower() in ("1", "true")
        self._send(200, report,
                   download_name=(f"xdate_{hyp_name}.json"
                                  if download else None))

    # -- correction drafts -------------------------------------------------
    def ep_correction_create(self, query):
        body = self._json_body()
        sid = body.get("sample_id") or query.get("sample_id")
        if not sid:
            raise ApiError(400, "MISSING_PARAM", "sample_id is required")
        offset = body.get("offset", body.get("start_year"))
        if offset is None:
            raise ApiError(400, "MISSING_PARAM",
                           "offset (calendar year of ring 1) is required")
        events = body.get("events")
        if events is None:
            raise ApiError(400, "MISSING_PARAM",
                           "events (missing_ring / false_ring list) is "
                           "required")
        run_id = body.get("run_id")
        draft = corrections.create_draft(
            self.conn, sample_id=sid, offset=int(offset), events=events,
            run_id=int(run_id) if run_id is not None else None,
            reference=body.get("reference", "master"),
            hypothesis=body.get("hypothesis"),
            min_overlap=int(body.get("min_overlap", 20)),
            narrow_z=float(body.get("narrow_z", -1.0)),
            narrow_q=float(body.get("narrow_q", 0.1)),
            note=body.get("note", ""))
        self._send(201, draft)

    def ep_correction_list(self, query):
        self._send(200, {"corrections": db.list_corrections(
            self.conn, sample_id=query.get("sample_id"))})

    def _correction_head(self, did):
        head = db.get_correction(self.conn, int(did))
        if head is None:
            raise ApiError(404, "NOT_FOUND",
                           f"correction draft not found: {did}")
        return head

    def ep_correction_get(self, query, did):
        head = self._correction_head(did)
        version = int(query.get("version", head["latest_version"]))
        row = db.get_correction_version(self.conn, int(did), version)
        if row is None:
            raise ApiError(404, "NOT_FOUND",
                           f"draft {did} has no version {version}")
        row["snapshot"] = head["snapshot"]
        row["candidate"] = head.get("candidate")
        self._send(200, corrections.version_public(
            corrections.evaluate_version(self.conn, row), with_mapping=True))

    def ep_correction_preview(self, query, did):
        # preview == evaluate a version against the frozen snapshot
        self.ep_correction_get(query, did)

    def ep_correction_versions(self, query, did):
        head = self._correction_head(did)
        rows = []
        for v in range(1, head["latest_version"] + 1):
            row = db.get_correction_version(self.conn, int(did), v)
            rows.append({"version": v, "note": row["note"],
                         "n_events": len(row["events"]),
                         "events": row["events"],
                         "created_at": row["created_at"]})
        self._send(200, {"draft_id": int(did), "versions": rows,
                         "latest_version": head["latest_version"],
                         "status": head["status"]})

    def ep_correction_new_version(self, query, did):
        self._correction_head(did)
        body = self._json_body()
        events = body.get("events")
        if events is None:
            raise ApiError(400, "MISSING_PARAM",
                           "events (missing_ring / false_ring list) is "
                           "required")
        out = corrections.add_version(self.conn, int(did), events=events,
                                      note=body.get("note", ""))
        self._send(201, out)

    def ep_correction_adopt(self, query, did):
        self._correction_head(did)
        body = self._json_body() if self.command == "POST" else {}
        hypothesis = (body.get("hypothesis") or query.get("hypothesis")
                      or DEFAULT_HYPOTHESIS)
        version = body.get("version", query.get("version"))
        out = corrections.adopt_draft(
            self.conn, int(did), hypothesis=hypothesis,
            version=int(version) if version is not None else None)
        self._send(200, out)

    def ep_correction_revoke(self, query, did):
        self._correction_head(did)
        out = corrections.revoke_draft(self.conn, int(did))
        self._send(200, out)

    def ep_correction_compare(self, query, did):
        self._correction_head(did)
        a, b = query.get("a"), query.get("b")
        if not a or not b:
            raise ApiError(400, "MISSING_PARAM",
                           "query parameters 'a' and 'b' (version numbers) "
                           "are required")
        self._send(200, corrections.compare_versions(
            self.conn, int(did), int(a), int(b)))

    def ep_correction_download(self, query, did):
        self._correction_head(did)
        report = corrections.draft_report(self.conn, int(did))
        download = query.get("download", "1").lower() in ("1", "true")
        self._send(200, report,
                   download_name=(f"correction_{did}.json"
                                  if download else None))

    # -- stability checks --------------------------------------------------
    def ep_stability_create(self, query):
        body = self._json_body()
        hypothesis = body.get("hypothesis") or query.get("hypothesis")
        if not hypothesis:
            raise ApiError(400, "MISSING_PARAM",
                           "hypothesis is required")
        sample_id = body.get("sample_id", query.get("sample_id")) or None
        std = self._std(body, query, hypothesis=hypothesis)
        result = stability.create_check(
            self.conn, hypothesis=hypothesis, sample_id=sample_id,
            reference=body.get("reference",
                               query.get("reference", "master")),
            window=int(body.get("window", query.get("window", 30))),
            step=int(body.get("step", query.get("step", 10))),
            min_valid_years=int(body.get(
                "min_valid_years", query.get("min_valid_years", 15))),
            run_threshold=int(body.get(
                "run_threshold", query.get("run_threshold", 3))),
            search_radius=int(body.get(
                "search_radius", query.get("search_radius", 3))),
            tolerance=float(body.get("tolerance",
                                     query.get("tolerance", 0.05))),
            narrow_z=float(body.get("narrow_z",
                                    query.get("narrow_z", -1.0))),
            narrow_q=float(body.get("narrow_q",
                                    query.get("narrow_q", 0.1))),
            note=body.get("note", ""), std=std)
        self._send(201, result)

    def ep_stability_list(self, query):
        self._send(200, {"checks": db.list_stability_checks(
            self.conn, hypothesis=query.get("hypothesis"),
            sample_id=query.get("sample_id"))})

    def ep_stability_get(self, query, cid):
        filters = {}
        for k in ("year_from", "year_to", "shift"):
            v = query.get(k)
            if v not in (None, ""):
                filters[k] = int(v)
        if query.get("status"):
            filters["status"] = query["status"]
        if query.get("sample_id"):
            filters["sample_id"] = query["sample_id"]
        self._send(200, stability.check_detail(self.conn, int(cid),
                                               filters=filters))

    def ep_stability_compare(self, query):
        a, b = query.get("a"), query.get("b")
        if not a or not b:
            raise ApiError(400, "MISSING_PARAM",
                           "query parameters 'a' and 'b' (check ids) are "
                           "required")
        self._send(200, stability.compare_checks(self.conn, int(a), int(b)))

    def ep_stability_download(self, query, cid):
        report = stability.check_report(self.conn, int(cid))
        download = query.get("download", "1").lower() in ("1", "true")
        self._send(200, report,
                   download_name=(f"stability_{cid}.json"
                                  if download else None))

    # -- standardization plans ---------------------------------------------
    def ep_std_create(self, query):
        body = self._json_body()
        name = body.get("name") or query.get("name")
        if not name:
            raise ApiError(400, "MISSING_PARAM", "name is required")
        hypothesis = (body.get("hypothesis")
                      or query.get("hypothesis"))
        if not hypothesis:
            raise ApiError(400, "MISSING_PARAM", "hypothesis is required")
        samples = body.get("samples")
        if samples is None:
            raise ApiError(400, "MISSING_PARAM",
                           "samples (per-sample detrending choices) is "
                           "required")
        plan = standardization.create_plan(
            self.conn, name=name, hypothesis=hypothesis,
            samples=samples, note=body.get("note", ""))
        self._send(201, plan)

    def ep_std_list(self, query):
        self._send(200, {"standardizations": db.list_standardizations(
            self.conn, status=query.get("status"),
            hypothesis=query.get("hypothesis"))})

    def _std_head(self, sid):
        plan = db.get_standardization(self.conn, int(sid))
        if plan is None:
            raise ApiError(404, "NOT_FOUND",
                           f"standardization plan not found: {sid}")
        return plan

    def ep_std_get(self, query, sid):
        self._std_head(sid)
        version = query.get("version")
        detail = standardization.plan_detail(
            self.conn, int(sid),
            version=int(version) if version not in (None, "") else None)
        self._send(200, detail)

    def ep_std_preview(self, query, sid):
        self._std_head(sid)
        body = self._json_body() if self.command == "POST" else {}
        if body.get("samples") is not None:
            # ad-hoc: evaluate a candidate config without creating a version
            config = standardization.normalize_config(
                {"samples": body["samples"]})
            plan = self._std_head(sid)
            preview = standardization.preview_public(
                self.conn, plan["hypothesis"], config)
            self._send(200, {"standardization_id": int(sid),
                             "ad_hoc": True, "preview": preview})
            return
        version = body.get("version", query.get("version"))
        detail = standardization.plan_detail(
            self.conn, int(sid),
            version=int(version) if version not in (None, "") else None)
        self._send(200, detail)

    def ep_std_versions(self, query, sid):
        plan = self._std_head(sid)
        if self.command == "POST":
            body = self._json_body()
            samples = body.get("samples")
            if samples is None:
                raise ApiError(400, "MISSING_PARAM",
                               "samples (per-sample detrending choices) "
                               "is required")
            detail = standardization.add_version(
                self.conn, int(sid), samples=samples,
                note=body.get("note", ""))
            self._send(201, detail)
            return
        self._send(200, {
            "standardization_id": int(sid),
            "status": plan["status"],
            "latest_version": plan["latest_version"],
            "adopted_version": plan["adopted_version"],
            "versions": db.list_std_versions(self.conn, int(sid))})

    def ep_std_validate(self, query, sid):
        self._std_head(sid)
        body = self._json_body() if self.command == "POST" else {}
        version = body.get("version", query.get("version"))
        out = standardization.validate_plan(
            self.conn, int(sid),
            version=int(version) if version not in (None, "") else None)
        self._send(200, out)

    def ep_std_adopt(self, query, sid):
        self._std_head(sid)
        body = self._json_body() if self.command == "POST" else {}
        version = body.get("version", query.get("version"))
        out = standardization.adopt_plan(
            self.conn, int(sid),
            version=int(version) if version not in (None, "") else None)
        self._send(200, out)

    def ep_std_retire(self, query, sid):
        self._std_head(sid)
        out = standardization.retire_plan(self.conn, int(sid))
        self._send(200, out)

    def ep_std_compare(self, query, sid):
        self._std_head(sid)
        a, b = query.get("a"), query.get("b")
        if not a or not b:
            raise ApiError(400, "MISSING_PARAM",
                           "query parameters 'a' and 'b' (version "
                           "numbers) are required")
        self._send(200, standardization.compare_versions(
            self.conn, int(sid), int(a), int(b)))

    def ep_std_download(self, query, sid):
        self._std_head(sid)
        report = standardization.plan_report(self.conn, int(sid))
        download = query.get("download", "1").lower() in ("1", "true")
        self._send(200, report,
                   download_name=(f"standardization_{sid}.json"
                                  if download else None))


# ---------------------------------------------------------------------------
# Routing table
# ---------------------------------------------------------------------------

def route(method, path, query, h: Handler):
    for methods, pattern, fn in _ROUTES:
        if method in methods:
            params = _match(pattern, path)
            if params is not None:
                return fn(h, query, **params)
    raise ApiError(404, "NOT_FOUND", f"no route for {method} {path}")


def _match(pattern, path):
    pp = [p for p in pattern.split("/") if p]
    ap = [p for p in path.split("/") if p]
    if len(pp) != len(ap):
        return None
    out = {}
    for pat, val in zip(pp, ap):
        if pat.startswith("{") and pat.endswith("}"):
            out[pat[1:-1]] = urllib.parse.unquote(val)
        elif pat != val:
            return None
    return out


_ROUTES = [
    ({"GET"},    "/",                                  Handler.ep_root),
    ({"GET"},    "/api",                               Handler.ep_root),
    ({"GET"},    "/api/help",                          Handler.ep_help),
    ({"GET"},    "/api/openapi.json",                  Handler.ep_openapi),
    ({"POST"},   "/api/series",                        Handler.ep_series_post),
    ({"GET"},    "/api/series",                        Handler.ep_series_list),
    ({"GET"},    "/api/series/{sid}",                  Handler.ep_series_one),
    ({"DELETE"}, "/api/series/{sid}",                  Handler.ep_series_delete),
    ({"GET"},    "/api/runs",                          Handler.ep_runs),
    ({"GET", "POST"}, "/api/crossdate",                Handler.ep_crossdate),
    ({"GET"},    "/api/hypotheses",                    Handler.ep_hypothesis_list),
    ({"POST"},   "/api/hypotheses",                    Handler.ep_hypothesis_create),
    ({"GET"},    "/api/hypotheses/{hyp_name}",             Handler.ep_hypothesis_get),
    ({"DELETE"}, "/api/hypotheses/{hyp_name}",             Handler.ep_hypothesis_delete),
    ({"GET"},    "/api/hypotheses/{hyp_name}/chronology",  Handler.ep_chronology),
    ({"GET"},    "/api/hypotheses/{hyp_name}/report",      Handler.ep_report),
    ({"GET"},    "/api/hypotheses/{hyp_name}/vs-master",   Handler.ep_compare_master),
    ({"POST"},   "/api/hypotheses/{hyp_name}/locks",       Handler.ep_lock),
    ({"DELETE"}, "/api/hypotheses/{hyp_name}/locks",       Handler.ep_unlock),
    ({"GET"},    "/api/chronology",                    Handler.ep_chronology_default),
    ({"GET"},    "/api/master",                        Handler.ep_master),
    ({"GET"},    "/api/compare",                       Handler.ep_compare),
    ({"POST"},   "/api/corrections",                   Handler.ep_correction_create),
    ({"GET"},    "/api/corrections",                   Handler.ep_correction_list),
    ({"GET"},    "/api/corrections/{did}",             Handler.ep_correction_get),
    ({"GET"},    "/api/corrections/{did}/preview",     Handler.ep_correction_preview),
    ({"GET"},    "/api/corrections/{did}/versions",    Handler.ep_correction_versions),
    ({"POST"},   "/api/corrections/{did}/versions",    Handler.ep_correction_new_version),
    ({"POST"},   "/api/corrections/{did}/adopt",       Handler.ep_correction_adopt),
    ({"POST"},   "/api/corrections/{did}/revoke",      Handler.ep_correction_revoke),
    ({"GET"},    "/api/corrections/{did}/compare",     Handler.ep_correction_compare),
    ({"GET"},    "/api/corrections/{did}/download",    Handler.ep_correction_download),
    ({"POST"},   "/api/stability",                     Handler.ep_stability_create),
    ({"GET"},    "/api/stability",                     Handler.ep_stability_list),
    ({"GET"},    "/api/stability/compare",             Handler.ep_stability_compare),
    ({"GET"},    "/api/stability/{cid}",               Handler.ep_stability_get),
    ({"GET"},    "/api/stability/{cid}/download",      Handler.ep_stability_download),
    ({"POST"},   "/api/standardizations",              Handler.ep_std_create),
    ({"GET"},    "/api/standardizations",              Handler.ep_std_list),
    ({"GET"},    "/api/standardizations/{sid}",        Handler.ep_std_get),
    ({"GET", "POST"}, "/api/standardizations/{sid}/preview",
                                                         Handler.ep_std_preview),
    ({"GET", "POST"}, "/api/standardizations/{sid}/versions",
                                                         Handler.ep_std_versions),
    ({"POST"},   "/api/standardizations/{sid}/validate", Handler.ep_std_validate),
    ({"POST"},   "/api/standardizations/{sid}/adopt",    Handler.ep_std_adopt),
    ({"POST"},   "/api/standardizations/{sid}/retire",   Handler.ep_std_retire),
    ({"GET"},    "/api/standardizations/{sid}/compare",  Handler.ep_std_compare),
    ({"GET"},    "/api/standardizations/{sid}/download", Handler.ep_std_download),
]

_ENDPOINT_HELP = [
    {"method": "POST", "path": "/api/series",
     "description": "上传 JSON/CSV 树轮序列，逐个样本校验并保存（原样保留）"},
    {"method": "GET", "path": "/api/series",
     "description": "列出全部样本"},
    {"method": "GET", "path": "/api/series/{sample_id}",
     "description": "获取样本及其逐年宽度、缺失标记与原始行号"},
    {"method": "DELETE", "path": "/api/series/{sample_id}",
     "description": "删除样本（级联删除其轮宽行）"},
    {"method": "POST/GET", "path": "/api/crossdate",
     "description": "在偏移范围内滑动待定序列，返回多个候选（不自动定年）"},
    {"method": "GET", "path": "/api/runs",
     "description": "历史滑动计算结果"},
    {"method": "POST", "path": "/api/hypotheses",
     "description": "新建定年假设"},
    {"method": "GET", "path": "/api/hypotheses",
     "description": "列出全部假设"},
    {"method": "POST", "path": "/api/hypotheses/{hyp_name}/locks",
     "description": "锁定某样本偏移（offset=第 1 轮日历年）"},
    {"method": "DELETE", "path": "/api/hypotheses/{hyp_name}/locks?sample_id=",
     "description": "撤销锁定"},
    {"method": "GET", "path": "/api/hypotheses/{hyp_name}/chronology",
     "description": "逐年样本数/均值/中位数/离散度及 LOW_COVERAGE、OUTLIER、LOCK_CONFLICT 标记"},
    {"method": "GET", "path": "/api/hypotheses/{hyp_name}/report",
     "description": "完整 JSON 报告（加 ?download=1 触发下载）"},
    {"method": "GET", "path": "/api/hypotheses/{hyp_name}/vs-master",
     "description": "假设锁定位置与已定年主年表的逐年差异"},
    {"method": "GET", "path": "/api/compare?a=&b=",
     "description": "比较两个定年假设的样本位置"},
    {"method": "POST", "path": "/api/corrections",
     "description": "从滑动候选偏移创建校正草案（missing_ring 在测量序号之间插入缺失日历年，false_ring 把指定测量标为不参与定年的伪环）；事件重复/次序矛盾/越界/与已标缺失环或显式年份冲突时 422 并定位测量序号"},
    {"method": "GET", "path": "/api/corrections",
     "description": "列出校正草案（可按 sample_id 过滤）"},
    {"method": "GET", "path": "/api/corrections/{id}",
     "description": "草案最新版本及分段试算（?version=N 查看指定版本）"},
    {"method": "GET", "path": "/api/corrections/{id}/preview?version=N",
     "description": "按事件重建分段年份映射，重算各段及全序列重叠区间、Pearson、符号一致率、共同极窄环，并列出相对原候选的变化"},
    {"method": "GET", "path": "/api/corrections/{id}/versions",
     "description": "草案全部版本"},
    {"method": "POST", "path": "/api/corrections/{id}/versions",
     "description": "替换事件列表生成新版本（草案被采用前可改）"},
    {"method": "POST", "path": "/api/corrections/{id}/adopt",
     "description": "采用草案：分段映射写入指定假设并进入主年表（任一有效分段不足 min_overlap 时 422 拒绝并定位测量序号）；原始宽度、年份与 raw_payload 不变"},
    {"method": "POST", "path": "/api/corrections/{id}/revoke",
     "description": "撤销采用，恢复样本原 placement"},
    {"method": "GET", "path": "/api/corrections/{id}/compare?a=&b=",
     "description": "比较两个草案版本的事件与统计差异"},
    {"method": "GET", "path": "/api/corrections/{id}/download",
     "description": "草案完整 JSON 导出（含来源运行、参照快照与全部版本）"},
    {"method": "POST", "path": "/api/stability",
     "description": "对已锁定样本或整个假设发起局部稳定性检查：按采用后的年份映射切重叠窗口（缺失年零宽参与、伪环不参与，尾窗收窄到实际最大年份），各窗口在当前位置 ±search_radius 内与参照滑动比对；master 参照按 leave-one-out 排除被检查样本自身，参照即目标（E_SELF_REFERENCE）或无独立参照成员（E_NO_INDEPENDENT_REFERENCE）时 422；窗口共同年份不足 min_valid_years 时只给依据不下结论"},
    {"method": "GET", "path": "/api/stability",
     "description": "列出检查作业（可按 hypothesis、sample_id 过滤）"},
    {"method": "GET", "path": "/api/stability/{id}",
     "description": "检查详情：逐窗口 best_shift、并列偏移、Pearson/符号一致率/共同极窄环与疑似错位标记；支持 ?year_from=&year_to=&shift=&status=&sample_id= 筛选窗口；参照或样本映射在创建后发生变化时只给出 stale 说明，不改锁定或校正草案"},
    {"method": "GET", "path": "/api/stability/compare?a=&b=",
     "description": "比较两次检查：参数差异、共有窗口 best_shift 变化、疑似错位标记的新增与消失"},
    {"method": "GET", "path": "/api/stability/{id}/download",
     "description": "检查完整 JSON 导出（参数、样本映射、参照快照、全部窗口与标记；?download=0 取消附件头）"},
    {"method": "POST", "path": "/api/standardizations",
     "description": "创建序列标准化方案（draft 版本1）：以 {samples:[{sample_id,method,parameters}]} 逐样本选择水平均值 mean、负指数曲线 negative_exponential 或固定窗口居中移动平均 moving_average（window 为>=3 奇数）；方案从指定 hypothesis 的采用映射读取年份与校正版本；缺失环保留零宽、伪环不参与拟合"},
    {"method": "GET", "path": "/api/standardizations",
     "description": "列出标准化方案（可按 status、hypothesis 过滤）"},
    {"method": "GET", "path": "/api/standardizations/{id}",
     "description": "方案详情与最新版本（?version=N 指定版本）：逐样本期望生长曲线、轮宽指数与拟合诊断（已采用版本返回冻结曲线）"},
    {"method": "POST/GET", "path": "/api/standardizations/{id}/preview",
     "description": "预览：POST 可提交候选 samples 做临时试算（不建版本）；GET/不带体返回指定 ?version=N 的期望曲线与指数。有效点不足/负指数不收敛/期望值非正/移动窗口数据不足时返回样本与测量序号，不自动更换算法"},
    {"method": "GET/POST", "path": "/api/standardizations/{id}/versions",
     "description": "GET 列出全部版本；POST 以新 samples 配置生成新版本（draft/validated 可改，adopted/retired 不可改；编辑 validated 方案回到 draft）"},
    {"method": "POST", "path": "/api/standardizations/{id}/validate",
     "description": "校验全部样本：全部通过且为最新版本时方案变为 validated，否则 422 并逐样本给出测量序号（不自动改方法）"},
    {"method": "POST", "path": "/api/standardizations/{id}/adopt",
     "description": "采用方案（?version=N 或 body version，默认最新）：再次全样本校验通过后冻结来源映射（含校正版本）、参数与拟合曲线；只有 adopted 版本可被主年表/滑动匹配/稳定性检查指定"},
    {"method": "POST", "path": "/api/standardizations/{id}/retire",
     "description": "停用方案（retired 不可再改或再采用；已冻结的旧作业仍保留原计算依据）"},
    {"method": "GET", "path": "/api/standardizations/{id}/compare?a=&b=",
     "description": "比较两个方案版本：样本增删、方法/参数变化与拟合诊断"},
    {"method": "GET", "path": "/api/standardizations/{id}/download",
     "description": "方案完整 JSON 导出（全部版本、冻结来源映射、期望曲线与指数、诊断；?download=0 取消附件头）"},
    {"method": "GET/POST", "path": "/api/master /api/chronology /api/crossdate /api/stability",
     "description": "以上计算接口均支持 standardization_id（或 standardization）+ standardization_version 指定已采用版本；不传则保持原原始宽度/样本均值口径，旧作业依据不变"},
    {"method": "GET", "path": "/api/master",
     "description": "当前主年表（逐年平均指数）"},
    {"method": "GET", "path": "/api/openapi.json",
     "description": "OpenAPI 3 接口描述"},
]


# ---------------------------------------------------------------------------
# Server factory / CLI
# ---------------------------------------------------------------------------

def make_server(host: str, port: int, db_path: str,
                log=print) -> ThreadingHTTPServer:
    conn = db.connect(db_path)

    class _Handler(Handler):
        pass

    _Handler.conn = conn
    _Handler.db_lock = threading.RLock()
    server = ThreadingHTTPServer((host, port), _Handler)
    server.log = log
    server.db_conn = conn
    return server
