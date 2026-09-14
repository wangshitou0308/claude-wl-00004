"""End-to-end example of the chronology signal-strength workflow (offline).

Starts a throwaway server on an ephemeral port, ingests the example data
and locks the dated site cores, then measures how strong the common signal
of the chronology is, window by window: sample depth, pair correlations,
Rbar and EPS, with a leave-one-out (jackknife) delta reported for every
member -- never an automatic exclusion.  The assessment is finished, an
adjacent run of EPS-passing windows is adopted as the reliable interval,
and the master chronology plus a sliding match are then restricted to
that interval.  A second assessment with a higher threshold is created
for comparison and everything is exported as JSON.

Run from the repository root::

    python examples/signal_strength.py
"""

from __future__ import annotations

import csv as csvmod
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
        # 1) ingest the example series and create hypothesis H1
        with open(os.path.join(HERE, "samples.csv"), encoding="utf-8") as f:
            csv_text = f.read()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/series",
            data=csv_text.encode("utf-8"),
            headers={"Content-Type": "text/csv"}, method="POST")
        with urllib.request.urlopen(req) as resp:
            print("1) upload:", resp.status)
        request(port, "/api/hypotheses", "POST", {"name": "H1"})

        # 2) lock the five dated site cores at their ingested starts
        starts, cur = {}, None
        for row in csvmod.DictReader(csv_text.splitlines()):
            if row["sample_id"]:
                cur = row["sample_id"]
                if row["start_year"]:
                    starts[cur] = int(row["start_year"])
        sites = sorted(starts)
        for sid, off in starts.items():
            request(port, "/api/hypotheses/H1/locks", "POST",
                    {"sample_id": sid, "offset": off})
        print(f"2) locked {len(sites)} dated cores: {', '.join(sites)}")

        # 3) assess signal strength in 40-year windows, step 20
        st, a = request(port, "/api/signal", "POST", {
            "name": "EPS-2026", "hypothesis": "H1", "samples": sites,
            "window": 40, "step": 20, "min_samples": 3,
            "eps_threshold": 0.85, "min_pair_years": 5})
        aid = a["assessment_id"]
        print(f"3) assessment #{aid} ({a['status']}): "
              f"{a['n_windows']} windows, {a['n_eps_pass']} pass EPS")
        for w in a["windows"]:
            if w["status"] == "ok":
                print(f"   win {w['window_index']} "
                      f"{w['start_year']}..{w['end_year']}: "
                      f"depth={w['mean_depth']:.2f} "
                      f"pairs={w['n_valid_pairs']}/{w['n_pairs']} "
                      f"Rbar={w['rbar']:.3f} EPS={w['eps']:.3f} "
                      f"pass={w['eps_pass']}")
            else:
                print(f"   win {w['window_index']} "
                      f"{w['start_year']}..{w['end_year']}: "
                      f"{w['status']} -- {w['reason']}")

        # jackknife: removing a member moves EPS, but no member is dropped
        ok = next(w for w in a["windows"] if w["status"] == "ok")
        biggest = max(ok["jackknife"], key=lambda j: abs(j["eps_delta"] or 0))
        print(f"   jackknife over {len(ok['jackknife'])} members; largest "
              f"move: remove {biggest['removed_sample']} -> "
              f"dEPS={biggest['eps_delta']:+.4f} (advisory, never excluded)")

        # 4) finish the draft and adopt the longest EPS-passing run
        st, c = request(port, f"/api/signal/{aid}/complete", "POST", {})
        print(f"4) {c['status']}; pass runs "
              f"(window indexes)={c['pass_runs']}")
        st, ad = request(port, f"/api/signal/{aid}/adopt", "POST", {})
        span = ad["reliable_span"]
        print(f"   adopted {span['n_windows']} windows "
              f"{span['start_year']}..{span['end_year']} "
              f"(min EPS {span['min_eps']:.3f})")

        # 5) the pinned master / match are restricted to the reliable span
        st, m = request(port, f"/api/master?hypothesis=H1&signal_id={aid}")
        print(f"5) pinned master {m['years'][0]['year']}.."
              f"{m['years'][-1]['year']} ({len(m['years'])} years, "
              f"signal_id={m['meta']['signal_id']})")
        st, cd = request(port, "/api/crossdate", "POST", {
            "sample_id": "SITE_C02", "hypothesis": "H1",
            "offset_min": 1890, "offset_max": 2010, "min_overlap": 20,
            "signal_id": aid})
        top = cd["candidates"][0]
        print(f"   sliding match stays inside the span: "
              f"{top['overlap_start']}..{top['overlap_end']}, "
              f"run pins signal_id={cd['params']['signal_id']}")

        # 6) a stricter second assessment for comparison (no auto-adoption)
        st, b = request(port, "/api/signal", "POST", {
            "name": "EPS-STRICT", "hypothesis": "H1", "samples": sites,
            "window": 40, "step": 20, "eps_threshold": 0.99})
        st, cmp_ = request(port,
                           f"/api/signal/compare?a={aid}&b={b['assessment_id']}")
        print(f"6) strict assessment #{b['assessment_id']}: "
              f"{b['n_eps_pass']}/{b['n_windows']} windows pass 0.99; "
              f"comparison shares {cmp_['n_common_windows']} windows")

        # 7) filtering + full JSON export of the adopted assessment
        st, filt = request(
            port, f"/api/signal/{aid}?eps_pass=1&year_from=1960")
        st, report = request(port, f"/api/signal/{aid}/download")
        print(f"7) passing windows since 1960: "
              f"{[w['window_index'] for w in filt['windows']]}; "
              f"exported {report['report_type']} with "
              f"{len(report['sources'])} frozen sources and "
              f"{len(report['windows'])} windows")

        # 8) retiring removes the pin for new jobs; old runs keep their range
        st, r = request(port, f"/api/signal/{aid}/retire", "POST", {})
        print(f"8) retired: status={r['status']} "
              f"(the historical run keeps its pinned signal_id)")
    finally:
        server.shutdown()
        server.server_close()
        server.db_conn.close()
        tmp.cleanup()


if __name__ == "__main__":
    main()
