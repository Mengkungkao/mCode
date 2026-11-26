"""The install smoke test (manifest ``test`` hook, ``run.sh --selftest``).

Fast and deterministic. Uses a temporary folder only; never takes the screen,
opens a serial port, records, plays sound, contacts Claude or the internet,
and never runs a compiler. Exits 1 with the reason on the first failure.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import traceback
import urllib.error
import urllib.request


def _check(name: str, ok: bool, detail: str = "") -> None:
    if not ok:
        raise AssertionError(f"{name}: {detail}")
    print(f"  ok  {name}")


def main() -> int:
    base = tempfile.mkdtemp(prefix="mcode-selftest-")
    try:
        _run(base)
    except Exception as exc:
        traceback.print_exc()
        print(f"mcode selftest FAILED: {exc}", file=sys.stderr)
        return 1
    finally:
        shutil.rmtree(base, ignore_errors=True)
    print("mcode selftest passed")
    return 0


def _run(base: str) -> None:
    from . import VERSION
    from .core.config import Settings
    from .core.jobs import JobSpec
    from .lcd import qr
    from .preview import FakeBoard, FakeStatusMonitor, build_demo, scenes
    from .projects import boards, templates
    from .projects.model import Project

    print(f"mCode Studio {VERSION} selftest")
    settings = Settings(os.path.join(base, "settings.json"))
    settings.set("ai.mode", "assisted")
    _check("settings round trip", Settings(os.path.join(base, "settings.json")).get("ai.mode") == "assisted")
    try:
        settings.set("ai.mode", "everything")
        _check("settings validation", False, "an invalid value was accepted")
    except ValueError:
        _check("settings validation", True)
    for board in boards.BOARDS:
        for toolchain in board.toolchains:
            for framework in board.frameworks(toolchain):
                files = templates.files_for(Project(path=os.path.join(base, "x"), name="X",
                                                    platform=board.platform, board_id=board.id,
                                                    toolchain=toolchain, framework=framework))
                if not files:
                    raise AssertionError(f"no template for {board.id} {toolchain}")
    _check("templates for every board/toolchain", True)
    code = qr.QrCode(b"https://claude.ai/oauth/authorize?test", "M")
    _check("QR encoder", code.size == code.version * 4 + 17)

    studio = build_demo(os.path.join(base, "demo"))
    try:
        project = studio.projects.current()
        _check("demo projects", project is not None and len(studio.projects.list()) == 3)
        _check("simulated USB boards", len(studio.devices.list()) == 2)
        job = studio.jobs.submit(JobSpec(kind="tool", title="echo", lane="tool",
                                         argv=[sys.executable, "-c", "print('mcode-ok')"],
                                         timeout=20))
        studio.jobs.wait(job.id, 20)
        _check("job runner", job.ok and "mcode-ok" in job.output(), job.error)
        from .lcd.ui import DeviceUI
        ui = DeviceUI(studio, board=FakeBoard(), keyboard=False, threaded=False,
                      status=FakeStatusMonitor())
        count = 0
        for _name, setup in scenes(studio, ui):
            setup()
            image = ui.render()
            if image.size != (240, 280):
                raise AssertionError(f"{_name} rendered {image.size}")
            count += 1
        _check(f"{count} LCD screens render", count > 30)
        from .web.server import WebServer
        web = WebServer(studio, port=0)
        _check("desktop IDE starts", web.start())
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{web.port}/", timeout=5) as page:
                body = page.read().decode()
            _check("desktop IDE serves the pairing page", "Connect this browser" in body)
            request = urllib.request.Request(f"http://127.0.0.1:{web.port}/api/state")
            try:
                urllib.request.urlopen(request, timeout=5)
                _check("API refuses requests without the token", False, "it answered")
            except urllib.error.HTTPError as exc:
                _check("API refuses requests without the token", exc.code == 401, str(exc.code))
        finally:
            web.stop()
    finally:
        studio.stop(timeout=0.5)

