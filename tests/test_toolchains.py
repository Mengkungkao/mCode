"""Toolchains: diagnostics, backend commands, installers, builds (fake tools)."""

from __future__ import annotations

import hashlib
import io
import json
import os
import tarfile
import time
import unittest

from app.core.config import Settings
from app.core.jobs import JobManager, JobSpec
from app.projects.model import Project
from app.toolchains import backends, diagnostics, installer
from app.toolchains.backends import BackendError
from app.toolchains.detect import Tool, Toolchains

from .helpers import FAKE_ARDUINO_CLI, TempCase, write_script

GCC_OUTPUT = """\
/home/u/p/src/main.cpp: In function 'void loop()':
/home/u/p/src/main.cpp:12:5: error: 'servo' was not declared in this scope
   12 |     servo.write(90);
/home/u/p/src/main.cpp:3:10: fatal error: ServoDriver.h: No such file or directory
/home/u/.platformio/packages/framework/cores/x.h:40:2: warning: deprecated thing [-Wcpp]
main.cpp:(.text.loop+0x10): undefined reference to `servo_init()'
collect2: error: ld returned 1 exit status
Multiple libraries were found for "Servo.h"
  Used: /home/u/Arduino/libraries/Servo
  Not used: /home/u/.arduino15/libraries/Servo
Sketch uses 4100 bytes (12%) of program storage space. Maximum is 32256 bytes.
Global variables use 210 bytes (10%) of dynamic memory, leaving 1838 bytes for local variables.
RAM:   [=         ]   6.8% (used 22336 bytes from 327680 bytes)
Flash: [==        ]  21.3% (used 279181 bytes from 1310720 bytes)
Error during build: exit status 1
"""


class DiagnosticsTests(unittest.TestCase):
    def test_parse(self):
        report = diagnostics.parse(GCC_OUTPUT, "/home/u/p")
        errors = report.errors
        self.assertEqual(errors[0].file, "src/main.cpp")
        self.assertTrue(errors[0].in_project)
        self.assertEqual((errors[0].line, errors[0].column), (12, 5))
        self.assertEqual(report.missing_headers, ["ServoDriver.h"])
        self.assertEqual(report.undefined, ["servo_init()"])
        self.assertEqual(report.conflicts[0]["header"], "Servo.h")
        self.assertEqual(len(report.conflicts[0]["not_used"]), 1)
        self.assertFalse(report.warnings[0].in_project)
        self.assertEqual(report.size["flash"]["percent"], 21.3)
        self.assertEqual(report.size["ram"]["used"], 22336)
        self.assertEqual(report.headline(), "Missing library: ServoDriver")
        self.assertTrue(report.tool_errors)

    def test_progress_and_phase(self):
        self.assertEqual(diagnostics.progress("Writing at 0x00010000... (12 %)"), 0.12)
        self.assertAlmostEqual(diagnostics.progress("Writing at 0x00010000 [=====>    ] 45.3% 123/272 bytes..."), 0.453)
        self.assertEqual(diagnostics.progress("Writing | ################################################## | 100% 1.23s "), 1.0)
        self.assertEqual(diagnostics.progress("[245/980] Building C object esp-idf/x.c.obj"), 245 / 980)
        self.assertIsNone(diagnostics.progress("Compiling sketch..."))
        self.assertEqual(diagnostics.phase("Connecting...."), "Connecting to the board")
        self.assertEqual(diagnostics.phase("Hash of data verified."), "Verifying")

    def test_chip_parsing(self):
        out = backends.parse_chip("Detecting chip type... ESP32-S3\nChip type: ESP32-S3 (QFN56) "
                                  "(revision v0.2)\nFeatures: Wi-Fi, BT 5 (LE)\nCrystal frequency: 40MHz\n"
                                  "MAC: dc:da:0c:11:22:33\n")
        self.assertEqual(out["chip"], "ESP32-S3")
        self.assertEqual(out["mac"], "dc:da:0c:11:22:33")
        self.assertEqual(backends.parse_chip("Chip is ESP32-D0WD-V3 (revision v3.0)")["chip"], "ESP32-D0WD-V3")
        self.assertEqual(backends.parse_chip("A fatal error occurred: Failed to connect"), {})


class BackendCommandTests(TempCase):
    def tools(self, **found):
        settings = Settings(None)
        tools = Toolchains(settings, os.path.join(self.tmp, "bin"), os.path.join(self.tmp, "venv"),
                           home=self.home)
        for tool_id in ("arduino-cli", "platformio", "esptool", "make", "esp-idf", "cppcheck", "cc",
                        "ccache"):
            if tool_id in found:
                tools._tools[tool_id] = Tool(tool_id, tool_id, found[tool_id], found.get(tool_id + "_v", "5.3.1"), "test")
            else:
                tools._tools[tool_id] = Tool(tool_id, tool_id, problem="not installed")
        return tools, settings

    def project(self, board="esp32-s3", toolchain="arduino-cli", framework="arduino", **kw):
        path = os.path.join(self.tmp, "proj")
        os.makedirs(path, exist_ok=True)
        return Project(path=path, name="Proj", platform="esp32", board_id=board, toolchain=toolchain,
                       framework=framework, **kw)

    def test_arduino_commands(self):
        tools, settings = self.tools(**{"arduino-cli": "/x/arduino-cli"})
        backend = backends.ArduinoCliBackend(tools, settings)
        project = self.project(upload_speed=460800)
        build = backend.build(project).steps[0]
        self.assertEqual(build[:4], ["/x/arduino-cli", "compile", "--fqbn",
                                     "esp32:esp32:esp32s3:UploadSpeed=460800"])
        self.assertIn("--additional-urls", build)
        self.assertEqual(build[-1], project.path)
        self.assertIn("--clean", backend.build(project, clean=True).steps[0])
        upload = backend.upload(project, "/dev/ttyACM0", speed=921600).steps[0]
        self.assertIn("esp32:esp32:esp32s3:UploadSpeed=921600", upload)
        self.assertNotIn("--verify", upload)          # esptool verifies its own writes
        uno = self.project("uno")
        self.assertIn("--verify", backend.upload(uno, "/dev/ttyUSB0").steps[0])

    def test_missing_tool_names_a_fix(self):
        tools, settings = self.tools()
        with self.assertRaises(BackendError) as caught:
            backends.ArduinoCliBackend(tools, settings).build(self.project())
        self.assertEqual(caught.exception.fix, "install:arduino-cli")

    def test_core_requirement_uses_the_given_home(self):
        tools, settings = self.tools(**{"arduino-cli": "/x/arduino-cli"})
        backend = backends.ArduinoCliBackend(tools, settings)
        needs = backend.requirements(self.project())
        self.assertEqual(needs[0]["fix"], "install-core:esp32:esp32")
        core = os.path.join(self.home, ".arduino15", "packages", "esp32", "hardware", "esp32", "3.3.12")
        os.makedirs(core)
        open(os.path.join(core, "boards.txt"), "w").close()
        self.assertEqual(backend.requirements(self.project()), [])

    def test_platformio_commands(self):
        tools, settings = self.tools(platformio="/x/pio", cc="/usr/bin/cc")
        project = self.project(toolchain="platformio", pio_env="esp32-s3-devkitc-1")
        with open(os.path.join(project.path, "platformio.ini"), "w") as handle:
            handle.write("[env:esp32-s3-devkitc-1]\nplatform = espressif32\nboard = esp32-s3-devkitc-1\n"
                         "[env:native]\nplatform = native\n")
        backend = backends.PlatformIOBackend(tools, settings)
        self.assertEqual(backend.build(project).steps[0][:6],
                         ["/x/pio", "run", "-d", project.path, "-e", "esp32-s3-devkitc-1"])
        upload = backend.upload(project, "/dev/ttyACM0").steps[0]
        self.assertEqual(upload[-6:], ["-t", "nobuild", "-t", "upload", "--upload-port", "/dev/ttyACM0"])
        self.assertEqual(backend.test(project).steps[0][-2:], ["-e", "native"])
        rebuild = backend.build(project, clean=True)
        self.assertEqual(len(rebuild.steps), 2)

    def test_esp_idf_sets_the_target_once(self):
        idf = os.path.join(self.tmp, "esp-idf")
        tools, settings = self.tools(**{"esp-idf": idf, "esptool": "/x/esptool"})
        backend = backends.EspIdfBackend(tools, settings)
        project = self.project("esp32-c3", "esp-idf", "espidf")
        plan = backend.build(project)
        script = plan.steps[0][2]
        self.assertIn("-DIDF_TARGET=esp32c3 build", script)    # first build: one configure
        self.assertNotIn("set-target", script)
        settings.set("dev.build_jobs", 1, save=False)
        self.assertEqual(backend.build(project).env["IDF_PY_BUILD_JOBS"], "1")
        self.assertEqual(plan.env["IDF_PATH"], idf)
        with open(os.path.join(project.path, "sdkconfig"), "w") as handle:
            handle.write('CONFIG_IDF_TARGET="esp32c3"\n')
        self.assertNotIn("set-target", backend.build(project).steps[0][2])
        project.board_id = "esp32-c5"                   # the board changed: set the target
        self.assertIn("set-target esp32c5", backend.build(project).steps[0][2])
        project.board_id = "esp32-c3"
        self.assertNotIn("IDF_CCACHE_ENABLE", plan.env)  # ccache not installed
        tools._tools["ccache"] = Tool("ccache", "ccache", "/usr/bin/ccache", "4.5.1", "test")
        self.assertEqual(backend.build(project).env["IDF_CCACHE_ENABLE"], "1")
        with self.assertRaises(BackendError):
            backend.upload(project, "/dev/ttyUSB0")       # not built yet
        os.makedirs(os.path.join(project.path, "build"))
        open(os.path.join(project.path, "build", "flash_args"), "w").close()
        upload = backend.upload(project, "/dev/ttyUSB0", 460800).steps[0]
        self.assertEqual(upload[-2:], ["write-flash", "@flash_args"])

    def test_esptool_command_names_follow_its_version(self):
        tools, _ = self.tools(esptool="/x/esptool", esptool_v="4.7.0")
        self.assertEqual(backends.Esptool(tools).chip_id("/dev/ttyUSB0")[-1], "chip_id")
        tools, _ = self.tools(esptool="/x/esptool", esptool_v="5.3.1")
        self.assertEqual(backends.Esptool(tools).erase("/dev/ttyUSB0")[-1], "erase-flash")

    def test_make_cannot_upload(self):
        tools, settings = self.tools(make="/usr/bin/make")
        with self.assertRaises(BackendError):
            backends.MakeBackend(tools, settings).upload(self.project("generic-host", "make"), "/dev/x")


class InstallerTests(TempCase):
    def opener(self, files):
        class Response(io.BytesIO):
            def __init__(self, url, data):
                super().__init__(data)
                self.url = url
                self.headers = {"Content-Length": str(len(data))}

            def geturl(self):
                return self.url

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def open_(url, timeout=30):
            import urllib.error
            if url.endswith("/latest"):
                return Response("https://github.com/arduino/arduino-cli/releases/tag/v1.6.0", b"")
            if url not in files:
                raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
            return Response(url, files[url])
        return open_

    def job(self):
        manager = JobManager(os.path.join(self.tmp, "jobs"))
        self.addCleanup(manager.shutdown, 1)
        return manager

    def archive(self):
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode="w:gz") as tar:
            body = b"#!/bin/sh\necho fake\n"
            info = tarfile.TarInfo("arduino-cli")
            info.size = len(body)
            tar.addfile(info, io.BytesIO(body))
            info = tarfile.TarInfo("LICENSE.txt")
            info.size = 3
            tar.addfile(info, io.BytesIO(b"MIT"))
        return data.getvalue()

    def run_install(self, files):
        manager = self.job()
        bin_dir = os.path.join(self.tmp, "bin")
        job = manager.submit(JobSpec("install", "t", func=lambda j: installer.install_arduino_cli(
            j, bin_dir, opener=self.opener(files), machine="aarch64"), lane="install"))
        manager.wait(job.id, 20)
        return job, os.path.join(bin_dir, "arduino-cli")

    def test_verified_install(self):
        archive = self.archive()
        base = "https://github.com/arduino/arduino-cli/releases/download/v1.6.0"
        name = "arduino-cli_1.6.0_Linux_ARM64.tar.gz"
        files = {f"{base}/1.6.0-checksums.txt":
                 f"{hashlib.sha256(archive).hexdigest()}  {name}\n".encode(),
                 f"{base}/{name}": archive}
        job, path = self.run_install(files)
        self.assertTrue(job.ok, job.error)
        self.assertTrue(os.access(path, os.X_OK))
        self.assertEqual(job.result["version"], "1.6.0")
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "bin", "LICENSE.txt")))

    def test_checksum_mismatch_refused(self):
        base = "https://github.com/arduino/arduino-cli/releases/download/v1.6.0"
        name = "arduino-cli_1.6.0_Linux_ARM64.tar.gz"
        files = {f"{base}/1.6.0-checksums.txt": f"{'0' * 64}  {name}\n".encode(),
                 f"{base}/{name}": self.archive()}
        job, path = self.run_install(files)
        self.assertFalse(job.ok)
        self.assertIn("checksum mismatch", job.error)
        self.assertFalse(os.path.exists(path))

    def test_no_checksum_file_refused(self):
        job, path = self.run_install({})
        self.assertFalse(job.ok)
        self.assertFalse(os.path.exists(path))


FAKE_GIT = r"""#!/usr/bin/env python3
import os, sys
log = os.environ.get("FAKE_TOOL_LOG")
if log:
    with open(log, "a") as handle:
        handle.write("git " + " ".join(sys.argv[1:]) + "\n")
if sys.argv[1] == "ls-remote":
    for tag in ("v5.5.2", "v6.1.1", "v6.2-beta1", "v6.1"):
        print(f"0123456789abcdef0123456789abcdef01234567\trefs/tags/{tag}")
    sys.exit(0)
if sys.argv[1] == "clone":
    if os.environ.get("FAKE_GIT_FAIL"):
        os.makedirs(sys.argv[-1], exist_ok=True)
        print("fatal: the remote end hung up unexpectedly", file=sys.stderr)
        sys.exit(128)
    dest = sys.argv[-1]
    os.makedirs(os.path.join(dest, "tools", "cmake"))
    open(os.path.join(dest, "tools", "idf.py"), "w").close()
    with open(os.path.join(dest, "tools", "cmake", "version.cmake"), "w") as handle:
        handle.write("set(IDF_VERSION_MAJOR 6)\nset(IDF_VERSION_MINOR 1)\nset(IDF_VERSION_PATCH 1)\n")
    with open(os.path.join(dest, "install.sh"), "w") as handle:
        handle.write('echo "install.sh $*" >> "$FAKE_TOOL_LOG"\n'
                     'mkdir -p "$HOME/.espressif/python_env/idf6.1_py3_env"\n')
    with open(os.path.join(dest, "tools", "idf_tools.py"), "w") as handle:
        handle.write("import os, sys\nopen(os.environ['FAKE_TOOL_LOG'], 'a').write("
                     "'idf_tools ' + ' '.join(sys.argv[1:]) + '\\n')\n")
    with open(os.path.join(dest, "export.sh"), "w") as handle:
        handle.write('idf.py() { echo "ESP-IDF v6.1.1"; }\n')
    sys.exit(0)
sys.exit(1)
"""


class BuildJobsTests(unittest.TestCase):
    """Parallel compiles follow the free memory (measured on the 1 GB .130 board),
    not a fixed "1 job under 1.1 GB"."""

    def jobs(self, **machine):
        return backends.build_jobs(Settings(None), machine)

    def test_one_gigabyte_board_with_free_memory_runs_three_jobs(self):
        self.assertEqual(self.jobs(cpus=4, memory_mb=981, available_mb=709), 3)

    def test_little_free_memory_means_fewer_jobs(self):
        self.assertEqual(self.jobs(cpus=4, memory_mb=981, available_mb=480), 2)
        self.assertEqual(self.jobs(cpus=4, memory_mb=416, available_mb=250), 1)

    def test_big_machine_and_unknown_memory(self):
        self.assertEqual(self.jobs(cpus=12, memory_mb=16000, available_mb=12000), 12)
        self.assertEqual(self.jobs(cpus=4, memory_mb=512), 1)

    def test_setting_wins(self):
        settings = Settings(None)
        settings.set("dev.build_jobs", 3, save=False)
        self.assertEqual(backends.build_jobs(settings, {"cpus": 1, "available_mb": 100}), 3)


class EspIdfInstallerTests(TempCase):
    def setUp(self):
        super().setUp()
        bin_dir = os.path.join(self.tmp, "fakebin")
        write_script(os.path.join(bin_dir, "git"), FAKE_GIT)
        self.log = os.path.join(self.tmp, "tool.log")
        open(self.log, "w").close()
        from unittest import mock
        patcher = mock.patch.dict(os.environ, {
            "PATH": bin_dir + os.pathsep + os.environ.get("PATH", ""),
            "FAKE_TOOL_LOG": self.log, "HOME": self.home})
        patcher.start()
        self.addCleanup(patcher.stop)
        free = mock.patch.object(installer, "IDF_MIN_FREE", 0)
        free.start()
        self.addCleanup(free.stop)

    def install(self, **kwargs):
        manager = JobManager(os.path.join(self.tmp, "jobs"))
        self.addCleanup(manager.shutdown, 1)
        job = manager.submit(JobSpec("install", "t", func=lambda j: installer.install_esp_idf(
            j, self.home, **kwargs), lane="install"))
        manager.wait(job.id, 60)
        return job

    def calls(self):
        with open(self.log) as handle:
            return handle.read()

    def test_newest_stable_release_into_the_standard_place(self):
        job = self.install()
        self.assertTrue(job.ok, job.error)
        folder = os.path.join(self.home, "esp", "v6.1.1", "esp-idf")
        self.assertEqual(job.result, {"version": "6.1.1", "path": folder})
        calls = self.calls()
        self.assertIn("git clone --progress --branch v6.1.1 --depth 1 --recursive", calls)
        self.assertIn("install.sh all", calls)
        self.assertIn("idf_tools install cmake ninja", calls)
        self.assertFalse(os.path.exists(folder + installer.IDF_PARTIAL))
        tools = Toolchains(Settings(None), os.path.join(self.tmp, "b"), os.path.join(self.tmp, "v"),
                           home=self.home)
        tool = tools.get("esp-idf", refresh=True)
        self.assertTrue(tool.found, tool.problem)
        self.assertEqual(tool.version, "6.1.1")

    def test_failed_download_leaves_no_esp_idf_folder_and_a_retry_cleans_up(self):
        os.environ["FAKE_GIT_FAIL"] = "1"
        job = self.install()
        self.assertFalse(job.ok)
        folder = os.path.join(self.home, "esp", "v6.1.1", "esp-idf")
        self.assertFalse(os.path.exists(folder))
        self.assertTrue(os.path.exists(folder + installer.IDF_PARTIAL))
        del os.environ["FAKE_GIT_FAIL"]
        job = self.install()
        self.assertTrue(job.ok, job.error)
        self.assertFalse(os.path.exists(folder + installer.IDF_PARTIAL))

    def test_existing_checkout_only_gets_its_tools(self):
        self.assertTrue(self.install().ok)
        open(self.log, "w").close()
        job = self.install(tag="v6.1.1")
        self.assertTrue(job.ok, job.error)
        self.assertNotIn("git clone", self.calls())
        self.assertIn("install.sh all", self.calls())

    def test_a_folder_mcode_did_not_make_is_never_touched(self):
        folder = os.path.join(self.home, "esp", "v6.1.1", "esp-idf")
        os.makedirs(folder)
        mine = os.path.join(folder, "notes.txt")
        with open(mine, "w") as handle:
            handle.write("keep")
        job = self.install(tag="v6.1.1")
        self.assertFalse(job.ok)
        self.assertIn("does not change folders it did not make", job.error)
        self.assertTrue(os.path.exists(mine))

    def test_not_enough_disk_space(self):
        from unittest import mock
        with mock.patch.object(installer, "IDF_MIN_FREE", 1 << 62):
            job = self.install()
        self.assertFalse(job.ok)
        self.assertIn("free space", job.error)
        self.assertNotIn("git clone", self.calls())

    def test_release_choice_follows_python(self):
        tags = ["v5.5.2", "v6.1.1", "v6.2-beta1", "v6.1", "v4.4.8", "release/v6.1"]
        self.assertEqual(installer.pick_idf_release(tags, (3, 10, 12)), "v6.1.1")
        self.assertEqual(installer.pick_idf_release(tags, (3, 9, 2)), "v5.5.2")

    def test_mcode_python_environment_is_kept_off_the_path(self):
        venv = os.path.join(self.tmp, "venv")
        os.makedirs(os.path.join(venv, "bin"))
        open(os.path.join(venv, "pyvenv.cfg"), "w").close()
        os.environ["PATH"] = os.path.join(venv, "bin") + os.pathsep + os.environ["PATH"]
        self.assertNotIn(os.path.join(venv, "bin"),
                         installer.clean_path_env()["PATH"].split(os.pathsep))


class BuildServiceTests(TempCase):
    def setUp(self):
        super().setUp()
        self.cli = write_script(os.path.join(self.tmp, "tools", "arduino-cli"), FAKE_ARDUINO_CLI)
        self.log = os.path.join(self.tmp, "tool.log")
        os.environ["FAKE_TOOL_LOG"] = self.log
        self.addCleanup(os.environ.pop, "FAKE_TOOL_LOG", None)
        core = os.path.join(self.home, ".arduino15", "packages", "esp32", "hardware", "esp32", "3.3.12")
        os.makedirs(core)
        open(os.path.join(core, "boards.txt"), "w").close()

    def studio_with_cli(self):
        studio = self.studio(hardware=False, ai=False)
        studio.settings.set("dev.arduino_cli", self.cli, save=False)
        studio.tools.refresh(wait=True, only=["arduino-cli"])
        return studio

    def test_build_after_a_cancelled_one_starts_clean(self):
        """2026-11-27, .130 board: a cancelled ESP32-C5 build left a partial core.a and
        every later build failed with "undefined reference to millis/app_main"."""
        import json
        studio = self.studio_with_cli()
        project = studio.projects.create("Blink", "esp32-c5", "arduino-cli", init_git=False)
        os.environ["FAKE_COMPILE_SLOW"] = "20"
        self.addCleanup(os.environ.pop, "FAKE_COMPILE_SLOW", None)
        job = studio.builds.run(project, "build")
        deadline = time.time() + 10
        while not os.path.exists(os.path.join(project.path, ".mcode", "build", "core", "core.a")) \
                and time.time() < deadline:
            time.sleep(0.05)                       # the compiler is running
        studio.jobs.cancel(job.id)
        studio.jobs.wait(job.id, 10)
        self.assertEqual(job.state, "cancelled")
        marker = os.path.join(project.path, ".mcode", "build", ".mcode-interrupted")
        deadline = time.time() + 5
        while not os.path.exists(marker) and time.time() < deadline:
            time.sleep(0.02)                       # written by the job's done callback
        self.assertTrue(os.path.exists(marker))
        del os.environ["FAKE_COMPILE_SLOW"]

        def compile_args():
            with open(self.log) as handle:
                return [json.loads(line) for line in handle if '"compile"' in line][-1]
        job = studio.builds.run(project, "build")
        studio.jobs.wait(job.id, 30)
        self.assertTrue(job.ok, job.error)
        self.assertIn("--clean", compile_args())
        deadline = time.time() + 5
        while os.path.exists(marker) and time.time() < deadline:
            time.sleep(0.02)
        self.assertFalse(os.path.exists(marker))
        job = studio.builds.run(project, "build")
        studio.jobs.wait(job.id, 30)
        self.assertNotIn("--clean", compile_args())

    def test_build_records_sizes_and_artifact(self):
        studio = self.studio_with_cli()
        project = studio.projects.create("Blink", "esp32-s3", "arduino-cli", init_git=False)
        self.assertFalse(studio.builds.up_to_date(project))
        job = studio.builds.run(project, "build")
        studio.jobs.wait(job.id, 30)
        self.assertTrue(job.ok, job.error + job.output())
        self.assertEqual(job.result["report"]["size"]["flash"]["percent"], 23.0)
        self.assertTrue(job.result["artifact"].endswith(".ino.bin"))
        self.assertTrue(studio.builds.up_to_date(project))
        last = studio.builds.last(project.id)
        self.assertEqual(last["state"], "succeeded")
        self.assertEqual(studio.builds.recent(1)[0]["project_id"], project.id)
        with open(os.path.join(project.path, project.slug + ".ino"), "a") as handle:
            handle.write("\n// edit\n")
        os.utime(os.path.join(project.path, project.slug + ".ino"),
                 (os.path.getmtime(job.result["artifact"] and os.path.join(project.path, job.result["artifact"])) + 5,) * 2)
        self.assertFalse(studio.builds.up_to_date(project))

    def test_failed_build_reports_problems(self):
        studio = self.studio_with_cli()
        project = studio.projects.create("Broken", "esp32-s3", "arduino-cli", init_git=False)
        os.environ["FAKE_COMPILE_FAIL"] = "1"
        self.addCleanup(os.environ.pop, "FAKE_COMPILE_FAIL", None)
        job = studio.builds.run(project, "build")
        studio.jobs.wait(job.id, 30)
        self.assertFalse(job.ok)
        self.assertEqual(job.error, "Missing library: ServoDriver")
        last = studio.builds.last(project.id)
        self.assertEqual(last["missing_headers"], ["ServoDriver.h"])
        self.assertEqual(last["diagnostics"][0]["file"], "broken.ino")

    def test_missing_core_is_reported_with_a_fix(self):
        studio = self.studio_with_cli()
        project = studio.projects.create("Uno", "uno", "arduino-cli", init_git=False)
        with self.assertRaises(BackendError) as caught:
            studio.builds.run(project, "build")
        self.assertEqual(caught.exception.fix, "install-core:arduino:avr")

    def test_clean_removes_only_build_output(self):
        studio = self.studio_with_cli()
        project = studio.projects.create("Clean", "esp32-s3", "arduino-cli", init_git=False)
        studio.jobs.wait(studio.builds.run(project, "build").id, 30)
        job = studio.builds.run(project, "clean")
        studio.jobs.wait(job.id, 30)
        self.assertTrue(job.ok)
        self.assertFalse(os.path.exists(os.path.join(project.path, ".mcode", "build")))
        self.assertTrue(os.path.exists(os.path.join(project.path, "clean.ino")))


if __name__ == "__main__":
    unittest.main()
