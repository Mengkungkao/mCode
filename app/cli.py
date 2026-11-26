"""mCode from a terminal: the same services as the LCD and the desktop IDE.

    ./run.sh cli projects
    ./run.sh cli new "ESP32 Robot" --board esp32-s3 --tool platformio
    ./run.sh cli boards
    ./run.sh cli build [PROJECT] [--action build|rebuild|clean|test|check]
    ./run.sh cli devices
    ./run.sh cli upload [PROJECT] --port /dev/ttyACM0      (asks before writing)
    ./run.sh cli identify --port /dev/ttyUSB0              (esptool chip detection)
    ./run.sh cli monitor --port /dev/ttyUSB0 [--baud 115200]
    ./run.sh cli tools [--install arduino-cli|platformio|esptool|claude] [--core esp32:esp32]
    ./run.sh cli claude [status|login|logout]
    ./run.sh cli ai "Add a heartbeat LED" [--project P] [--mode ask|assisted|advanced]
    ./run.sh cli ai --approve | --reject | --undo [--project P]

PROJECT is a project id, folder name or path; the current project by default.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time

from .projects import boards


def _studio(hardware=True, ai=True):
    from .studio import Studio
    studio = Studio(hardware=hardware, ai=ai, voice=False)
    return studio


def _follow(studio, job) -> int:
    """Print a job's output as it arrives; returns 0 when it succeeded."""
    printed = 0
    while True:
        done = job.done_event.wait(0.25)
        lines = list(job.lines)
        if len(lines) < printed:
            printed = 0
        for line in lines[printed:]:
            print(line)
        printed = len(lines)
        if done:
            break
    print(f"-- {job.title}: {job.state}" + (f" ({job.error})" if job.error and not job.ok else ""))
    return 0 if job.ok else 1


def main(argv) -> int:
    parser = argparse.ArgumentParser(prog="mcode cli", description="mCode Studio from a terminal")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("projects")
    new = sub.add_parser("new")
    new.add_argument("name")
    new.add_argument("--board", required=True)
    new.add_argument("--tool", default="")
    new.add_argument("--framework", default="")
    sub.add_parser("boards")
    build = sub.add_parser("build")
    build.add_argument("project", nargs="?")
    build.add_argument("--action", default="build", choices=["build", "rebuild", "clean", "test",
                                                             "check"])
    sub.add_parser("devices")
    upload = sub.add_parser("upload")
    upload.add_argument("project", nargs="?")
    upload.add_argument("--port", default="")
    upload.add_argument("--yes", action="store_true", help="confirm without asking (scripts)")
    ident = sub.add_parser("identify")
    ident.add_argument("--port", required=True)
    mon = sub.add_parser("monitor")
    mon.add_argument("--port", required=True)
    mon.add_argument("--baud", type=int, default=115200)
    tools = sub.add_parser("tools")
    tools.add_argument("--install", default="")
    tools.add_argument("--core", default="")
    claude = sub.add_parser("claude")
    claude.add_argument("action", nargs="?", default="status", choices=["status", "login", "logout"])
    ai = sub.add_parser("ai")
    ai.add_argument("text", nargs="?", default="")
    ai.add_argument("--project", default="")
    ai.add_argument("--mode", default="")
    ai.add_argument("--approve", action="store_true")
    ai.add_argument("--reject", action="store_true")
    ai.add_argument("--undo", action="store_true")
    args = parser.parse_args(argv)

    if args.command == "boards":
        for board in boards.BOARDS:
            print(f"{board.id:14} {board.name:32} {board.mcu:12} {', '.join(board.toolchains)}")
        return 0
    studio = _studio(hardware=args.command in ("devices", "upload", "identify", "monitor"),
                     ai=args.command in ("ai", "claude"))
    try:
        return _run(studio, args)
    except Exception as exc:        # a user-facing message, not a traceback
        print(f"error: {exc}", file=sys.stderr)
        fix = getattr(exc, "fix", "")
        if fix:
            print(f"fix: {fix}", file=sys.stderr)
        return 1
    finally:
        studio.stop(timeout=1.0)


def _run(studio, args) -> int:
    command = args.command
    if command == "projects":
        current = studio.projects.current()
        for project in studio.projects.list():
            mark = "*" if current and current.id == project.id else " "
            print(f"{mark} {project.id}  {project.name:28} {project.board_name:26} {project.toolchain}")
        print(f"(folder: {studio.projects.root})")
        return 0
    if command == "new":
        project = studio.projects.create(args.name, args.board, args.tool, args.framework)
        print(f"created {project.name} in {project.path}")
        for need in studio.builds.requirements(project):
            print(f"needs: {need['label']} ({need['fix']})")
        return 0
    if command == "build":
        studio.tools.refresh(wait=True)
        project = studio.project(args.project)
        return _follow(studio, studio.builds.run(project, args.action, source="cli"))
    if command == "devices":
        devices = studio.devices.scan()
        for device in devices:
            print(f"{device.port:14} {device.vid}:{device.pid}  {device.describe()}"
                  + (f"  [{device.problem}]" if device.problem else ""))
        if not devices:
            print("no USB serial devices")
        return 0
    if command in ("upload", "identify"):
        studio.tools.refresh(wait=True)
        studio.devices.scan()
        if command == "identify":
            from .projects.model import Project
            project = studio.projects.current() or Project(path="", name="-", platform="esp32",
                                                           board_id="esp32-devkit",
                                                           toolchain="arduino-cli")
        else:
            project = studio.project(args.project)
        plan = studio.uploads.prepare(project, args.port, "identify" if command == "identify"
                                      else "upload")
        for check in plan.checks:
            mark = "ok" if check.ok else check.level
            print(f"[{mark:7}] {check.message}")
        if not plan.ok:
            print("stopped: " + plan.problem())
            return 1
        if command == "upload" and not args.yes:
            answer = input(f"Flash {plan.project} to {plan.port}? Type yes: ")
            if answer.strip().lower() != "yes":
                print("not flashed")
                return 1
        return _follow(studio, studio.uploads.start(plan.token, confirmed=True))
    if command == "monitor":
        stop = threading.Event()
        studio.events.subscribe(lambda topic, data: [print(l["text"]) for l in data["lines"]]
                                if topic == "monitor" else None)
        studio.monitor.connect(args.port, args.baud)
        print(f"-- {args.port} at {args.baud} baud; Ctrl+C to stop")
        try:
            while not stop.wait(1):
                pass
        except KeyboardInterrupt:
            pass
        return 0
    if command == "tools":
        if args.install:
            return _follow(studio, studio.builds.install_tool(args.install))
        if args.core:
            studio.tools.refresh(wait=True)
            return _follow(studio, studio.builds.install_core(args.core))
        studio.tools.refresh(wait=True)
        for tool in studio.tools.all():
            state = f"{tool.version or 'found'}  ({tool.source})" if tool.found else \
                f"missing  {tool.hint}"
            print(f"{tool.name:22} {state}")
        return 0
    if command == "claude":
        studio.tools.refresh(wait=True, only=["claude"])
        if args.action == "status":
            print(json.dumps(studio.ai.refresh_status(wait=True), indent=2, default=str))
            return 0
        if args.action == "logout":
            print("signed out" if studio.ai.logout() else "sign-out failed")
            return 0
        session = studio.ai.login_start()
        deadline = time.time() + 600
        while time.time() < deadline:
            session = studio.ai.login_session
            if session.state == "waiting" and session.url:
                print(f"Open this link and sign in:\n  {session.url}")
                code = input("Paste the code shown after signing in (Enter if none): ").strip()
                if code:
                    studio.ai.login_code(code)
                while session.state in ("waiting", "checking"):
                    time.sleep(0.5)
            if session.state in ("done", "failed", "cancelled"):
                break
            time.sleep(0.3)
        print(f"sign-in: {session.state} {session.error}")
        return 0 if session.state == "done" else 1
    if command == "ai":
        studio.tools.refresh(wait=True)
        studio.ai.refresh_status(wait=True)
        project = studio.project(args.project or None)
        if args.approve:
            print(studio.ai.approve(project))
            return 0
        if args.reject:
            print(studio.ai.reject(project))
            return 0
        if args.undo:
            print(studio.ai.undo(project))
            return 0
        studio.ai.start()
        turn = studio.ai.ask(project, args.text, mode=args.mode or None, source="cli")
        job = studio.jobs.get(turn.job_id)
        seen = 0
        while not job.done_event.wait(0.5):
            for step in turn.activities[seen:]:
                print("·", step["label"])
            seen = len(turn.activities)
        job.done_event.wait()
        data = turn.to_dict()
        print(data["text"])
        print(f"-- {data['state']}  {data['summary']}")
        for change in data["changes"]:
            print(f"   {change['status']:8} {change['path']} +{change['added']} -{change['removed']}"
                  + (f"  RISKY: {'; '.join(change['risky'])}" if change["risky"] else ""))
        if data["state"] == "review":
            print("approve with: ./run.sh cli ai --approve   (or --reject)")
        return 0 if data["state"] in ("done", "review") else 1
    return 1
