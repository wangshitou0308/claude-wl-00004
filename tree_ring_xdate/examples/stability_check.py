"""End-to-end example of the local stability-check workflow (offline, stdlib only).

Starts a throwaway server on an ephemeral port, ingests the example data,
locks the undated core at a deliberately wrong offset, and asks whether the
locked placement is *locally* stable: sliding windows are compared against
the master chronology at the current position and at neighbouring shifts.
The check flags the misplacement interval (with measurement seqs), the lock
is then corrected, a second check confirms stability, and the two checks
are compared and exported.

Run from the repository root::

    python examples/stability_check.py
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


def request(port, path, method="GET", body=None):
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
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
        # 1) ingest example series, create hypothesis H1
        with open(os.path.join(HERE, "samples.csv"), encoding="utf-8") as f:
            csv_text = f.read()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/series",
            data=csv_text.encode("utf-8"),
            headers={"Content-Type": "text/csv"}, method="POST")
        with urllib.request.urlopen(req) as resp:
            print("1) upload:", resp.status)
        request(port, "/api/hypotheses", "POST", {"name": "H1"})

        # 2) lock UNKNOWN_01 two years too late (true start is 1948)
        request(port, "/api/hypotheses/H1/locks", "POST",
                {"sample_id": "UNKNOWN_01", "offset": 1950})
        print("2) UNKNOWN_01 locked at 1950 (deliberately wrong by +2 yr)")

        # 3) stability check: 20-year windows, step 10, ±4 yr search
        st, chk = request(port, "/api/stability", "POST",
                          {"hypothesis": "H1", "sample_id": "UNKNOWN_01",
                           "window": 20, "step": 10, "min_valid_years": 10,
                           "run_threshold": 2, "search_radius": 4})
        print(f"3) check #{chk['check_id']}: {chk['n_windows']} windows, "
              f"{chk['n_flags']} flag(s)")
        for w in chk["windows"]:
            cur = w["current"]["correlation"] if w["current"] else None
            print(f"   win {w['window_index']} "
                  f"{w['start_year']}..{w['end_year']}: "
                  f"best_shift={w['best_shift']:+d} "
                  f"tied={w['tied_shifts']} r@current={cur:.3f}")
        for fl in chk["flags"]:
            print(f"   FLAG: {fl['message']}")

        # 4) re-lock at the correct offset and check again
        request(port, "/api/hypotheses/H1/locks", "POST",
                {"sample_id": "UNKNOWN_01", "offset": 1948})
        st, chk2 = request(port, "/api/stability", "POST",
                           {"hypothesis": "H1", "sample_id": "UNKNOWN_01",
                            "window": 20, "step": 10, "min_valid_years": 10,
                            "run_threshold": 2, "search_radius": 4})
        print(f"4) re-locked at 1948; check #{chk2['check_id']}: "
              f"{chk2['n_flags']} flag(s), "
              f"best shifts={[w['best_shift'] for w in chk2['windows']]}")

        # 5) compare the two checks (the flag must be gone)
        st, cmp_ = request(
            port,
            f"/api/stability/compare?a={chk['check_id']}&b={chk2['check_id']}")
        print(f"5) compare: {cmp_['n_common_windows']} common windows, "
              f"flags only in A={len(cmp_['flags_only_in_a'])}, "
              f"only in B={len(cmp_['flags_only_in_b'])}")

        # 6) filtered view + JSON export of the first check
        st, filt = request(
            port, f"/api/stability/{chk['check_id']}?shift=-2&status=ok")
        st, report = request(
            port, f"/api/stability/{chk['check_id']}/download")
        print(f"6) windows with best_shift=-2: {len(filt['windows'])}; "
              f"exported {report['report_type']} with "
              f"{len(report['windows'])} windows")
    finally:
        server.shutdown()
        server.server_close()
        server.db_conn.close()
        tmp.cleanup()


if __name__ == "__main__":
    main()
