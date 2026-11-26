"""Hardware: USB discovery and identification, serial port (a real PTY),
serial monitor, port ownership, upload safety checks and upload flow."""

from __future__ import annotations

import os
import pty
import time
import tty
import unittest

from app.hardware import usbids
from app.hardware.devices import DeviceManager, _relevant
from app.hardware.monitor import SerialMonitor
from app.hardware.ports import PortBusy, PortCoordinator
from app.hardware.serialport import SerialError, SerialPort
from app.hardware.upload import match_device
from app.preview import make_sysfs
from app.projects.model import Project, ProjectError

from .helpers import FAKE_ARDUINO_CLI, TempCase, write_script


def wait_for(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


class DiscoveryTests(TempCase):
    def manager(self):
        sysfs = make_sysfs(os.path.join(self.tmp, "sys"))
        dev = os.path.join(self.tmp, "dev")
        os.makedirs(dev, exist_ok=True)
        for name in ("ttyUSB0", "ttyACM0"):
            open(os.path.join(dev, name), "w").close()
        from app.core.state import StateStore
        manager = DeviceManager(StateStore(os.path.join(self.tmp, "state.json")), sysfs=sysfs,
                                devroot=dev, proc=os.path.join(self.tmp, "noproc"),
                                core_ids=lambda: {"303a:1001": ["ESP32 Family Device"]})
        return manager, dev

    def test_identification_from_usb_ids_and_drivers(self):
        manager, dev = self.manager()
        devices = {d.short_port: d for d in manager.scan()}
        bridge = devices["ttyUSB0"]
        self.assertEqual((bridge.kind, bridge.bridge, bridge.driver), ("bridge", "CP210x", "cp210x"))
        self.assertEqual(bridge.board_id, "")
        self.assertIn("unknown", bridge.describe())
        native = devices["ttyACM0"]
        self.assertEqual((native.kind, native.family, native.driver), ("native-esp", "esp32", "cdc_acm"))
        self.assertEqual(native.serial, "DC:DA:0C:11:22:33")
        self.assertEqual(native.board_names, [])          # 303a:1001 is shared: no guess

    def test_not_identified_by_port_name(self):
        """A ttyACM device with an unknown ID stays unknown, whatever its name."""
        from app.hardware.devices import Device
        manager, _ = self.manager()
        device = Device(port="/dev/ttyACM9", vid="1234", pid="5678", driver="cdc_acm")
        manager._identify(device)
        self.assertEqual((device.kind, device.board_id, device.family), ("cdc", "", ""))

    def test_permission_problem_is_explained(self):
        manager, dev = self.manager()
        os.chmod(os.path.join(dev, "ttyUSB0"), 0o000)
        self.addCleanup(os.chmod, os.path.join(dev, "ttyUSB0"), 0o644)
        if os.access(os.path.join(dev, "ttyUSB0"), os.R_OK):
            self.skipTest("running as root")
        device = {d.short_port: d for d in manager.scan()}["ttyUSB0"]
        self.assertFalse(device.readable)
        self.assertIn("dialout", device.problem)

    def test_selection_and_chip_memory_persist(self):
        manager, _ = self.manager()
        devices = manager.scan()
        project = Project(path="/x", name="X", platform="esp32", board_id="esp32-s3",
                          toolchain="arduino-cli")
        self.assertIsNone(manager.selected(project))           # two boards: no guess
        manager.select(devices[0], project)
        self.assertEqual(manager.selected(project).port, devices[0].port)
        manager.remember_chip(devices[0], {"chip": "ESP32-S3", "mac": "x"})
        again, _ = self.manager()
        device = {d.port: d for d in again.scan()}[devices[0].port]
        self.assertEqual(device.chip, "ESP32-S3")
        self.assertIn("esptool", device.describe())

    def test_hotplug_relevance(self):
        self.assertTrue(_relevant(b"add@/devices/x/tty/ttyUSB0\0ACTION=add\0SUBSYSTEM=tty\0"))
        self.assertFalse(_relevant(b"change@/devices/x/power_supply\0SUBSYSTEM=power_supply\0"))

    def test_chip_family(self):
        self.assertEqual(usbids.chip_family("ESP32-S3 (QFN56)"), "esp32s3")
        self.assertEqual(usbids.chip_family("ESP32-C61"), "esp32c61")
        self.assertEqual(usbids.chip_family("ESP32-D0WD-V3"), "esp32")


class NoBoardHelpTests(TempCase):
    """Why the board list is empty, worded from what the kernel shows in sysfs
    (2026-11-27: an Orange Pi Zero 2W whose OTG port is a gadget-only port)."""

    def sysfs(self):
        root = os.path.join(self.tmp, "sys")
        os.makedirs(os.path.join(root, "bus", "usb", "devices"))
        os.makedirs(os.path.join(root, "class", "tty"))
        return root

    def hub(self, root, name="usb1"):
        path = os.path.join(root, "devices", "platform", name)
        os.makedirs(path)
        with open(os.path.join(path, "bDeviceClass"), "w") as handle:
            handle.write("09\n")
        os.symlink(path, os.path.join(root, "bus", "usb", "devices", name))

    def usb(self, root, name, vid, pid, product, iface_class, tty=""):
        path = os.path.join(root, "devices", "platform", "usb1", name)
        iface = os.path.join(path, f"{name}:1.0")
        os.makedirs(iface)
        for key, value in (("idVendor", vid), ("idProduct", pid), ("product", product),
                           ("bDeviceClass", "00")):
            with open(os.path.join(path, key), "w") as handle:
                handle.write(value + "\n")
        with open(os.path.join(iface, "bInterfaceClass"), "w") as handle:
            handle.write(iface_class + "\n")
        os.symlink(path, os.path.join(root, "bus", "usb", "devices", name))
        os.symlink(iface, os.path.join(root, "bus", "usb", "devices", f"{name}:1.0"))
        if tty:
            os.makedirs(os.path.join(root, "class", "tty", tty))
            os.symlink(iface, os.path.join(root, "class", "tty", tty, "device"))

    def manager(self, root):
        from app.core.state import StateStore
        return DeviceManager(StateStore(os.path.join(self.tmp, "state.json")), sysfs=root,
                             devroot=os.path.join(self.tmp, "dev"),
                             proc=os.path.join(self.tmp, "noproc"), core_ids=lambda: {})

    def test_nothing_on_usb_and_a_gadget_only_port(self):
        root = self.sysfs()
        self.hub(root)
        os.makedirs(os.path.join(root, "class", "udc", "musb-hdrc.5.auto"))
        self.usb(root, "1-1", "046d", "c31c", "USB Keyboard", "03")     # not a board
        short, detail = self.manager(root).no_board_help()
        self.assertEqual(short, "Nothing on USB")
        self.assertIn("data cable", detail)
        self.assertIn("musb-hdrc.5.auto", detail)
        self.assertNotIn("Keyboard", detail)
        self.assertNotIn("no USB host port", detail)

    def test_device_without_a_serial_port_is_named(self):
        root = self.sysfs()
        self.hub(root)
        self.usb(root, "1-1", "303a", "1001", "USB JTAG/serial debug unit", "02")
        short, detail = self.manager(root).no_board_help()
        self.assertIn("no serial port", short)
        self.assertIn("303a:1001", detail)

    def test_board_with_a_serial_port_is_not_reported_as_silent(self):
        root = self.sysfs()
        self.hub(root)
        self.usb(root, "1-1", "303a", "1001", "USB JTAG/serial debug unit", "02", tty="ttyACM0")
        summary = self.manager(root).usb_summary()
        self.assertEqual(summary.host_ports, 1)
        self.assertEqual([(d.usb_id, d.serial_port) for d in summary.devices],
                         [("303a:1001", True)])

    def test_no_host_port(self):
        short, detail = self.manager(self.sysfs()).no_board_help()
        self.assertIn("no USB host port", detail)


class MatchTests(unittest.TestCase):
    def device(self, **kw):
        from app.hardware.devices import Device
        return Device(port="/dev/ttyX", **kw)

    def project(self, board):
        return Project(path="/x", name="X", platform="esp32", board_id=board, toolchain="arduino-cli")

    def test_mismatches_stop_and_unknowns_warn(self):
        uno = self.device(vid="2341", pid="0043", board_id="uno", kind="arduino", family="avr")
        self.assertFalse(match_device(self.project("esp32-s3"), uno).ok)
        self.assertEqual(match_device(self.project("esp32-s3"), uno).level, "error")
        self.assertTrue(match_device(self.project("uno"), uno).ok)
        chip = self.device(chip="ESP32-C3", kind="bridge", bridge="CH340")
        self.assertEqual(match_device(self.project("esp32-s3"), chip).level, "error")
        self.assertTrue(match_device(self.project("esp32-c3"), chip).ok)
        bridge = self.device(kind="bridge", bridge="CP210x")
        check = match_device(self.project("uno"), bridge)
        self.assertEqual((check.ok, check.level), (False, "warning"))
        native = self.device(kind="native-esp", family="esp32")
        self.assertEqual(match_device(self.project("uno"), native).level, "error")
        self.assertEqual(match_device(self.project("esp32-devkit"), native).level, "error")
        self.assertEqual(match_device(self.project("esp32-s3"), native).level, "warning")


class SerialTests(TempCase):
    def pty_pair(self):
        master, slave = pty.openpty()
        tty.setraw(master)
        path = os.ttyname(slave)
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        return master, path

    def test_port_reads_and_writes(self):
        master, path = self.pty_pair()
        port = SerialPort(path, 115200).open()
        self.addCleanup(port.close)
        os.write(master, b"hello\n")
        self.assertTrue(wait_for(lambda: True))
        self.assertEqual(port.read(1.0), b"hello\n")
        port.write(b"ping\r\n")
        self.assertEqual(os.read(master, 100), b"ping\r\n")
        with self.assertRaises(SerialError) as caught:          # TIOCEXCL: one owner at a time
            SerialPort(path, 9600).open()
        self.assertIn("in use", str(caught.exception))
        port.close()
        SerialPort(path, 74880).open().close()                 # non-standard rate (termios2)

    def test_missing_port(self):
        with self.assertRaises(SerialError):
            SerialPort(os.path.join(self.tmp, "nothing"), 9600).open()

    def test_monitor_lines_hex_search_save_send(self):
        master, path = self.pty_pair()
        events = []
        ports = PortCoordinator()
        monitor = SerialMonitor(ports, os.path.join(self.tmp, "logs"),
                                publish=lambda t, d: events.append((t, d)))
        self.addCleanup(monitor.close_all)
        monitor.connect(path, 115200)
        os.write(master, b"boot ok\r\nheartbeat 1\nparti")
        self.assertTrue(wait_for(lambda: any(l.text == "parti" for l in monitor.lines)))
        texts = [l.text for l in monitor.lines if l.direction == "rx"]
        self.assertEqual(texts, ["boot ok", "heartbeat 1", "parti"])
        self.assertTrue(any(t == "monitor" for t, _ in events))
        self.assertIn("62 6f 6f 74", monitor.hexdump()[0])
        self.assertEqual(len(monitor.search("HEART")), 1)
        monitor.send("led on", "crlf")
        self.assertEqual(os.read(master, 100), b"led on\r\n")
        monitor.send("0a ff", hex_mode=True)
        self.assertEqual(os.read(master, 100), b"\n\xff")
        saved = monitor.save()
        with open(saved) as handle:
            self.assertIn("heartbeat 1", handle.read())
        with self.assertRaises(PortBusy):
            ports.acquire(path, "an upload")
        monitor.set_paused(True)
        self.assertTrue(monitor.status()["paused"])

    def test_suspend_and_resume_for_upload(self):
        master, path = self.pty_pair()
        ports = PortCoordinator()
        monitor = SerialMonitor(ports, os.path.join(self.tmp, "logs"))
        self.addCleanup(monitor.close_all)
        monitor.connect(path, 9600)
        self.assertTrue(monitor.suspend(path))
        self.assertFalse(monitor.connected)
        self.assertIsNone(ports.owner(path))
        ports.acquire(path, "an upload")
        ports.release(path, "an upload")
        self.assertFalse(monitor.resume(present=lambda p: False))   # board not back yet
        self.assertTrue(monitor.suspended)
        monitor.device_added(path)                                   # hot-plug brings it back
        self.assertTrue(monitor.connected)


class UploadTests(TempCase):
    def setUp(self):
        super().setUp()
        self.cli = write_script(os.path.join(self.tmp, "tools", "arduino-cli"), FAKE_ARDUINO_CLI)
        core = os.path.join(self.home, ".arduino15", "packages", "esp32", "hardware", "esp32", "3.3.12")
        os.makedirs(core)
        open(os.path.join(core, "boards.txt"), "w").close()

    def ready(self):
        studio = self.studio(hardware=True, ai=False)
        studio.settings.set("dev.arduino_cli", self.cli, save=False)
        studio.tools.refresh(wait=True, only=["arduino-cli"])
        project = studio.projects.create("Robot", "esp32-s3", "arduino-cli", init_git=False)
        return studio, project

    def test_no_firmware_no_device(self):
        studio, project = self.ready()
        plan = studio.uploads.prepare(project, "")
        self.assertFalse(plan.ok)
        failed = {c.id for c in plan.checks if not c.ok and c.level == "error"}
        self.assertIn("firmware", failed)
        self.assertIn("device", failed)
        self.assertEqual(plan.to_dict()["token"], "")

    def test_mismatch_blocks_and_bridge_warns(self):
        studio, project = self.ready()
        studio.jobs.wait(studio.builds.run(project, "build").id, 30)
        bridge = studio.uploads.prepare(project, os.path.join(self.tmp, "dev", "ttyUSB0"))
        self.assertTrue(bridge.ok, bridge.problem())
        self.assertTrue(bridge.warnings)
        uno = studio.projects.create("Uno", "esp32-devkit", "arduino-cli", init_git=False)
        studio.jobs.wait(studio.builds.run(uno, "build").id, 30)
        native = studio.uploads.prepare(uno, os.path.join(self.tmp, "dev", "ttyACM0"))
        self.assertFalse(native.ok)
        self.assertIn("no USB of its own", native.problem())

    def test_confirmation_token_is_required_and_single_use(self):
        studio, project = self.ready()
        studio.jobs.wait(studio.builds.run(project, "build").id, 30)
        port = os.path.join(self.tmp, "dev", "ttyACM0")
        plan = studio.uploads.prepare(project, port)
        self.assertTrue(plan.ok, plan.problem())
        with self.assertRaises(ProjectError):
            studio.uploads.start(plan.token, confirmed=False)
        with self.assertRaises(ProjectError):
            studio.uploads.start("not-a-token", confirmed=True)
        job = studio.uploads.start(plan.token, confirmed=True)
        studio.jobs.wait(job.id, 30)
        self.assertTrue(job.ok, job.error + job.output())
        self.assertTrue(job.result["verified"])
        self.assertEqual(job.progress, 1.0)
        with self.assertRaises(ProjectError):
            studio.uploads.start(plan.token, confirmed=True)       # used once
        history = studio.uploads.history()
        self.assertEqual(history[0]["state"], "succeeded")

    def test_source_changed_after_confirming(self):
        studio, project = self.ready()
        studio.jobs.wait(studio.builds.run(project, "build").id, 30)
        plan = studio.uploads.prepare(project, os.path.join(self.tmp, "dev", "ttyACM0"))
        sketch = os.path.join(project.path, "robot.ino")
        with open(sketch, "a") as handle:
            handle.write("// newer\n")
        future = time.time() + 30
        os.utime(sketch, (future, future))
        with self.assertRaises(ProjectError) as caught:
            studio.uploads.start(plan.token, confirmed=True)
        self.assertIn("changed", str(caught.exception))

    def test_failed_upload_is_explained(self):
        studio, project = self.ready()
        studio.jobs.wait(studio.builds.run(project, "build").id, 30)
        os.environ["FAKE_UPLOAD_EXIT"] = "2"
        self.addCleanup(os.environ.pop, "FAKE_UPLOAD_EXIT", None)
        plan = studio.uploads.prepare(project, os.path.join(self.tmp, "dev", "ttyACM0"))
        job = studio.uploads.start(plan.token, confirmed=True)
        studio.jobs.wait(job.id, 30)
        self.assertFalse(job.ok)
        self.assertEqual(studio.uploads.history()[0]["state"], "failed")


if __name__ == "__main__":
    unittest.main()
