"""Board settings (toolchains/boardconfig.py) and the build files they keep in
step (projects/buildfiles.py). 2026-11-27: the owner asked for an Arduino-IDE
style board configuration after an ESP32-C3 would not flash."""

from __future__ import annotations

import json
import os
import unittest

from app.projects import buildfiles
from app.projects.model import ProjectError

from .helpers import FAKE_ARDUINO_CLI, TempCase, write_script


def read(path):
    with open(path) as handle:
        return handle.read()


INI = """; my notes stay
[platformio]
default_envs = esp32-s3-devkitc-1

[env:esp32-s3-devkitc-1]
platform = espressif32
board = esp32-s3-devkitc-1   ; old board
framework = arduino
build_flags =
    -DMY_FLAG="a b"
lib_deps =
    bblanchon/ArduinoJson@^7

; host tests
[env:native]
platform = native
"""


class BuildFilesTests(TempCase):
    def project(self, toolchain="platformio", board="esp32-s3", **fields):
        from app.projects.model import Project
        os.makedirs(os.path.join(self.tmp, "p"), exist_ok=True)
        return Project(path=os.path.join(self.tmp, "p"), name="P", platform="esp32",
                       board_id=board, toolchain=toolchain, **fields)

    def ini(self, text=INI):
        path = os.path.join(self.tmp, "p", "platformio.ini")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as handle:
            handle.write(text)
        return path

    def test_platformio_board_speed_and_cdc_edit_only_their_lines(self):
        path = self.ini()
        project = self.project(board="esp32-c3", upload_speed=460800, usb_cdc_on_boot=True)
        self.assertEqual(buildfiles.sync(project), ["platformio.ini"])
        text = read(path)
        self.assertIn("board = esp32-c3-devkitm-1\n", text)
        self.assertIn("upload_speed = 460800", text)
        self.assertIn('    -DMY_FLAG="a b"\n    -DARDUINO_USB_CDC_ON_BOOT=1', text)
        self.assertIn("; my notes stay", text)
        self.assertIn("; host tests\n[env:native]\nplatform = native", text)
        self.assertEqual(text.count("board ="), 1)                 # native env untouched
        project.usb_cdc_on_boot = False
        project.upload_speed = 0
        buildfiles.sync(project)
        text = read(path)
        self.assertNotIn("ARDUINO_USB_CDC_ON_BOOT", text)
        self.assertNotIn("upload_speed", text)
        self.assertIn('build_flags =\n    -DMY_FLAG="a b"', text)
        self.assertEqual(buildfiles.sync(project), [])             # nothing left to change

    def test_cdc_flag_alone_is_removed_with_its_key(self):
        path = self.ini("[env:x]\nboard = esp32-s3-devkitc-1\nframework = arduino\n")
        project = self.project(pio_env="x", usb_cdc_on_boot=True)
        buildfiles.sync(project)
        self.assertIn("build_flags =\n    -DARDUINO_USB_CDC_ON_BOOT=1", read(path))
        project.usb_cdc_on_boot = False
        buildfiles.sync(project)
        self.assertNotIn("build_flags", read(path))

    def test_sketch_yaml_follows_the_fqbn(self):
        project = self.project("arduino-cli", "esp32-c3", usb_cdc_on_boot=True)
        path = os.path.join(project.path, "sketch.yaml")
        with open(path, "w") as handle:
            handle.write("# mine\ndefault_fqbn: esp32:esp32:esp32c5\n")
        buildfiles.sync(project)
        self.assertEqual(read(path),
                         "# mine\ndefault_fqbn: esp32:esp32:esp32c3:CDCOnBoot=cdc\n")


class FastBuildTests(TempCase):
    """ESP-IDF's trimmed build (MINIMAL_BUILD), as Espressif's examples use it."""

    def test_new_esp_idf_projects_build_only_what_main_needs(self):
        studio = self.studio(hardware=False, ai=False)
        project = studio.projects.create("Fast", "esp32-c5", "esp-idf", "espidf", init_git=False)
        text = read(os.path.join(project.path, "CMakeLists.txt"))
        self.assertLess(text.index(buildfiles.MINIMAL_BUILD), text.index("project("))
        self.assertGreater(text.index(buildfiles.MINIMAL_BUILD), text.index("project.cmake"))
        self.assertTrue(buildfiles.minimal_build(project.path))
        setting = {s.id: s for s in studio.boardconfig.settings(project)}["fast_build"]
        self.assertEqual(setting.value, "1")

    def test_switch_off_and_on_keeps_the_rest_of_the_file(self):
        path = os.path.join(self.tmp, "CMakeLists.txt")
        original = ("cmake_minimum_required(VERSION 3.16)\n"
                    "include($ENV{IDF_PATH}/tools/cmake/project.cmake)\n"
                    "# my line\nproject(mine)\n")
        with open(path, "w") as handle:
            handle.write(original)
        self.assertFalse(buildfiles.minimal_build(self.tmp))
        self.assertTrue(buildfiles.set_minimal_build(self.tmp, True))
        self.assertTrue(buildfiles.minimal_build(self.tmp))
        self.assertFalse(buildfiles.set_minimal_build(self.tmp, True))      # already on
        self.assertTrue(buildfiles.set_minimal_build(self.tmp, False))
        self.assertEqual(read(path), original)

    def test_folder_without_a_project_line_has_no_switch(self):
        with open(os.path.join(self.tmp, "CMakeLists.txt"), "w") as handle:
            handle.write("cmake_minimum_required(VERSION 3.16)\n")
        self.assertIsNone(buildfiles.minimal_build(self.tmp))


class BoardConfigTests(TempCase):
    def setUp(self):
        super().setUp()
        self.cli = write_script(os.path.join(self.tmp, "tools", "arduino-cli"), FAKE_ARDUINO_CLI)

    def studio_project(self, board="esp32-c5", toolchain="arduino-cli", framework="arduino"):
        studio = self.studio(hardware=True, ai=False)
        studio.settings.set("dev.arduino_cli", self.cli, save=False)
        studio.tools.refresh(wait=True, only=["arduino-cli"])
        return studio, studio.projects.create("Blink", board, toolchain, framework,
                                              init_git=False)

    def test_settings_come_from_the_core_without_erase(self):
        studio, project = self.studio_project()
        ids = [s.id for s in studio.boardconfig.settings(project)]
        self.assertEqual(ids, ["board", "usb_cdc_on_boot", "upload_speed", "menu:CPUFreq"])

    def test_apply_menus_cdc_speed_and_board(self):
        studio, project = self.studio_project()
        config = studio.boardconfig
        project = config.apply(project, "menu:CPUFreq", "80")
        project = config.apply(project, "usb_cdc_on_boot", "1")
        project = config.apply(project, "upload_speed", "115200")
        self.assertEqual(project.fqbn(),
                         "esp32:esp32:esp32c5:CPUFreq=80,CDCOnBoot=cdc,UploadSpeed=115200")
        project = config.apply(project, "menu:CPUFreq", "160")         # the default
        self.assertEqual(project.board_options, {})
        project = config.apply(project, "menu:CPUFreq", "80")
        project = config.apply(project, "board", "esp32-c3")
        self.assertEqual(project.board_options, {})                    # menus differ per board
        self.assertTrue(project.usb_cdc_on_boot)
        saved = json.loads(read(os.path.join(project.path, ".mcode", "project.json")))
        self.assertEqual(saved["board_id"], "esp32-c3")

    def test_values_the_tools_do_not_offer_are_refused(self):
        studio, project = self.studio_project()
        config = studio.boardconfig
        for setting, value in (("menu:CPUFreq", "999"), ("menu:EraseFlash", "all"),
                               ("upload_speed", "12345"), ("board", "not-a-board"),
                               ("usb_cdc_on_boot", "maybe")):
            with self.assertRaises(ProjectError, msg=setting):
                config.apply(project, setting, value)

    def test_platformio_offers_only_its_boards(self):
        studio, project = self.studio_project("esp32-s3", "platformio", "arduino")
        boards = [c.value for c in studio.boardconfig.settings(project)[0].choices]
        self.assertIn("esp32-c3", boards)
        self.assertNotIn("esp32-c5", boards)                           # no C5 in PlatformIO
        self.assertNotIn("esp32-c6", boards)                           # ESP-IDF only there

    def test_connected_identified_chip_is_offered_first(self):
        studio, project = self.studio_project()
        device = next(d for d in studio.devices.list() if d.short_port == "ttyACM0")
        studio.devices.remember_chip(device, {"chip": "ESP32-C3"})
        studio.devices.select(studio.devices.get(device.port), project)
        self.assertEqual(studio.boardconfig.connected_board(project).id, "esp32-c3")
        first = studio.boardconfig.settings(project)[0].choices[0]
        self.assertEqual((first.value, first.label), ("esp32-c3", "ESP32-C3 (connected)"))


if __name__ == "__main__":
    unittest.main()
