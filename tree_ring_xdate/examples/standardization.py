"""End-to-end example of the sequence-standardization workflow (offline, stdlib only).

After cross-dating has locked (or correction drafts have adopted) the
sample-to-year mapping, this example builds a *standardization plan*:

1. create a draft plan choosing, independently per sample, a horizontal
   mean, a negative-exponential curve or a fixed-window centred moving
   average;
2. preview the expected-growth curves and ring-width indices (missing
   rings keep index 0; false rings never fit);
3. validate every sample (failures are localised by measurement seq, the
   chosen method is never switched automatically);
4. adopt the plan, which freezes the source mapping and the fitted
   curves;
5. build the master chronology and run a stability check against the
   frozen standardized version;
6. compare two versions and export the plan as JSON.

Run from the repository root::

    python examples/standardization.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import urllib.request
import urllib.error

sys.path.insert(0, os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")))

from tree_ring_xdate.server import make_server  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def request(port, path, method="GET", body=None):
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
                                 data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def main():
    tmp = tempfile.TemporaryDirectory()
    server = make_server("127.0.0.1", 0,
                         os.path.join(tmp.name, "demo.db"), log=None)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        # 1) ingest, hypothesis, lock the samples
        with open(os.path.join(HERE, "samples.csv"), encoding="utf-8") as f:
            csv_text = f.read()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/series",
            data=csv_text.encode("utf-8"),
            headers={"Content-Type": "text/csv"}, method="POST")
        with urllib.request.urlopen(req) as resp:
            print("1) upload:", resp.status)
        request(port, "/api/hypotheses", "POST", {"name": "H1"})
        request(port, "/api/hypotheses/H1/locks", "POST",
                {"sample_id": "SITE_A01", "offset": 1901})
        request(port, "/api/hypotheses/H1/locks", "POST",
                {"sample_id": "SITE_B01", "offset": 1920})
        request(port, "/api/hypotheses/H1/locks", "POST",
                {"sample_id": "UNKNOWN_01", "offset": 1948})

        # 2) create a draft: three samples, three different methods
        st, plan = request(port, "/api/standardizations", "POST", {
            "name": "STD-2026", "hypothesis": "H1",
            "samples": [
                {"sample_id": "SITE_A01",
                 "method": "negative_exponential"},
                {"sample_id": "SITE_B01",
                 "method": "moving_average",
                 "parameters": {"window": 21}},
                {"sample_id": "UNKNOWN_01", "method": "mean"}]})
        print(f"2) draft #{plan['standardization_id']}: {st}, "
              f"{plan['preview']['n_valid_samples']}/"
              f"{plan['preview']['n_samples']} samples valid")
        for s in plan["preview"]["samples"]:
            d = s["diagnostics"]
            if s["method"] == "negative_exponential":
                p = d["parameters"]
                print(f"   {s['sample_id']:11s} exp  b={p['b_per_year']:.5f}"
                      f"/yr  R2={d['r_squared']:.3f}  "
                      f"iters={d['iterations']}")
            elif s["method"] == "moving_average":
                print(f"   {s['sample_id']:11s} MA   window={d['window']}  "
                      f"edge-truncated positions="
                      f"{d['n_truncated_edge_positions']}")
            else:
                print(f"   {s['sample_id']:11s} mean expected="
                      f"{d['expected']:.3f}  index mean="
                      f"{d['index_mean']:.3f}")

        # 3) validate -> validated
        st, val = request(port,
                          "/api/standardizations/1/validate", "POST", {})
        print(f"3) validate: {st} -> {val['status']}")

        # 4) ad-hoc preview of an alternative window (no version created)
        st, adhoc = request(port, "/api/standardizations/1/preview", "POST",
                            {"samples": [
                                {"sample_id": "SITE_B01",
                                 "method": "moving_average",
                                 "parameters": {"window": 11}}]})
        s = adhoc["preview"]["samples"][0]
        print(f"4) ad-hoc preview window=11: valid={s['valid']} "
              f"(plan still has one version)")

        # 5) adopt: source mapping + curves are frozen into version 1
        st, adopted = request(port, "/api/standardizations/1/adopt",
                              "POST", {})
        print(f"5) adopt: {st} -> {adopted['status']} "
              f"v{adopted['adopted_version']} (curves frozen)")

        # 6) standardized master chronology + a stability check on the pin
        st, master = request(port,
                             "/api/master?hypothesis=H1&standardization_id=1")
        ids = sorted(m["sample_id"] for m in master["meta"]["members"])
        print(f"6) standardized master: {master['meta']['start']}.."
              f"{master['meta']['end']}, members={ids}")
        st, chk = request(port, "/api/stability", "POST", {
            "hypothesis": "H1", "sample_id": "UNKNOWN_01",
            "window": 20, "step": 10, "min_valid_years": 10,
            "run_threshold": 2, "search_radius": 4,
            "standardization_id": 1})
        print(f"   stability check #{chk['check_id']}: "
              f"std=v{chk['params']['standardization_version']}, "
              f"{chk['n_flags']} flag(s), "
              f"reference stale={chk['reference_status']['stale']}")

        # 7) retiring releases the live chronology but keeps old jobs' basis
        st, ret = request(port, "/api/standardizations/1/retire", "POST", {})
        print(f"7) retire: {st} -> {ret['status']}")

        # 8) JSON export (download)
        st, report = request(port, "/api/standardizations/1/download")
        n_versions = len(report["versions"])
        print(f"8) export {report['report_type']}: {n_versions} version(s), "
              f"plan status={report['plan']['status']}")
    finally:
        server.shutdown()
        server.server_close()
        server.db_conn.close()
        tmp.cleanup()


if __name__ == "__main__":
    main()
