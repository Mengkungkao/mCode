"""Settings, storage, events, history, redaction and the job runner."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import unittest

from app.core import storage
from app.core.config import FIELDS, Settings
from app.core.events import EventBus
from app.core.history import History
from app.core.jobs import CANCELLED, FAILED, SUCCEEDED, TIMEOUT, JobManager, JobSpec
from app.core.redact import redact

from .helpers import TempCase


class SettingsTests(TempCase):
    def test_defaults_and_round_trip(self):
        path = os.path.join(self.tmp, "settings.json")
        settings = Settings(path)
        self.assertEqual(settings.get("ai.mode"), "ask")
        settings.set("ai.mode", "advanced")
        settings.set("hw.default_baud", "9600")
        again = Settings(path)
        self.assertEqual(again.get("ai.mode"), "advanced")
        self.assertEqual(again.get("hw.default_baud"), 9600)
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)

    def test_invalid_values_refused(self):
        settings = Settings(None)
        with self.assertRaises(ValueError):
            settings.set("ai.mode", "anything")
        with self.assertRaises(ValueError):
            settings.set("hw.default_baud", 12345)
        with self.assertRaises(ValueError):
            settings.set("web.port", 80)
        with self.assertRaises(KeyError):
            settings.set("no.such", 1)

    def test_corrupt_file_kept_and_defaults_used(self):
        path = os.path.join(self.tmp, "settings.json")
        with open(path, "w") as handle:
            handle.write("{not json")
        settings = Settings(path)
        self.assertEqual(settings.get("ai.mode"), "ask")
        kept = [n for n in os.listdir(self.tmp) if n.startswith("settings.json.corrupt-")]
        self.assertEqual(len(kept), 1)

    def test_bad_single_value_falls_back_and_unknown_keys_kept(self):
        path = os.path.join(self.tmp, "settings.json")
        storage.atomic_write_json(path, {"ai.mode": "rogue", "hw.default_baud": 57600,
                                         "future.key": "x"})
        settings = Settings(path)
        self.assertEqual(settings.get("ai.mode"), "ask")
        self.assertEqual(settings.get("hw.default_baud"), 57600)
        self.assertTrue(settings.problems)
        settings.set("ai.mode", "assisted")
        with open(path) as handle:
            self.assertEqual(json.load(handle)["future.key"], "x")

    def test_cycle_for_one_button(self):
        settings = Settings(None)
        self.assertEqual(settings.cycle("ai.mode"), "assisted")
        self.assertEqual(settings.cycle("ai.mode"), "advanced")
        self.assertEqual(settings.cycle("ai.mode"), "ask")
        self.assertTrue(settings.cycle("ai.voice_autosend"))

    def test_every_field_default_is_valid(self):
        for field in FIELDS:
            self.assertEqual(field.validate(field.default), field.default, field.key)


class EventAndHistoryTests(TempCase):
    def test_bus_callbacks_and_bounded_queue(self):
        bus = EventBus()
        seen = []
        bus.subscribe(lambda t, d: seen.append((t, d)))
        bus.subscribe(lambda t, d: 1 / 0)              # a broken listener
        token, q = bus.subscribe_queue(maxsize=2)
        for i in range(5):
            bus.publish("x", i)
        self.assertEqual(len(seen), 5)
        self.assertEqual([q.get_nowait()[1] for _ in range(2)], [3, 4])
        bus.unsubscribe(token)

    def test_history_redacts_and_filters(self):
        history = History(os.path.join(self.tmp, "h.jsonl"))
        history.add("build", project_id="a", headline="key sk-ant-abcdefghijklmnop leaked")
        history.add("upload", project_id="b", state="succeeded")
        records = history.recent(["build"])
        self.assertEqual(len(records), 1)
        self.assertNotIn("abcdefghijklmnop", records[0]["headline"])
        self.assertEqual(history.recent(project_id="b")[0]["kind"], "upload")


class LogPruneTests(TempCase):
    def test_old_logs_are_removed(self):
        studio = self.studio(hardware=False, ai=False)
        old = os.path.join(studio.paths.job_logs, "old.log")
        new = os.path.join(studio.paths.job_logs, "new.log")
        for path in (old, new):
            open(path, "w").close()
        past = time.time() - 86400 * 40
        os.utime(old, (past, past))
        self.assertEqual(studio.prune_logs(), 1)
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.exists(new))


class RedactTests(unittest.TestCase):
    def test_patterns(self):
        text = ("OPENAI_API_KEY=sk-proj-1234567890abcdefghij token ghp_abcdefghijklmnopqrstuv "
                "https://claude.ai/oauth/authorize?code=secret123&state=xyz "
                "Authorization: Bearer abc.def.ghi http://user:pass@host/x")
        out = redact(text)
        for secret in ("1234567890abcdefghij", "ghp_abcdefghijklmnopqrstuv", "secret123",
                       "abc.def.ghi", "pass@"):
            self.assertNotIn(secret, out)


class JobTests(TempCase):
    def manager(self, **kw):
        events = []
        manager = JobManager(os.path.join(self.tmp, "jobs"), publish=lambda t, d: events.append(t),
                             **kw)
        self.addCleanup(manager.shutdown, 1)
        return manager, events

    def test_streams_output_and_collapses_carriage_returns(self):
        manager, events = self.manager()
        code = "import sys\nprint('a')\nsys.stdout.write('p 1%\\rp 50%\\rp 100%\\n')\nprint('b')"
        job = manager.submit(JobSpec("build", "t", argv=[sys.executable, "-c", code], timeout=10))
        manager.wait(job.id, 10)
        self.assertEqual(job.state, SUCCEEDED)
        self.assertEqual(job.tail()[-3:], ["a", "p 100%", "b"])
        self.assertIn("job.line", events)
        with open(job.log_path) as handle:
            self.assertIn("p 100%", handle.read())

    def test_cancel_kills_the_process_group(self):
        manager, _ = self.manager()
        marker = os.path.join(self.tmp, "child-alive")
        child = f"import time; time.sleep(3); open({marker!r}, 'w')"
        code = ("import subprocess, sys, time\n"
                f"subprocess.Popen([sys.executable, '-c', {child!r}])\n"
                "time.sleep(30)")
        job = manager.submit(JobSpec("build", "t", argv=[sys.executable, "-c", code], timeout=60))
        deadline = time.time() + 5
        while job.state != "running" and time.time() < deadline:
            time.sleep(0.02)
        time.sleep(0.3)
        self.assertTrue(manager.cancel(job.id))
        manager.wait(job.id, 8)
        self.assertEqual(job.state, CANCELLED)
        time.sleep(3.5)
        self.assertFalse(os.path.exists(marker), "the grandchild survived the cancel")

    def test_timeout(self):
        manager, _ = self.manager()
        job = manager.submit(JobSpec("build", "t", argv=["sleep", "20"], timeout=0.5))
        manager.wait(job.id, 8)
        self.assertEqual(job.state, TIMEOUT)

    def test_one_build_at_a_time_and_resources_never_overlap(self):
        manager, _ = self.manager()
        running = []
        overlap = []
        lock = threading.Lock()

        def work(job):
            with lock:
                running.append(job.id)
                if len(running) > 1:
                    overlap.append(list(running))
            time.sleep(0.2)
            with lock:
                running.remove(job.id)

        jobs = [manager.submit(JobSpec("build", f"b{i}", func=work, lane="build")) for i in range(3)]
        jobs += [manager.submit(JobSpec("upload", f"u{i}", func=work, lane="tool",
                                        resource="/dev/ttyUSB0")) for i in range(2)]
        for job in jobs:
            manager.wait(job.id, 10)
        self.assertTrue(all(j.state == SUCCEEDED for j in jobs))
        for group in overlap:
            names = [manager.get(i).title for i in group]
            self.assertFalse(sum(n.startswith("b") for n in names) > 1, names)
            self.assertFalse(sum(n.startswith("u") for n in names) > 1, names)

    def test_python_job_steps_and_failure_message(self):
        manager, _ = self.manager()

        def work(job):
            job.run([sys.executable, "-c", "print('step one')"])
            job.set_phase("second", 0.5)
            job.run(["false"], what="the second step")

        job = manager.submit(JobSpec("build", "t", func=work))
        manager.wait(job.id, 10)
        self.assertEqual(job.state, FAILED)
        self.assertIn("the second step failed", job.error)
        self.assertIn("step one", job.output())

    def test_missing_program(self):
        manager, _ = self.manager()
        job = manager.submit(JobSpec("build", "t", argv=["/nonexistent/tool"]))
        manager.wait(job.id, 5)
        self.assertEqual(job.state, FAILED)
        self.assertIn("cannot start", job.error)

    def test_shutdown_cancels_everything(self):
        manager, _ = self.manager()
        job = manager.submit(JobSpec("build", "t", argv=["sleep", "30"], timeout=60))
        time.sleep(0.2)
        manager.shutdown(5)
        self.assertEqual(job.state, CANCELLED)
        with self.assertRaises(RuntimeError):
            manager.submit(JobSpec("build", "t", argv=["true"]))


if __name__ == "__main__":
    unittest.main()
