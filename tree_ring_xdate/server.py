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

from . import analysis, db, validate
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

    def ep_crossdate(self, query):
        body = self._json_body() if self.command == "POST" else {}
        sid = body.get("sample_id") or query.get("sample_id")
        if not sid:
            raise ApiError(400, "MISSING_PARAM",
                           "sample_id is required")
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
            "hypothesis": body.get("hypothesis",
                                   query.get("hypothesis")),
        }
        for k in ("offset_min", "offset_max"):
            v = body.get(k, query.get(k))
            params[k] = int(v) if v not in (None, "") else None
        if params["min_overlap"] < 3:
            raise ApiError(400, "BAD_REQUEST",
                           "min_overlap must be >= 3")
        result = analysis.crossdate(self.conn, sid, **params)
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
        chrono = analysis.build_chronology(
            self.conn, hypothesis,
            min_samples=int(query.get("min_samples", 3)),
            outlier_sd=float(query.get("outlier_sd", 2.0)),
            weak_correlation=float(query.get("weak_correlation", 0.3)))
        self._send(200, chrono)

    def ep_master(self, query):
        hypothesis = query.get("hypothesis", DEFAULT_HYPOTHESIS)
        if not db.get_hypothesis(self.conn, hypothesis, with_locks=False):
            if "hypothesis" in query:
                raise ApiError(404, "NOT_FOUND",
                               f"hypothesis not found: {hypothesis}")
            db.create_hypothesis(self.conn, hypothesis,
                                 "auto-created working hypothesis")
        yw, meta = analysis.build_reference(self.conn, "master", hypothesis)
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
            weak_correlation=float(query.get("weak_correlation", 0.3)))
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
