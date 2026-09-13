"""End-to-end example of the correction-draft workflow (offline, stdlib only).

Starts a throwaway server on an ephemeral port, ingests the example data,
cross-dates the undated core, then builds a correction draft with a
missing-ring gap and a false ring, previews the segmented statistics,
adopts it into a hypothesis, compares two versions and exports JSON.

Run from the repository root::

    python examples/correction_workflow.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import urllib.request

sys.path.insert(0, os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")))

from tree_ring_xdate.server import make_server  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def request(port, path, method="GET", body=None, ctype="application/json"):
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = ctype
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
                                 data=data, method=method, headers=headers)
    with urllib.request.urlopen(req) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


def main():
    tmp = tempfile.TemporaryDirectory()
    server = make_server("127.0.0.1", 0,
                         os.path.join(tmp.name, "demo.db"), log=None)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        # 1) ingest example series
        with open(os.path.join(HERE, "samples.csv"), encoding="utf-8") as f:
            csv_text = f.read()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/series",
            data=csv_text.encode("utf-8"),
            headers={"Content-Type": "text/csv"}, method="POST")
        with urllib.request.urlopen(req) as resp:
            print("1) upload:", resp.status)

        # 2) sliding cross-date of the undated core
        st, cd = request(port, "/api/crossdate", "POST",
                         {"sample_id": "UNKNOWN_01", "offset_min": 1900,
                          "offset_max": 1990, "min_overlap": 30})
        best = cd["candidates"][0]
        print(f"2) crossdate: best offset {best['offset']} "
              f"r={best['correlation']:.3f} run_id={cd['run_id']}")

        # 3) correction draft: one missing ring after measurement 20,
        #    measurement 35 declared a false ring
        st, draft = request(
            port, "/api/corrections", "POST",
            {"sample_id": "UNKNOWN_01", "offset": best["offset"],
             "run_id": cd["run_id"], "min_overlap": 10,
             "events": [{"type": "missing_ring", "after_seq": 20},
                        {"type": "false_ring", "seq": 35}]})
        did = draft["draft_id"]
        print(f"3) draft #{did} created; segments:")
        for s in draft["evaluation"]["segments"]:
            r = s["correlation"]
            print(f"   seg {s['segment']}: {s['start_year']}..{s['end_year']} "
                  f"n={s['n_overlap']} r={r:.3f}" if r is not None else
                  f"   seg {s['segment']}: n={s['n_overlap']} (no variance)")
        delta = draft["changes_vs_candidate"]["delta"]
        print(f"   whole-series r change vs candidate: "
              f"{delta['correlation']:+.4f}")

        # 4) alternative version + comparison
        st, v2 = request(
            port, f"/api/corrections/{did}/versions", "POST",
            {"events": [{"type": "missing_ring", "after_seq": 21},
                        {"type": "false_ring", "seq": 35}]})
        st, cmp_ = request(port, f"/api/corrections/{did}/compare?a=1&b=2")
        print(f"4) version 2 created; compare 1->2: "
              f"delta r={cmp_['delta']['correlation']:+.4f}")

        # 5) adopt into hypothesis H1 and inspect the chronology
        st, ad = request(port, f"/api/corrections/{did}/adopt", "POST",
                         {"hypothesis": "H1", "version": 2})
        st, chrono = request(port, "/api/hypotheses/H1/chronology")
        print(f"5) adopted v2 into H1; corrected samples: "
              f"{chrono['corrected_samples']}")

        # 6) JSON export + revoke
        st, report = request(port, f"/api/corrections/{did}/download")
        st, rv = request(port, f"/api/corrections/{did}/revoke", "POST", {})
        print(f"6) exported {len(report['versions'])} versions; "
              f"revoked -> {rv['status']}")
    finally:
        server.shutdown()
        server.server_close()
        server.db_conn.close()
        tmp.cleanup()


if __name__ == "__main__":
    main()
