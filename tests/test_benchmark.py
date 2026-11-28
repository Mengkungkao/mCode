"""tools/benchmark.py keeps working (the numbers themselves are recorded by hand)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@unittest.skipUnless(sys.platform.startswith("linux"), "reads /proc")
class BenchmarkTests(unittest.TestCase):
    def test_runs_and_reports_every_measure(self):
        result = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "benchmark.py"),
                                 "--idle", "0.2", "--json"], capture_output=True, text=True,
                                timeout=120, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        data = json.loads(result.stdout[result.stdout.index("{"):])
        for key in ("total_start_s", "rss_idle_mb", "idle_cpu_percent", "api_state_ms_p50",
                    "project_load_ms_p50", "page_kb_gzip"):
            self.assertIn(key, data)
        self.assertLess(data["page_kb_gzip"], data["page_kb_raw"])


if __name__ == "__main__":
    unittest.main()
