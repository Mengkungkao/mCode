"""mCode Studio's entry point.

    run.sh                    automatic: under mFruit OS the Whisplay LCD (plus the desktop
                              IDE server, opened on the HDMI desktop when one is running);
                              elsewhere the desktop IDE in a browser
    run.sh --lcd              the LCD only (needs whisplay-daemon)
    run.sh --desktop          the desktop IDE only (prints its address)
    run.sh --preview DIR      every LCD screen as PNG files
    run.sh --selftest         the install smoke test (no screen, network, audio or boards)
    run.sh --diagnose         tools, Claude Code, boards, audio and paths
    run.sh cli ...            command line (projects, build, upload, monitor, ai): cli --help
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading

from . import APP_NAME, VERSION, paths

log = logging.getLogger("mcode")
EXIT_NO_DAEMON = 2


def setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stdout)


def _wait_for_signal(event: threading.Event) -> None:
    def leave(_signum, _frame):
        event.set()
    signal.signal(signal.SIGTERM, leave)
    signal.signal(signal.SIGINT, leave)
    while not event.wait(3600):
        pass


def start_web(studio, port=None, lan=None, open_browser=True):
    from .web.server import WebServer
    if lan is not None:
        studio.settings.set("web.lan", bool(lan), save=False)
    web = WebServer(studio, port=port)
    if not web.start():
        return None
    studio.web = web
    studio.on_stop(web.stop)
    if open_browser and studio.settings.get("ui.open_browser") and web.desktop_available():
        ok, why = web.open_browser()
        log.info("desktop IDE %s", "opened on the desktop" if ok else f"not opened: {why}")
    return web


def run_desktop(args) -> int:
    from .studio import Studio
    studio = Studio()
    studio.web = None
    studio.start()
    web = start_web(studio, port=args.port, lan=args.lan or None, open_browser=not args.no_browser)
    if web is None:
        print("The desktop IDE could not start (is the port in use?).", file=sys.stderr)
        studio.stop()
        return 1
    print(f"{APP_NAME} {VERSION}\nDesktop IDE: {web.local_url()}")
    for url in web.urls(with_token=False)[1:] if studio.settings.get("web.lan") else []:
        print(f"  on the network: {url}  (pairing code {web.pair_code()})")
    print("Ctrl+C stops mCode.")
    stop = threading.Event()
    _wait_for_signal(stop)
    studio.stop(timeout=2.0)
    return 0


def run_lcd(args, allow_fallback: bool) -> int:
    from .lcd.ui import DeviceUI
    from .studio import Studio
    studio = Studio()
    studio.web = None
    studio.start()
    if studio.settings.get("ui.desktop_ide") and studio.settings.get("ui.display_mode") != "lcd":
        start_web(studio, port=args.port, lan=args.lan or None, open_browser=not args.no_browser)
    ui = DeviceUI(studio)
    if not ui.start():
        if allow_fallback and studio.web is not None:
            print(f"whisplay-daemon is not running: desktop IDE only.\n{studio.web.local_url()}")
            stop = threading.Event()
            _wait_for_signal(stop)
            studio.stop()
            return 0
        studio.stop()
        print("whisplay-daemon is not running: no LCD to draw on. Try: ./run.sh --desktop",
              file=sys.stderr)
        return EXIT_NO_DAEMON

    def leave(_signum, _frame):
        ui.request_exit()

    signal.signal(signal.SIGTERM, leave)
    signal.signal(signal.SIGINT, leave)
    ui.exit_requested.wait()
    log.info("leaving")
    # app_exit_requested must be answered within 3 seconds.
    done = threading.Event()

    def stop_all():
        try:
            studio.stop(timeout=1.5)
        finally:
            done.set()

    threading.Thread(target=stop_all, daemon=True).start()
    done.wait(2.0)
    ui.stop()
    return 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["cli"]:
        from . import cli
        return cli.main(argv[1:])
    parser = argparse.ArgumentParser(prog="mcode", description=f"{APP_NAME}: AI embedded "
                                     "development for mFruit OS")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--lcd", action="store_true", help="the Whisplay LCD interface")
    mode.add_argument("--desktop", action="store_true", help="the desktop IDE only")
    mode.add_argument("--preview", metavar="DIR", help="render every LCD screen to PNG files")
    mode.add_argument("--selftest", action="store_true", help="install smoke test")
    mode.add_argument("--diagnose", action="store_true", help="report tools, boards and setup")
    parser.add_argument("--port", type=int, default=None, help="desktop IDE port")
    parser.add_argument("--lan", action="store_true", help="allow computers on the network")
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {VERSION}")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    if args.selftest:
        from . import selftest
        return selftest.main()
    if args.preview:
        from . import preview
        return preview.main(args.preview)
    setup_logging(args.verbose)
    if args.diagnose:
        from . import diagnostics
        return diagnostics.main()
    if args.desktop:
        return run_desktop(args)
    if args.lcd:
        return run_lcd(args, allow_fallback=False)
    from .core.config import Settings
    display = Settings(paths.Paths().settings_file).get("ui.display_mode")
    if display == "desktop":
        return run_desktop(args)
    if paths.under_mfruit_os() or display == "lcd" or os.path.exists(
            os.environ.get("WHISPLAY_DAEMON_SOCKET", "/tmp/whisplay-daemon.sock")):
        return run_lcd(args, allow_fallback=not paths.under_mfruit_os())
    return run_desktop(args)


if __name__ == "__main__":
    sys.exit(main())
