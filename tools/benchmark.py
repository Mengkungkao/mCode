#!/usr/bin/env python3
"""Measure mCode on this machine: start-up, idle memory and CPU, threads, API
latency, page weight. Repeatable on the dev machine and on a board:

    python3 tools/benchmark.py              # 30 s idle window
    python3 tools/benchmark.py --idle 120   # longer idle window
    python3 tools/benchmark.py --json       # machine-readable

It uses a throw-away data folder and project folder (your settings, projects
and paired browsers are not touched), the real tool detection and the real
USB discovery, and binds the desktop IDE to 127.0.0.1 on a free port. Linux
only (reads /proc). Results go to stdout; record them in
docs/PERFORMANCE_BENCHMARK.md with the date, revision and machine.
"""

from __future__ import annotations

import argparse
import gzip
import http.client
import json
import os
import platform
import shutil
import statistics
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def rss_mb() -> float:
    with open("/proc/self/status") as handle:
        for line in handle:
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024
    return 0.0


def cpu_seconds() -> float:
    with open("/proc/self/stat") as handle:
        fields = handle.read().rsplit(")", 1)[1].split()
    ticks = os.sysconf("SC_CLK_TCK")
    return (int(fields[11]) + int(fields[12])) / ticks          # utime + stime


def request(port: int, path: str, cookie: str, method: str = "GET", body=None) -> tuple:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    headers = {"Host": f"127.0.0.1:{port}", "Cookie": f"mcode_session={cookie}",
               "Accept-Encoding": "gzip"}
    payload = None
    if body is not None:
        payload = json.dumps(body).encode()
        headers.update({"Content-Type": "application/json", "X-mCode": "1"})
    start = time.perf_counter()
    conn.request(method, path, body=payload, headers=headers)
    response = conn.getresponse()
    data = response.read()
    elapsed = (time.perf_counter() - start) * 1000
    conn.close()
    return response.status, data, elapsed, response


def percentile(values, fraction):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1))))]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--idle", type=float, default=30.0, help="idle window in seconds")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    out = {"machine": platform.machine(), "python": platform.python_version(),
           "time": time.strftime("%Y-%m-%d %H:%M:%S")}
    tmp = tempfile.mkdtemp(prefix="mcode-bench-")
    try:
        rss0 = rss_mb()
        t0 = time.perf_counter()
        from app.studio import Studio
        from app.web.server import WebServer
        t_import = time.perf_counter()
        studio = Studio(data_dir=os.path.join(tmp, "data"), voice=False)
        studio.settings.set("dev.projects_dir", os.path.join(tmp, "projects"), save=False)
        t_build = time.perf_counter()
        studio.start()
        t_start = time.perf_counter()
        web = WebServer(studio, host="127.0.0.1", port=0)
        web.start()
        studio.web = web
        t_web = time.perf_counter()
        out.update(import_s=round(t_import - t0, 3), construct_s=round(t_build - t_import, 3),
                   start_s=round(t_start - t_build, 3), web_s=round(t_web - t_start, 3),
                   total_start_s=round(t_web - t0, 3))
        studio.tools.refresh(wait=True)
        out["tools_detect_s"] = round(time.perf_counter() - t_web, 3)
        out["rss_baseline_mb"] = round(rss0, 1)
        out["rss_started_mb"] = round(rss_mb(), 1)
        cookie, _ = web.sessions.create("admin", "benchmark", "127.0.0.1", "benchmark")
        # Idle: nothing happening, the IDE not open.
        cpu0, wall0 = cpu_seconds(), time.monotonic()
        time.sleep(args.idle)
        cpu1, wall1 = cpu_seconds(), time.monotonic()
        out["idle_window_s"] = args.idle
        out["idle_cpu_percent"] = round(100 * (cpu1 - cpu0) / (wall1 - wall0), 2)
        out["threads_idle"] = threading.active_count()
        out["rss_idle_mb"] = round(rss_mb(), 1)
        # API latency.
        status, _, _, _ = request(web.port, "/api/state", cookie)
        assert status == 200, status
        times = [request(web.port, "/api/state", cookie)[2] for _ in range(40)]
        out["api_state_ms_p50"] = round(statistics.median(times), 1)
        out["api_state_ms_p90"] = round(percentile(times, 0.9), 1)
        t = time.perf_counter()
        status, data, _, _ = request(web.port, "/api/projects/create", cookie, "POST",
                                     {"name": "Bench", "board_id": "esp32-s3",
                                      "toolchain": "arduino-cli"})
        out["project_create_ms"] = round((time.perf_counter() - t) * 1000, 1)
        pid = json.loads(data)["project"]["id"]
        times = [request(web.port, f"/api/project/{pid}", cookie)[2] for _ in range(20)]
        out["project_load_ms_p50"] = round(statistics.median(times), 1)
        # Page weight: what a browser downloads on the first visit (gzip).
        static = os.path.join(ROOT, "app", "web", "static")
        with open(os.path.join(static, "index.html"), encoding="utf-8") as handle:
            html = handle.read()
        import re
        assets = re.findall(r'(?:href|src)="/static/([^"]+)"', html)
        raw = gz = 0
        for rel in assets:
            status, body, _, response = request(web.port, "/static/" + rel, cookie)
            gz += len(body)
            raw += len(gzip.decompress(body)) if response.getheader("Content-Encoding") == "gzip" \
                else len(body)
        out["page_assets"] = len(assets)
        out["page_kb_raw"] = round(raw / 1024)
        out["page_kb_gzip"] = round(gz / 1024)
        out["rss_after_requests_mb"] = round(rss_mb(), 1)
        studio.stop(timeout=2)
        web.stop()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if args.json:
        print(json.dumps(out, indent=2))
    else:
        width = max(len(k) for k in out)
        for key, value in out.items():
            print(f"{key:<{width}}  {value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
