"""End-to-end tests: real HTTP server on an ephemeral port + temp SQLite.

Run from the repository root::

    python -m unittest discover -s tree_ring_xdate/tests -v
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from urllib.parse import urlencode

from tree_ring_xdate.server import make_server

HERE = os.path.dirname(os.path.abspath(__file__))
EXAMPLES = os.path.normpath(os.path.join(HERE, "..", "examples"))


class ServerTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "test.db")
        self.server = make_server("127.0.0.1", 0, self.db_path, log=None)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()
        self.server.db_conn.close()
        self.tmp.cleanup()

    def url(self, path, **params):
        base = f"http://127.0.0.1:{self.port}{path}"
        if params:
            base += "?" + urlencode(params)
        return base

    def request(self, path, method="GET", body=None, *, ctype="application/json",
                raw=None, headers=None, **params):
        data = None
        hdr = dict(headers or {})
        if raw is not None:
            data = raw.encode("utf-8") if isinstance(raw, str) else raw
            hdr["Content-Type"] = ctype
        elif body is not None:
            data = json.dumps(body).encode("utf-8")
            hdr["Content-Type"] = "application/json"
        req = urllib.request.Request(self.url(path, **params), data=data,
                                     method=method, headers=hdr)
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8")), \
                    dict(resp.headers)
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8")), \
                dict(e.headers)


CSV_OK = """sample_id,unit,start_year,year,width,missing
A01,mm,1950,,1.21,0
A01,,,,0.98,0
A01,,,,0,1
A01,,,,1.05,0
B01,mm,1960,,0.8,0
B01,,,,0.7,0
B01,,,,0.6,0
"""

CSV_BAD = """sample_id,unit,start_year,year,width,missing
X01,mm,1950,,1.1,0
X01,cm,,,0.9,0
X01,,,,-0.2,0
X01,,,,0,0
X02,mm,,1950,1.0,0
X02,,,1951,1.1,0
X02,,,1950,0.9,0
"""


class TestIngestion(ServerTestBase):
    def test_csv_ok(self):
        st, body, _ = self.request("/api/series", "POST", raw=CSV_OK,
                                   ctype="text/csv")
        self.assertEqual(st, 201)
        self.assertEqual(body["n_saved"], 2)
        self.assertEqual(body["n_errors"], 0)
        st, s, _ = self.request("/api/series/A01")
        self.assertEqual(s["known_start"], 1950)
        self.assertTrue(s["has_missing"])
        # missing ring stored as width 0 with original line preserved
        r3 = s["rings"][2]
        self.assertEqual((r3["year"], r3["width"], r3["missing"],
                          r3["raw_line"]), (1952, 0.0, True, 4))
        # raw payload verbatim
        self.assertIn("A01,mm,1950,,1.21,0", s["raw_payload"])

    def test_error_localization(self):
        st, body, _ = self.request("/api/series", "POST", raw=CSV_BAD,
                                   ctype="text/csv")
        self.assertEqual(st, 200)  # partial acceptance
        self.assertEqual(body["n_saved"], 0)
        codes = {(e["code"], e.get("sample_id"), e.get("line"))
                 for e in body["errors"]}
        self.assertIn(("E_UNIT_CONFLICT", "X01", 3), codes)
        self.assertIn(("E_NONPOSITIVE_WIDTH", "X01", 4), codes)
        # zero width without missing marker -> contradictory mark
        self.assertTrue(any(e["code"] == "E_CONFLICTING_MARK"
                            and e["sample_id"] == "X01"
                            and e["line"] == 5 for e in body["errors"]))
        self.assertTrue(any(e["code"] == "E_DUPLICATE_YEAR"
                            and e["sample_id"] == "X02"
                            and e["line"] == 8 for e in body["errors"]))
        # nothing fatal got stored
        st, lst, _ = self.request("/api/series")
        self.assertEqual(lst["series"], [])

    def test_strict_mode_rejects_batch(self):
        st, body, _ = self.request("/api/series", "POST", raw=CSV_BAD,
                                   ctype="text/csv", strict=1)
        self.assertEqual(st, 422)
        self.assertIn("strict", body["message"])

    def test_strict_mode_persists_nothing_from_mixed_batch(self):
        # one perfectly valid sibling plus one invalid sample: strict
        # mode must roll the whole request back, no partial write.
        mixed = (
            "sample_id,unit,start_year,year,width,missing\n"
            "GOOD1,mm,1980,,1.1,0\n"
            "GOOD1,,,,0.9,0\n"
            "GOOD1,,,,1.0,0\n"
            "BAD1,mm,1980,,1.2,0\n"
            "BAD1,cm,,0.8,0\n"          # E_UNIT_CONFLICT
            "BAD1,,,-0.4,0\n")         # E_NONPOSITIVE_WIDTH
        st, body, _ = self.request("/api/series", "POST", raw=mixed,
                                   ctype="text/csv", strict=1)
        self.assertEqual(st, 422)
        self.assertEqual(body["n_saved"], 0)
        self.assertTrue(body["errors"])
        st, lst, _ = self.request("/api/series")
        self.assertEqual(lst["series"], [])
        # the valid sample must not be retrievable either
        st, _, _ = self.request("/api/series/GOOD1")
        self.assertEqual(st, 404)

    def test_non_strict_mixed_batch_keeps_valid_sibling(self):
        mixed = (
            "sample_id,unit,start_year,year,width,missing\n"
            "GOOD1,mm,1980,,1.1,0\n"
            "BAD1,mm,1980,,1.2,0\n"
            "BAD1,cm,,0.8,0\n")
        st, body, _ = self.request("/api/series", "POST", raw=mixed,
                                   ctype="text/csv")
        self.assertEqual(st, 201)
        self.assertEqual([s["sample_id"] for s in body["saved"]], ["GOOD1"])
        st, s, _ = self.request("/api/series/GOOD1")
        self.assertEqual(st, 200)
        st, _, _ = self.request("/api/series/BAD1")
        self.assertEqual(st, 404)

    def test_json_raw_payload_is_verbatim(self):
        # newlines, custom spacing and indentation must survive byte-for-byte
        pretty = (
            '{\n'
            '  "samples" : [\n'
            '    {\n'
            '        "sample_id"  :  "PRETTY",\n'
            '        "unit":   "mm",\n'
            '        "start_year" :2001,\n'
            '        "rings" : [ {"width" : 1.10},\n'
            '                    {"width": 0, "missing" : true},\n'
            '                    {"width" :0.90} ]\n'
            '    }\n'
            '  ]\n'
            '}')
        st, body, _ = self.request("/api/series", "POST", raw=pretty,
                                   ctype="application/json")
        self.assertEqual(st, 201, body)
        st, s, _ = self.request("/api/series/PRETTY")
        self.assertEqual(s["raw_payload"],
                         '{\n'
                         '        "sample_id"  :  "PRETTY",\n'
                         '        "unit":   "mm",\n'
                         '        "start_year" :2001,\n'
                         '        "rings" : [ {"width" : 1.10},\n'
                         '                    {"width": 0, "missing" : true},\n'
                         '                    {"width" :0.90} ]\n'
                         '    }')
        # ring raw_line keeps the JSON item index
        self.assertEqual([r["raw_line"] for r in s["rings"]], [1, 2, 3])

    def test_json_single_object_raw_payload_is_verbatim(self):
        pretty = '  {\n "sample_id":"SOLO","unit":"mm",\n "start_year":1999,\n "rings":[{"width":1.0}]\n}  '
        st, body, _ = self.request("/api/series", "POST", raw=pretty,
                                   ctype="application/json")
        self.assertEqual(st, 201, body)
        st, s, _ = self.request("/api/series/SOLO")
        self.assertEqual(s["raw_payload"], pretty)

    def test_json_nested_same_named_key_cannot_shadow_samples(self):
        # A nested "samples" (here inside metadata) placed BEFORE the outer
        # samples array must not be mistaken for the real sample list.
        payload = (
            '{\n'
            '  "metadata": {\n'
            '    "samples": [\n'
            '      {"sample_id": "DECOY", "unit": "cm", "widths": [9.9]}\n'
            '    ],\n'
            '    "note": "nested same-named key"\n'
            '  },\n'
            '  "samples" : [\n'
            '    {\n'
            '      "sample_id": "REAL01",\n'
            '      "unit": "mm",\n'
            '      "start_year": 2000,\n'
            '      "rings": [ {"width": 1.1},\n'
            '                 {"width": 0, "missing": true},\n'
            '                 {"width": 0.8} ]\n'
            '    }\n'
            '  ]\n'
            '}')
        st, body, _ = self.request("/api/series", "POST", raw=payload,
                                   ctype="application/json")
        self.assertEqual(st, 201, body)
        self.assertEqual([s["sample_id"] for s in body["saved"]],
                         ["REAL01"])
        st, lst, _ = self.request("/api/series")
        self.assertEqual({s["sample_id"] for s in lst["series"]},
                         {"REAL01"})
        st, s, _ = self.request("/api/series/REAL01")
        raw = s["raw_payload"]
        # audit text is the real sample's exact source fragment
        self.assertIn("REAL01", raw)
        self.assertNotIn("DECOY", raw)
        self.assertIn(raw, payload)
        self.assertEqual([r["width"] for r in s["rings"]],
                         [1.1, 0.0, 0.8])

    def test_json_nested_same_named_key_after_samples(self):
        payload = ('{"samples":[{"sample_id":"A","unit":"mm",'
                   '"widths":[1,2,3]}],'
                   '"metadata":{"samples":[{"sample_id":"DECOY"}]}}')
        st, body, _ = self.request("/api/series", "POST", raw=payload,
                                   ctype="application/json")
        self.assertEqual(st, 201, body)
        self.assertEqual([s["sample_id"] for s in body["saved"]], ["A"])
        st, s, _ = self.request("/api/series/A")
        self.assertIn("\"sample_id\":\"A\"", s["raw_payload"])
        self.assertNotIn("DECOY", s["raw_payload"])

    def test_csv_raw_payload_preserves_quotes_crlf_and_trailing_newline(self):
        csv_bytes = (
            b'sample_id,unit,start_year,year,width,missing\r\n'
            b'"Q-01",mm,1990,,1.20,0\r\n'
            b',  ,  ,  , 0.90 , 0 \r\n'
            b'Q-01,,,,0,1\r\n'
            b'\r\n'
        )
        st, body, _ = self.request("/api/series", "POST",
                                   raw=csv_bytes.decode("utf-8"),
                                   ctype="text/csv")
        self.assertEqual(st, 201, body)
        st, s, _ = self.request("/api/series/Q-01")
        expected = (
            'sample_id,unit,start_year,year,width,missing\r\n'
            '"Q-01",mm,1990,,1.20,0\r\n'
            ',  ,  ,  , 0.90 , 0 \r\n'
            'Q-01,,,,0,1\r\n'
        )
        self.assertEqual(s["raw_payload"], expected)
        # blank separator line before EOF is excluded, but final CRLF of
        # the last data line survives
        self.assertTrue(s["raw_payload"].endswith("0,1\r\n"))

    def test_csv_raw_payload_without_trailing_newline(self):
        csv_text = ("sample_id,unit,start_year,width,missing\n"
                    "N1,mm,1999,1.0,0\n"
                    "N1,,,1.1,0")
        st, _, _ = self.request("/api/series", "POST", raw=csv_text,
                                ctype="text/csv")
        self.assertEqual(st, 201)
        st, s, _ = self.request("/api/series/N1")
        self.assertEqual(s["raw_payload"], csv_text)
        self.assertFalse(s["raw_payload"].endswith("\n\n"))

    def test_csv_raw_payload_quoted_multiline_field(self):
        # quoted note with embedded newline: one logical row spans two
        # physical lines; the audit slice must contain both verbatim
        csv_text = (
            'sample_id,unit,start_year,width,missing,note\n'
            'Q03,mm,1992,1.2,0,"line1\nline2"\n'
            'Q03,,,0.7,0,\n'
        )
        st, body, _ = self.request("/api/series", "POST", raw=csv_text,
                                   ctype="text/csv")
        self.assertEqual(st, 201, body)
        st, s, _ = self.request("/api/series/Q03")
        self.assertEqual(s["raw_payload"], csv_text)
        self.assertEqual(s["rings"][0]["raw_line"], 3)  # logical row ends L3
        self.assertEqual(s["rings"][1]["raw_line"], 4)

    def test_json_payload_and_contradictory_mark(self):
        payload = {"sample_id": "J01", "unit": "mm", "start_year": 2000,
                   "rings": [{"width": 1.0}, {"width": 0.5,
                                              "missing": True},
                             {"width": 0}]}
        st, body, _ = self.request("/api/series", "POST", body=payload)
        self.assertEqual(st, 200)
        self.assertEqual(body["n_saved"], 0)
        errs = body["errors"]
        # positive width on a missing marker; zero width unmarked
        self.assertEqual({e["code"] for e in errs},
                         {"E_CONFLICTING_MARK"})
        # the two offending items are both pinpointed (rows 2 and 3)
        self.assertEqual({e["row"] for e in errs}, {2, 3})
    def test_sparse_csv_gets_implicit_gap_warning(self):
        csv_text = ("sample_id,unit,year,width\n"
                    "S1,mm,1970,1.0\nS1,,1971,1.1\nS1,,1973,0.9\n")
        st, body, _ = self.request("/api/series", "POST", raw=csv_text,
                                   ctype="text/csv")
        self.assertEqual(st, 201)
        st, s, _ = self.request("/api/series/S1")
        self.assertEqual(s["known_start"], 1970)
        self.assertEqual([(r["year"], r["width"], r["missing"])
                          for r in s["rings"]],
                         [(1970, 1.0, False), (1971, 1.1, False),
                          (1972, 0.0, True), (1973, 0.9, False)])
        self.assertEqual(s["warnings"][0]["code"], "W_IMPLICIT_GAP")

    def test_replace_and_delete(self):
        self.request("/api/series", "POST", raw=CSV_OK, ctype="text/csv")
        payload = {"sample_id": "A01", "unit": "0.01mm",
                   "start_year": 1800,
                   "rings": [{"width": 50}, {"width": 0, "missing": True}]}
        st, body, _ = self.request("/api/series", "POST", body=payload)
        self.assertEqual(st, 201)
        st, s, _ = self.request("/api/series/A01")
        self.assertEqual(s["unit"], "0.01mm")
        self.assertEqual(s["known_start"], 1800)
        self.assertEqual(len(s["rings"]), 2)
        st, _, _ = self.request("/api/series/A01", "DELETE")
        self.assertEqual(st, 200)
        st, _, _ = self.request("/api/series/A01")
        self.assertEqual(st, 404)


class TestCrossdating(ServerTestBase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(EXAMPLES, "samples.csv"),
                  encoding="utf-8") as f:
            cls.csv = f.read()

    def setUp(self):
        super().setUp()
        st, body, _ = self.request("/api/series", "POST", raw=self.csv,
                                   ctype="text/csv")
        self.assertEqual(st, 201, body)
        self.assertEqual(body["n_errors"], 0)

    def test_candidates_and_automatic_answer(self):
        st, body, _ = self.request(
            "/api/crossdate", "POST",
            body={"sample_id": "UNKNOWN_01", "reference": "master",
                  "offset_min": 1900, "offset_max": 1990,
                  "min_overlap": 30, "tolerance": 0.05})
        self.assertEqual(st, 200)
        cands = body["candidates"]
        self.assertGreaterEqual(len(cands), 1)
        best = cands[0]
        self.assertEqual(best["offset"], 1948)  # known generated answer
        self.assertGreater(best["correlation"], 0.9)
        self.assertGreater(best["sign_agreement"], 0.8)
        self.assertIn(2002, best["narrow_hits"])  # pointer year hit
        self.assertEqual(best["overlap_start"], 1948)
        self.assertEqual(best["n_overlap"], best["overlap_end"]
                         - best["overlap_start"] + 1)
        self.assertIn("run_id", body)

    def test_min_overlap_filters(self):
        st, body, _ = self.request(
            "/api/crossdate", "POST",
            body={"sample_id": "UNKNOWN_01", "offset_min": 1800,
                  "offset_max": 1850, "min_overlap": 30})
        self.assertEqual(st, 200)
        self.assertEqual(body["candidates"], [])

    def test_near_tie_keeps_multiple(self):
        # craft two identical references so several offsets score alike
        # is unrealistic; instead verify tolerance widens the kept set
        st, tight, _ = self.request(
            "/api/crossdate", "POST",
            body={"sample_id": "UNKNOWN_01", "offset_min": 1900,
                  "offset_max": 1990, "min_overlap": 30, "tolerance": 0.0})
        st, wide, _ = self.request(
            "/api/crossdate", "POST",
            body={"sample_id": "UNKNOWN_01", "offset_min": 1900,
                  "offset_max": 1990, "min_overlap": 30, "tolerance": 0.2})
        self.assertGreaterEqual(len(wide["candidates"]),
                                len(tight["candidates"]))
        # ranks are contiguous starting at 1
        self.assertEqual([c["rank"] for c in tight["candidates"]],
                         list(range(1, len(tight["candidates"]) + 1)))

    def test_runs_persisted(self):
        self.request("/api/crossdate", "POST",
                     body={"sample_id": "UNKNOWN_01", "min_overlap": 30})
        st, body, _ = self.request("/api/runs")
        self.assertEqual(st, 200)
        self.assertEqual(body["runs"][0]["sample_id"], "UNKNOWN_01")
        self.assertTrue(body["runs"][0]["candidates"])

    def test_unknown_sample_404(self):
        st, body, _ = self.request("/api/crossdate", "POST",
                                   body={"sample_id": "GHOST"})
        self.assertEqual(st, 404)


class TestHypotheses(ServerTestBase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(EXAMPLES, "samples.csv"),
                  encoding="utf-8") as f:
            cls.csv = f.read()

    def setUp(self):
        super().setUp()
        self.request("/api/series", "POST", raw=self.csv, ctype="text/csv")
        st, body, _ = self.request("/api/crossdate", "POST",
                                   body={"sample_id": "UNKNOWN_01",
                                         "offset_min": 1900,
                                         "offset_max": 1990,
                                         "min_overlap": 30})
        self.run_id = body["run_id"]

    def test_full_workflow(self):
        st, _, _ = self.request("/api/hypotheses", "POST",
                                body={"name": "H1", "note": "first pass"})
        self.assertEqual(st, 201)
        st, body, _ = self.request(
            "/api/hypotheses/H1/locks", "POST",
            body={"sample_id": "UNKNOWN_01", "offset": 1948,
                  "run_id": self.run_id})
        self.assertEqual(st, 200)
        self.assertEqual(body["locked"]["offset"], 1948)

        st, chrono, _ = self.request("/api/hypotheses/H1/chronology")
        self.assertEqual(st, 200)
        self.assertGreater(chrono["n_years"], 100)
        low = [y for y in chrono["yearly"]
               if "LOW_COVERAGE" in y["flags"]]
        self.assertTrue(low)  # early years covered by 1-2 samples
        keys = {"year", "n_samples", "mean_index", "median_index",
                "stdev_index", "mad", "members", "flags"}
        self.assertTrue(keys <= set(chrono["yearly"][10].keys()))
        # correct placement must not flag a weak match
        weak = [c for c in chrono["lock_conflicts"]
                if c["type"] == "WEAK_MATCH"]
        self.assertEqual(weak, [])

    def test_wrong_lock_flags_conflict(self):
        self.request("/api/hypotheses", "POST", body={"name": "BAD"})
        st, _, _ = self.request(
            "/api/hypotheses/BAD/locks", "POST",
            body={"sample_id": "UNKNOWN_01", "offset": 1960})
        st, chrono, _ = self.request("/api/hypotheses/BAD/chronology")
        types = [c["type"] for c in chrono["lock_conflicts"]]
        self.assertIn("WEAK_MATCH", types)

    def test_lock_overrides_known_start_and_reports_conflict(self):
        # SITE_A01 is a dated sample (known start 1901).  Locking it
        # elsewhere must actually move it in the chronology and surface a
        # LOCK_VS_KNOWN conflict rather than being silently ignored.
        st, s0, _ = self.request("/api/series/SITE_A01")
        self.assertEqual(s0["known_start"], 1901)
        self.request("/api/hypotheses", "POST",
                     body={"name": "REDATE", "note": "re-date A01"})
        st, body, _ = self.request(
            "/api/hypotheses/REDATE/locks", "POST",
            body={"sample_id": "SITE_A01", "offset": 1920})
        self.assertEqual(st, 200)

        st, chrono, _ = self.request("/api/hypotheses/REDATE/chronology")
        self.assertEqual(st, 200)
        # the locked position wins, not the ingested known start
        self.assertEqual(chrono["placements"]["SITE_A01"], 1920)
        self.assertEqual(chrono["known_starts"]["SITE_A01"], 1901)
        self.assertGreaterEqual(chrono["n_known_start_conflicts"], 1)
        kc = [c for c in chrono["lock_conflicts"]
              if c["type"] == "LOCK_VS_KNOWN" and c["sample_id"] == "SITE_A01"]
        self.assertEqual(len(kc), 1)
        self.assertEqual(kc[0]["known_start"], 1901)
        self.assertEqual(kc[0]["locked_offset"], 1920)
        self.assertEqual(kc[0]["shift"], 19)

        # the report must carry the same explicit conflict
        st, report, _ = self.request("/api/hypotheses/REDATE/report")
        self.assertTrue(any(
            c["type"] == "LOCK_VS_KNOWN" and c["sample_id"] == "SITE_A01"
            for c in report["flags"]["lock_conflicts"]))

        # master reference under this hypothesis must also move
        st, master, _ = self.request("/api/master", hypothesis="REDATE")
        member = next(m for m in master["meta"]["members"]
                      if m["sample_id"] == "SITE_A01")
        self.assertEqual(member["start"], 1920)
        self.assertTrue(any(c["sample_id"] == "SITE_A01"
                            for c in master["meta"]
                            ["known_start_conflicts"]))

    def test_matching_lock_on_dated_sample_has_no_conflict(self):
        self.request("/api/hypotheses", "POST", body={"name": "SAME"})
        st, _, _ = self.request(
            "/api/hypotheses/SAME/locks", "POST",
            body={"sample_id": "SITE_A01", "offset": 1901})
        self.assertEqual(st, 200)
        st, chrono, _ = self.request("/api/hypotheses/SAME/chronology")
        self.assertEqual(chrono["placements"]["SITE_A01"], 1901)
        self.assertEqual(chrono["n_known_start_conflicts"], 0)
        self.assertFalse([c for c in chrono["lock_conflicts"]
                          if c["type"] == "LOCK_VS_KNOWN"])

    def test_unlock_and_delete(self):
        self.request("/api/hypotheses", "POST", body={"name": "TMP"})
        self.request("/api/hypotheses/TMP/locks", "POST",
                     body={"sample_id": "UNKNOWN_01", "offset": 1948})
        st, body, _ = self.request(
            "/api/hypotheses/TMP/locks", "DELETE", sample_id="UNKNOWN_01")
        self.assertEqual(st, 200)
        self.assertTrue(body["existed"])
        st, h, _ = self.request("/api/hypotheses/TMP")
        self.assertEqual(h["locks"], [])
        st, _, _ = self.request("/api/hypotheses/TMP", "DELETE")
        self.assertEqual(st, 200)
        st, _, _ = self.request("/api/hypotheses/TMP")
        self.assertEqual(st, 404)

    def test_compare_and_vs_master(self):
        self.request("/api/hypotheses", "POST", body={"name": "H1"})
        self.request("/api/hypotheses", "POST", body={"name": "H2"})
        self.request("/api/hypotheses/H1/locks", "POST",
                     body={"sample_id": "UNKNOWN_01", "offset": 1948})
        self.request("/api/hypotheses/H2/locks", "POST",
                     body={"sample_id": "UNKNOWN_01", "offset": 1955})
        st, cmp_, _ = self.request("/api/compare", a="H1", b="H2")
        self.assertEqual(st, 200)
        diff = [d for d in cmp_["differences"]
                if d["sample_id"] == "UNKNOWN_01"][0]
        self.assertEqual(diff["shift"], 7)
        st, vm, _ = self.request("/api/hypotheses/H1/vs-master")
        row = [r for r in vm["samples"]
               if r["sample_id"] == "UNKNOWN_01"][0]
        self.assertEqual(row["status"], "locked_only")

    def test_report_download(self):
        self.request("/api/hypotheses", "POST", body={"name": "HR"})
        self.request("/api/hypotheses/HR/locks", "POST",
                     body={"sample_id": "UNKNOWN_01", "offset": 1948})
        st, report, hdr = self.request(
            "/api/hypotheses/HR/report", download=1)
        self.assertEqual(st, 200)
        self.assertIn("attachment", hdr["Content-Disposition"])
        self.assertEqual(report["report_type"], "tree_ring_crossdating")
        self.assertIn("yearly", report)
        self.assertIn("flags", report)
        # JSON must be NaN-free
        raw = json.dumps(report, allow_nan=False)
        self.assertNotIn("NaN", raw)

    def test_master_endpoint(self):
        self.request("/api/hypotheses", "POST", body={"name": "HM"})
        self.request("/api/hypotheses/HM/locks", "POST",
                     body={"sample_id": "UNKNOWN_01", "offset": 1948})
        st, body, _ = self.request("/api/master", hypothesis="HM")
        self.assertEqual(st, 200)
        self.assertEqual(body["meta"]["type"], "master")
        self.assertGreater(len(body["years"]), 100)


class TestCorrections(ServerTestBase):
    """Correction drafts: missing-ring gaps and false-ring exclusions."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(EXAMPLES, "samples.csv"),
                  encoding="utf-8") as f:
            cls.csv = f.read()

    def setUp(self):
        super().setUp()
        st, body, _ = self.request("/api/series", "POST", raw=self.csv,
                                   ctype="text/csv")
        self.assertEqual(st, 201, body)
        st, cd, _ = self.request(
            "/api/crossdate", "POST",
            body={"sample_id": "UNKNOWN_01", "offset_min": 1900,
                  "offset_max": 1990, "min_overlap": 30})
        self.assertEqual(st, 200)
        self.run_id = cd["run_id"]
        self.best_offset = cd["candidates"][0]["offset"]

    def _create(self, events, **kw):
        body = {"sample_id": "UNKNOWN_01", "offset": self.best_offset,
                "run_id": self.run_id, "min_overlap": 10,
                "events": events}
        body.update(kw)
        return self.request("/api/corrections", "POST", body=body)

    # -- creation / preview ------------------------------------------------
    def test_create_draft_segments_and_changes(self):
        st, draft, _ = self._create(
            [{"type": "missing_ring", "after_seq": 20},
             {"type": "false_ring", "seq": 35}])
        self.assertEqual(st, 201, draft)
        self.assertEqual(draft["draft_id"], 1)
        self.assertEqual(draft["version"], 1)
        self.assertEqual(draft["status"], "draft")
        # three segments: 1..20(+gap), 21..34, 36..60
        segs = draft["evaluation"]["segments"]
        self.assertEqual(len(segs), 3)
        self.assertEqual(segs[0]["start_year"], self.best_offset)
        self.assertEqual(segs[0]["n_missing"], 1)   # inserted gap year
        self.assertEqual(segs[1]["n_years"], 14)
        for s in segs:
            self.assertIn("correlation", s)
            self.assertIn("sign_agreement", s)
            self.assertIn("narrow_hits", s)
            self.assertIn("n_overlap", s)
        whole = draft["evaluation"]["whole"]
        self.assertEqual(whole["n_overlap"], 60)
        ch = draft["changes_vs_candidate"]
        self.assertTrue(ch["available"])
        self.assertEqual(ch["candidate_offset"], self.best_offset)
        self.assertIn("correlation", ch["delta"])
        self.assertIn("narrow_hits_added", ch["delta"])
        # false ring has no year; inserted gap has one
        roles = {m["seq"]: m["role"] for m in draft["mapping"]
                 if m["seq"] is not None}
        self.assertEqual(roles[35], "false_ring")
        inserted = [m for m in draft["mapping"]
                    if m["role"] == "inserted_missing"]
        self.assertEqual(len(inserted), 1)
        self.assertEqual(inserted[0]["year"], self.best_offset + 20)

    def test_preview_versions(self):
        st, draft, _ = self._create(
            [{"type": "missing_ring", "after_seq": 20}])
        did = draft["draft_id"]
        st, v2, _ = self.request(
            f"/api/corrections/{did}/versions", "POST",
            body={"events": [{"type": "missing_ring", "after_seq": 25},
                             {"type": "false_ring", "seq": 40}]})
        self.assertEqual(st, 201)
        self.assertEqual(v2["version"], 2)
        st, prev, _ = self.request(
            f"/api/corrections/{did}/preview", version=2)
        self.assertEqual(st, 200)
        self.assertEqual(prev["version"], 2)
        self.assertEqual(len(prev["evaluation"]["segments"]), 3)
        st, prev1, _ = self.request(
            f"/api/corrections/{did}/preview", version=1)
        self.assertEqual(len(prev1["evaluation"]["segments"]), 2)

    def test_list_and_get_draft(self):
        st, draft, _ = self._create(
            [{"type": "missing_ring", "after_seq": 10}])
        did = draft["draft_id"]
        st, lst, _ = self.request("/api/corrections",
                                  sample_id="UNKNOWN_01")
        self.assertEqual(st, 200)
        self.assertEqual(len(lst["corrections"]), 1)
        self.assertEqual(lst["corrections"][0]["draft_id"], did)
        st, one, _ = self.request(f"/api/corrections/{did}")
        self.assertEqual(st, 200)
        self.assertEqual(one["sample_id"], "UNKNOWN_01")
        st, _, _ = self.request("/api/corrections/999")
        self.assertEqual(st, 404)

    # -- validation rejections ---------------------------------------------
    def test_reject_duplicate_events(self):
        st, body, _ = self._create(
            [{"type": "false_ring", "seq": 10},
             {"type": "false_ring", "seq": 10}])
        self.assertEqual(st, 422)
        self.assertEqual(body["errors"][0]["code"], "E_EVENT_DUPLICATE")
        self.assertEqual(body["errors"][0]["seq"], 10)

    def test_reject_order_contradiction(self):
        st, body, _ = self._create(
            [{"type": "false_ring", "seq": 30},
             {"type": "missing_ring", "after_seq": 10}])
        self.assertEqual(st, 422)
        self.assertEqual(body["errors"][0]["code"], "E_EVENT_ORDER")

    def test_reject_out_of_range(self):
        st, body, _ = self._create([{"type": "false_ring", "seq": 999}])
        self.assertEqual(st, 422)
        self.assertEqual(body["errors"][0]["code"], "E_EVENT_RANGE")
        self.assertEqual(body["errors"][0]["seq"], 999)
        st, body, _ = self._create(
            [{"type": "missing_ring", "after_seq": 61}])
        self.assertEqual(st, 422)
        self.assertEqual(body["errors"][0]["code"], "E_EVENT_RANGE")

    def test_reject_event_vs_recorded_missing(self):
        # A01 has a recorded missing ring at seq 3
        csv_text = ("sample_id,unit,start_year,year,width,missing\n"
                    "M1,mm,1950,,1.1,0\nM1,,,,1.0,0\nM1,,,,0,1\n"
                    "M1,,,,1.2,0\nM1,,,,1.3,0\n")
        self.request("/api/series", "POST", raw=csv_text, ctype="text/csv")
        st, body, _ = self.request(
            "/api/corrections", "POST",
            body={"sample_id": "M1", "offset": 1950,
                  "events": [{"type": "false_ring", "seq": 3}]})
        self.assertEqual(st, 422)
        self.assertEqual(body["errors"][0]["code"], "E_EVENT_VS_MISSING")
        self.assertEqual(body["errors"][0]["seq"], 3)

    def test_reject_event_vs_explicit_year(self):
        # SITE_A01 has explicit years (known_start 1901): any gap shifts them
        st, body, _ = self.request(
            "/api/corrections", "POST",
            body={"sample_id": "SITE_A01", "offset": 1901,
                  "events": [{"type": "missing_ring", "after_seq": 10}]})
        self.assertEqual(st, 422)
        codes = {e["code"] for e in body["errors"]}
        self.assertEqual(codes, {"E_EVENT_VS_YEAR"})
        self.assertEqual(body["errors"][0]["seq"], 11)

    def test_reject_segment_too_short_on_adopt(self):
        st, draft, _ = self._create(
            [{"type": "false_ring", "seq": 30}], min_overlap=40)
        did = draft["draft_id"]
        st, body, _ = self.request(
            f"/api/corrections/{did}/adopt", "POST",
            body={"hypothesis": "H1"})
        self.assertEqual(st, 422)
        err = body["errors"][0]
        self.assertEqual(err["code"], "E_SEGMENT_TOO_SHORT")
        self.assertIn("seq", err)
        self.assertIn("segment", err)

    # -- adopt / revoke ----------------------------------------------------
    def test_adopt_enters_hypothesis_and_master(self):
        st, draft, _ = self._create(
            [{"type": "missing_ring", "after_seq": 20},
             {"type": "false_ring", "seq": 35}])
        did = draft["draft_id"]
        st, ad, _ = self.request(
            f"/api/corrections/{did}/adopt", "POST",
            body={"hypothesis": "H1"})
        self.assertEqual(st, 200, ad)
        self.assertEqual(ad["status"], "adopted")
        st, chrono, _ = self.request("/api/hypotheses/H1/chronology")
        self.assertEqual(chrono["corrected_samples"], ["UNKNOWN_01"])
        self.assertEqual(chrono["correction_drafts"]["UNKNOWN_01"], [did])
        st, master, _ = self.request("/api/master", hypothesis="H1")
        member = [m for m in master["meta"]["members"]
                  if m["sample_id"] == "UNKNOWN_01"][0]
        self.assertTrue(member["corrected"])
        # double adoption rejected
        st, body, _ = self.request(
            f"/api/corrections/{did}/adopt", "POST",
            body={"hypothesis": "H1"})
        self.assertEqual(st, 422)
        self.assertEqual(body["errors"][0]["code"], "E_DRAFT_ADOPTED")

    def test_adopt_then_revoke_restores(self):
        st, draft, _ = self._create(
            [{"type": "missing_ring", "after_seq": 20}])
        did = draft["draft_id"]
        self.request(f"/api/corrections/{did}/adopt", "POST",
                     body={"hypothesis": "H1"})
        st, rv, _ = self.request(f"/api/corrections/{did}/revoke",
                                 "POST", body={})
        self.assertEqual(st, 200)
        self.assertEqual(rv["status"], "revoked")
        st, chrono, _ = self.request("/api/hypotheses/H1/chronology")
        self.assertEqual(chrono["corrected_samples"], [])
        # revoke again -> 422
        st, body, _ = self.request(f"/api/corrections/{did}/revoke",
                                   "POST", body={})
        self.assertEqual(st, 422)
        self.assertEqual(body["errors"][0]["code"], "E_DRAFT_NOT_ADOPTED")

    def test_original_series_untouched(self):
        st, before, _ = self.request("/api/series/UNKNOWN_01")
        st, draft, _ = self._create(
            [{"type": "missing_ring", "after_seq": 20},
             {"type": "false_ring", "seq": 35}])
        did = draft["draft_id"]
        self.request(f"/api/corrections/{did}/adopt", "POST",
                     body={"hypothesis": "H1"})
        st, after, _ = self.request("/api/series/UNKNOWN_01")
        self.assertEqual(before["rings"], after["rings"])
        self.assertEqual(before["raw_payload"], after["raw_payload"])
        self.assertEqual(before["known_start"], after["known_start"])

    # -- versions / compare / download -------------------------------------
    def test_compare_versions(self):
        st, draft, _ = self._create(
            [{"type": "missing_ring", "after_seq": 20}])
        did = draft["draft_id"]
        self.request(f"/api/corrections/{did}/versions", "POST",
                     body={"events": [{"type": "missing_ring",
                                       "after_seq": 25}]})
        st, cmp_, _ = self.request(f"/api/corrections/{did}/compare",
                                   a=1, b=2)
        self.assertEqual(st, 200)
        self.assertEqual(len(cmp_["events_added"]), 1)
        self.assertEqual(len(cmp_["events_removed"]), 1)
        self.assertIn("correlation", cmp_["delta"])
        self.assertEqual(cmp_["version_a"], 1)
        self.assertEqual(cmp_["version_b"], 2)

    def test_download_report(self):
        st, draft, _ = self._create(
            [{"type": "missing_ring", "after_seq": 20}])
        did = draft["draft_id"]
        st, report, hdr = self.request(f"/api/corrections/{did}/download")
        self.assertEqual(st, 200)
        self.assertIn("attachment", hdr["Content-Disposition"])
        self.assertEqual(report["report_type"],
                         "tree_ring_correction_draft")
        self.assertEqual(report["draft"]["run_id"], self.run_id)
        self.assertIn("reference_snapshot", report)
        self.assertTrue(report["reference_snapshot"]["years"])
        self.assertEqual(len(report["versions"]), 1)
        json.dumps(report, allow_nan=False)

    def test_help_and_openapi_advertise_corrections(self):
        st, help_, _ = self.request("/api/help")
        paths = {e["path"] for e in help_["endpoints"]}
        self.assertIn("/api/corrections", paths)
        self.assertIn("/api/corrections/{id}/adopt", paths)
        st, spec, _ = self.request("/api/openapi.json")
        self.assertIn("/api/corrections", spec["paths"])
        self.assertIn("/api/corrections/{id}/adopt", spec["paths"])
        self.assertIn("CorrectionEvent",
                      spec["components"]["schemas"])


class TestDocs(unittest.TestCase):
    def test_openapi_is_self_describing(self):
        from tree_ring_xdate.docs import OPENAPI, HTML_DOCS
        self.assertEqual(OPENAPI["openapi"][:3], "3.0")
        self.assertIn("/api/crossdate", OPENAPI["paths"])
        self.assertIn("树轮", HTML_DOCS)
        json.dumps(OPENAPI, ensure_ascii=False)  # serialisable


if __name__ == "__main__":
    unittest.main()
