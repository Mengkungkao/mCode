"""``run.sh --diagnose``: what mCode finds on this device, in plain words.

Read-only: it detects tools, asks Claude Code for its login status (no
request is sent to Claude), lists USB serial devices and audio devices, and
prints the paths mCode uses. Nothing is installed, flashed or changed.
"""

from __future__ import annotations

import os

from . import APP_NAME, VERSION
from .projects import boards
from .toolchains import detect


def main() -> int:
    from .studio import Studio
    studio = Studio()
    try:
        studio.tools.refresh(wait=True)
        info = detect.machine()
        print(f"{APP_NAME} {VERSION}")
        print(f"device: {info.get('model') or info.get('arch')} · {info.get('memory_mb')} MB · "
              f"{info.get('cpus')} CPU(s) · Python {info.get('python')}")
        print(f"data folder: {studio.paths.data}")
        print(f"projects:    {studio.projects.root}")
        print("\ntools:")
        for tool in studio.tools.all():
            if tool.found:
                print(f"  ok       {tool.name:22} {tool.version:10} {tool.source}: {tool.path}")
            else:
                print(f"  missing  {tool.name:22} {tool.problem}. {tool.hint}")
        print("\nboard catalogue vs installed Arduino cores:")
        for check in boards.check_against_cores():
            print(f"  {check.status:20} {check.board_id:12} {check.fqbn}")
        print("\nClaude Code:")
        if studio.ai is not None:
            status = studio.ai.refresh_status(wait=True)
            auth = status["auth"]
            print(f"  {status['state']}: {auth.get('label')}"
                  + (f" ({auth.get('note')})" if auth.get("note") else ""))
            if status.get("memory_warning"):
                print(f"  warning: {status['memory_warning']}")
        print("\nUSB serial devices:")
        devices = studio.devices.scan() if studio.devices else []
        for device in devices:
            print(f"  {device.port:14} {device.vid}:{device.pid} {device.describe()}"
                  + (f"  [{device.problem}]" if device.problem else ""))
        if not devices:
            print("  none")
        if studio.voice is not None:
            voice = studio.voice.status()
            print("\nvoice:")
            print(f"  microphone: {voice['microphone'] or 'none'} ({voice['microphone_info']})")
            print(f"  speaker:    {voice['speaker'] or 'none'} ({voice['speaker_info']})")
            print(f"  speech recognition: {voice['stt']}; spoken feedback: {voice['tts']}")
            print(f"  keys in {studio.paths.env_file}: {voice['keys']}")
        groups = []
        try:
            groups = [g for g in os.getgroups()]
            import grp
            names = {grp.getgrgid(g).gr_name for g in groups}
            print("\npermissions: " + ("in the dialout group (serial ports OK)" if "dialout" in names
                                      else "NOT in the dialout group: sudo usermod -aG dialout $USER"))
        except (ImportError, KeyError, OSError):
            pass
    finally:
        studio.stop(timeout=1.0)
    return 0
