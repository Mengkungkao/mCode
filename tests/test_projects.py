"""Projects: catalogue, templates, manager, files boundary, backups."""

from __future__ import annotations

import configparser
import io
import os
import subprocess
import tarfile
import unittest

from app.core.config import Settings
from app.core.state import StateStore
from app.projects import backups, boards, files, gitutil, model, templates
from app.projects.manager import ProjectManager
from app.projects.model import Project, ProjectError

from .helpers import TempCase


class CatalogueTests(unittest.TestCase):
    def test_ids_unique_and_definitions_consistent(self):
        ids = [b.id for b in boards.BOARDS]
        self.assertEqual(len(ids), len(set(ids)))
        for board in boards.BOARDS:
            self.assertTrue(board.toolchains, board.id)
            for toolchain in board.toolchains:
                self.assertTrue(board.frameworks(toolchain), (board.id, toolchain))
            if board.platform == "esp32":
                self.assertEqual(board.flasher, "esptool")
                self.assertTrue(board.idf_target)

    def test_official_platformio_has_no_arduino_for_c6_h2(self):
        for board_id in ("esp32-c6", "esp32-h2"):
            self.assertEqual(boards.get(board_id).frameworks("platformio"), ["espidf"])

    def test_esp32_c5_offers_only_what_its_tools_define(self):
        board = boards.get("esp32-c5")
        self.assertEqual(board.fqbn, "esp32:esp32:esp32c5")
        self.assertEqual(board.idf_target, "esp32c5")
        self.assertEqual(board.toolchains, ["arduino-cli", "esp-idf"])
        self.assertIn("303a:1001", board.usb_ids)
        self.assertIs(boards.find_by_idf_target("esp32c5"), board)
        self.assertIs(boards.find_by_fqbn("esp32:esp32:esp32c5:CDCOnBoot=cdc"), board)

    def test_fqbns_match_installed_cores(self):
        """Every catalogue FQBN whose core is installed on this machine exists in it."""
        checks = boards.check_against_cores()
        missing = [c for c in checks if c.status == "missing"]
        self.assertEqual(missing, [], [f"{c.board_id}: {c.fqbn}" for c in missing])
        if not any(c.status == "ok" for c in checks):
            self.skipTest("no Arduino core installed here (nothing to compare against)")

    def test_boards_txt_reader(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "boards.txt")
            with open(path, "w") as handle:
                handle.write("uno.name=Arduino Uno\nuno.vid.0=0x2341\nuno.pid.0=0x0043\n"
                             "uno.upload_port.1.vid=0x2A03\nuno.upload_port.1.pid=0x0043\n"
                             "uno.build.mcu=atmega328p\n# comment\n")
            data = boards.read_boards_txt(path)
            self.assertEqual(data["uno"]["name"], "Arduino Uno")
            self.assertEqual(data["uno"]["mcu"], "atmega328p")
            self.assertIn("2341:0043", data["uno"]["usb"])
            self.assertIn("2a03:0043", data["uno"]["usb"])


class TemplateTests(TempCase):
    def test_every_board_and_framework_has_a_template(self):
        for board in boards.BOARDS:
            for toolchain in board.toolchains:
                for framework in board.frameworks(toolchain):
                    project = Project(path=os.path.join(self.tmp, "p"), name="P",
                                      platform=board.platform, board_id=board.id,
                                      toolchain=toolchain, framework=framework)
                    out = templates.files_for(project)
                    self.assertIn("README.md", out)
                    self.assertIn("docs/WIRING.md", out)

    def test_platformio_ini_is_valid_and_has_a_native_test_env(self):
        project = Project(path=os.path.join(self.tmp, "robot"), name="Robot", platform="esp32",
                          board_id="esp32-s3", toolchain="platformio", framework="arduino",
                          pio_env="esp32-s3-devkitc-1", usb_cdc_on_boot=True, libraries=["x/y@^1"])
        ini = templates.files_for(project)["platformio.ini"]
        parser = configparser.ConfigParser(interpolation=None)
        parser.read_string(ini)
        self.assertEqual(parser.get("env:esp32-s3-devkitc-1", "board"), "esp32-s3-devkitc-1")
        self.assertIn("ARDUINO_USB_CDC_ON_BOOT=1", parser.get("env:esp32-s3-devkitc-1", "build_flags"))
        self.assertEqual(parser.get("env:native", "platform"), "native")

    def test_starter_firmware_assumes_no_pins(self):
        project = Project(path=os.path.join(self.tmp, "a"), name="A", platform="arduino",
                          board_id="uno", toolchain="arduino-cli")
        sketch = templates.files_for(project)["a.ino"]
        self.assertIn("#ifdef LED_BUILTIN", sketch)
        self.assertNotRegex(sketch, r"pinMode\(\d")


class ManagerTests(TempCase):
    def manager(self):
        settings = Settings(None)
        settings.set("dev.projects_dir", os.path.join(self.tmp, "projects"), save=False)
        events = []
        return ProjectManager(settings, StateStore(os.path.join(self.tmp, "state.json")),
                              os.path.join(self.tmp, "backups"),
                              publish=lambda t, d: events.append((t, d))), events

    def test_create_each_toolchain_and_reload(self):
        manager, events = self.manager()
        for board_id, toolchain, framework in (("esp32-s3", "platformio", "arduino"),
                                               ("esp32-c6", "platformio", "espidf"),
                                               ("esp32-c3", "esp-idf", ""),
                                               ("uno", "arduino-cli", ""),
                                               ("generic-host", "make", "")):
            project = manager.create(f"T {board_id} {toolchain}", board_id, toolchain, framework,
                                     init_git=False)
            again = model.load(project.path)
            self.assertEqual(again.id, project.id)
            self.assertEqual(again.board_id, board_id)
            self.assertFalse(again.detected)
        self.assertEqual(len(manager.list()), 5)
        self.assertEqual(manager.current().board_id, "generic-host")
        self.assertTrue(any(t == "projects" for t, _ in events))

    def test_create_refuses_bad_input(self):
        manager, _ = self.manager()
        with self.assertRaises(ProjectError):
            manager.create("", "uno")
        with self.assertRaises(ProjectError):
            manager.create("x/y", "uno")
        with self.assertRaises(ProjectError):
            manager.create("X", "no-such-board")
        with self.assertRaises(ProjectError):
            manager.create("X", "esp32-c6", "platformio", "arduino")
        manager.create("Same", "uno", init_git=False)
        with self.assertRaises(ProjectError):
            manager.create("Same", "uno", init_git=False)

    def test_detects_projects_made_elsewhere(self):
        root = os.path.join(self.tmp, "elsewhere")
        pio = os.path.join(root, "pio")
        os.makedirs(pio)
        with open(os.path.join(pio, "platformio.ini"), "w") as handle:
            handle.write("[env:native]\nplatform = native\n[env:c3]\nplatform = espressif32\n"
                         "board = esp32-c3-devkitm-1\nframework = arduino\n")
        found = model.load(pio)
        self.assertTrue(found.detected)
        self.assertEqual((found.board_id, found.pio_env), ("esp32-c3", "c3"))
        idf = os.path.join(root, "idf")
        os.makedirs(idf)
        with open(os.path.join(idf, "CMakeLists.txt"), "w") as handle:
            handle.write("include($ENV{IDF_PATH}/tools/cmake/project.cmake)\n")
        with open(os.path.join(idf, "sdkconfig"), "w") as handle:
            handle.write('CONFIG_IDF_TARGET="esp32s3"\n')
        self.assertEqual(model.load(idf).board_id, "esp32-s3")
        sketch = os.path.join(root, "blink")
        os.makedirs(sketch)
        open(os.path.join(sketch, "blink.ino"), "w").close()
        with open(os.path.join(sketch, "sketch.yaml"), "w") as handle:
            handle.write("default_fqbn: arduino:avr:mega:cpu=atmega2560\n")
        self.assertEqual(model.load(sketch).board_id, "mega2560")
        empty = os.path.join(root, "empty")
        os.makedirs(empty)
        with self.assertRaises(ProjectError):
            model.load(empty)

    def test_rename_duplicate_delete(self):
        manager, _ = self.manager()
        project = manager.create("Old Name", "uno", init_git=False)
        renamed = manager.rename(project, "New Name")
        self.assertTrue(os.path.exists(os.path.join(renamed.path, "new-name.ino")))
        self.assertEqual(manager.current().name, "New Name")
        self.assertTrue(manager.backups(renamed))
        copy = manager.duplicate(renamed)
        self.assertNotEqual(copy.id, renamed.id)
        self.assertTrue(os.path.exists(os.path.join(copy.path, os.path.basename(copy.path) + ".ino")))
        trash = manager.delete(copy)
        self.assertTrue(os.path.isdir(trash))
        self.assertFalse(os.path.exists(copy.path))
        self.assertNotIn(copy.id, [p.id for p in manager.list()])

    def test_search(self):
        manager, _ = self.manager()
        manager.create("Garden Pump", "esp32-devkit", init_git=False)
        manager.create("Robot Arm", "uno", init_git=False)
        self.assertEqual([p.name for p in manager.search("uno")], ["Robot Arm"])
        self.assertEqual([p.name for p in manager.search("garden esp32")], ["Garden Pump"])

    def test_backup_and_restore_keeps_build_output(self):
        manager, _ = self.manager()
        project = manager.create("Keep", "generic-host", "make", init_git=False)
        main = os.path.join(project.path, "src", "main.c")
        os.makedirs(os.path.join(project.path, "build"))
        open(os.path.join(project.path, "build", "app"), "w").close()
        name = os.path.basename(manager.backup(project))
        with open(main, "w") as handle:
            handle.write("broken")
        manager.restore(project, name)
        with open(main) as handle:
            self.assertIn("logic_add", handle.read())
        self.assertTrue(os.path.exists(os.path.join(project.path, "build", "app")))
        reasons = [b["reason"] for b in manager.backups(project)]
        self.assertIn("before-restore", reasons)

    def test_restore_refuses_unsafe_archives(self):
        archive = os.path.join(self.tmp, "evil.tar.gz")
        with tarfile.open(archive, "w:gz") as tar:
            info = tarfile.TarInfo("../outside.txt")
            info.size = 1
            tar.addfile(info, io.BytesIO(b"x"))
        with self.assertRaises(ProjectError):
            backups.safe_extract(archive, os.path.join(self.tmp, "dest"))
        link = os.path.join(self.tmp, "link.tar.gz")
        with tarfile.open(link, "w:gz") as tar:
            info = tarfile.TarInfo("evil")
            info.type = tarfile.SYMTYPE
            info.linkname = "/etc/passwd"
            tar.addfile(info)
        with self.assertRaises(ProjectError):
            backups.safe_extract(link, os.path.join(self.tmp, "dest2"))


@unittest.skipUnless(gitutil.git_path(), "git is not installed")
class GitSafetyTests(TempCase):
    """A-01: mCode's own git calls ran programs named in a project's .git/config."""

    def repo(self):
        path = os.path.join(self.tmp, "repo")
        os.makedirs(path)
        with open(os.path.join(path, "Makefile"), "w") as handle:
            handle.write("all:\n")
        with open(os.path.join(path, ".gitattributes"), "w") as handle:
            handle.write("* filter=evil diff=evil\n")
        self.assertTrue(gitutil.init(path))
        self.markers = {name: os.path.join(self.tmp, name) for name in
                        ("fsmonitor", "filter", "process", "textconv", "external", "gpg", "hook")}
        m = self.markers
        settings = {"core.fsmonitor": f"touch {m['fsmonitor']}; false",
                    "filter.evil.clean": f"sh -c 'touch {m['filter']}; cat'",
                    "filter.evil.process": f"touch {m['process']}",
                    "diff.evil.textconv": f"sh -c 'touch {m['textconv']}; cat'",
                    "diff.external": f"touch {m['external']}",
                    "gpg.program": f"touch {m['gpg']}", "commit.gpgSign": "true"}
        for key, value in settings.items():
            subprocess.run(["git", "-c", "core.fsmonitor=false", "config", key, value], cwd=path,
                           check=True)
        hook = os.path.join(path, ".git", "hooks", "pre-commit")
        with open(hook, "w") as handle:
            handle.write(f"#!/bin/sh\ntouch {m['hook']}\n")
        os.chmod(hook, 0o755)
        with open(os.path.join(path, "Makefile"), "a") as handle:
            handle.write("\t@echo changed\n")
        return path

    def ran(self):
        return sorted(name for name, path in self.markers.items() if os.path.exists(path))

    def test_viewing_a_repository_runs_none_of_its_programs(self):
        path = self.repo()
        status = gitutil.status(path)
        self.assertEqual([c["path"] for c in status["changes"]], ["Makefile"])
        self.assertIn("changed", gitutil.diff(path))
        self.assertTrue(gitutil.log(path))
        self.assertIn("Project created", gitutil.show(path, "HEAD"))
        self.assertEqual(self.ran(), [])

    def test_commits_for_the_ai_run_no_hooks_or_signing_programs(self):
        path = self.repo()
        self.assertIn("Committed", gitutil.commit(path, "AI change", hooks=False))
        self.assertEqual(self.ran(), [])

    def test_show_refuses_options_as_revisions(self):
        path = self.repo()
        with self.assertRaises(ValueError):
            gitutil.show(path, "--output=/tmp/x")


class FileBoundaryTests(TempCase):
    def setUp(self):
        super().setUp()
        self.root = os.path.join(self.tmp, "proj")
        os.makedirs(os.path.join(self.root, "src"))
        with open(os.path.join(self.root, "src", "main.cpp"), "w") as handle:
            handle.write("int main() {}\n")

    def test_paths_outside_are_refused(self):
        for bad in ("../x", "/etc/passwd", "src/../../x", "a\0b"):
            with self.assertRaises(ProjectError, msg=bad):
                files.resolve(self.root, bad)
        os.symlink("/etc", os.path.join(self.root, "escape"))
        with self.assertRaises(ProjectError):
            files.resolve(self.root, "escape/passwd")

    def test_read_write_and_conflict(self):
        data = files.read_text(self.root, "src/main.cpp")
        self.assertEqual(data["language"], "cpp")
        saved = files.write_text(self.root, "src/main.cpp", "int main() { return 0; }\n",
                                 expect_sha=data["sha"])
        with self.assertRaises(ProjectError):
            files.write_text(self.root, "src/main.cpp", "late", expect_sha=data["sha"])
        self.assertNotEqual(saved["sha"], data["sha"])

    def test_concurrent_saves_do_not_collide(self):
        import threading
        data = files.read_text(self.root, "src/main.cpp")
        errors = []

        def save(text):
            try:
                files.write_text(self.root, "src/main.cpp", text, expect_sha=data["sha"])
            except Exception as exc:        # a conflict is allowed, a crash is not
                errors.append(exc)

        threads = [threading.Thread(target=save, args=("same text\n",)) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(open(os.path.join(self.root, "src", "main.cpp")).read(), "same text\n")
        leftovers = [n for n in os.listdir(os.path.join(self.root, "src")) if "mcode-save" in n]
        self.assertEqual(leftovers, [])

    def test_walk_skips_build_output_and_tree(self):
        os.makedirs(os.path.join(self.root, ".pio", "build"))
        open(os.path.join(self.root, ".pio", "build", "firmware.bin"), "w").close()
        os.makedirs(os.path.join(self.root, ".mcode", "build"))
        open(os.path.join(self.root, ".mcode", "build", "x.bin"), "w").close()
        self.assertEqual(list(files.walk_sources(self.root)), ["src/main.cpp"])
        tree = files.tree(self.root)
        self.assertEqual([e["path"] for e in tree], ["src", "src/main.cpp"])

    def test_remove_goes_to_trash(self):
        trash = os.path.join(self.tmp, "trash")
        target = files.remove(self.root, "src/main.cpp", trash)
        self.assertTrue(os.path.exists(target))
        with self.assertRaises(ProjectError):
            files.remove(self.root, ".", trash)


if __name__ == "__main__":
    unittest.main()
