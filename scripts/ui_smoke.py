#!/usr/bin/env python3
"""GUI smoke test: serve the Flet app on the web, drive its screens from Python, screenshot each with headless
Chromium (Playwright). Any exception in a view shows up on stdout; screenshots land in --out.

    FRAMEPORT_HOME=<a test data dir> python scripts/ui_smoke.py --out /tmp/shots [--game <package>]
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import flet as ft  # noqa: E402

from frameport.core import library  # noqa: E402
from frameport.ui.app import FramePortApp  # noqa: E402

PORT = 8557
ERRORS: list[str] = []


def driver(app: FramePortApp, steps: list[tuple[str, callable]], ready: threading.Event, done_step: list):
    for name, action in steps:
        try:
            action(app)
        except Exception:  # noqa: BLE001
            ERRORS.append(f"{name}: {traceback.format_exc()}")
        done_step.append(name)
        ready.set()
        time.sleep(4)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--game", default=None)
    ap.add_argument("--frame", default=None, help="steamos@host: also connect and run a launch-test job for --game")
    ap.add_argument("--no-test", action="store_true", help="with --frame: skip the launch-test job")
    ap.add_argument("--scale", type=float, default=1.0, help="UI scale to render at (e.g. 1.5)")
    ap.add_argument("--viewport", default="1280x820", help="browser size, e.g. 2560x1440")
    ap.add_argument("--update", action="store_true", help="pretend a new FramePort release exists (update UI)")
    args = ap.parse_args()
    from frameport.ui import theme

    theme.set_scale(args.scale)
    vw, vh = (int(x) for x in args.viewport.split("x"))
    args.out.mkdir(parents=True, exist_ok=True)
    game = args.game or (library.games()[0]["package"] if library.games() else None)
    steps = [("library", lambda a: a.navigate(0)), ("frame", lambda a: a.navigate(1)), ("tools", lambda a: a.navigate(2))]
    if game:
        steps.insert(1, ("game", lambda a: a.open_game(game)))
        steps.insert(2, ("game-customize", lambda a: a.open_game(game, advanced=True)))
        steps += [("share-dialog", lambda a: a.share_config_dialog(game)),
                  ("report-dialog", lambda a: (a.page.pop_dialog(), a.report_problem_dialog(game)))]
        # last: the right-click menu stays open over whatever comes next
        steps.append(("library-menu", lambda a: (a.page.pop_dialog(), a.navigate(0), time.sleep(3),
                                                 a.library_view.open_menu(game))))
    if args.update:
        from frameport import updates

        fake = updates.Update(version="9.9.9", tag="v9.9.9", page="https://example.invalid", asset=None, asset_url=None,
                              sums_url=None, wheel_url=None,
                              notes="## What's new\n- Self-update test release\n- **Bold** and `code` in notes")
        steps += [("update-banner", lambda a: (a.updater._set(fake), a.navigate(0))),
                  ("update-dialog", lambda a: a.updater.show_dialog()),
                  ("update-settings", lambda a: (a.page.pop_dialog(), a.navigate(2)))]
    if args.frame:
        from frameport.frame.connection import parse_target

        def connect(a):
            a.connect(parse_target(args.frame))
            for _ in range(60):
                if a.target:
                    break
                time.sleep(1)
            a.navigate(1)

        def job(a):
            a.start_job(game, build=False, install=False, test=True)
            time.sleep(5)
            while a.job_running:
                time.sleep(2)

        steps += [("frame-connected", connect), ("library-connected", lambda a: a.navigate(0))]
        if game:
            steps.append(("game-connected", lambda a: a.open_game(game)))
        if not args.no_test:
            steps.append(("launch-test-job", job))
    ready, done = threading.Event(), []

    def app_main(page: ft.Page):
        try:
            app = FramePortApp(page)
        except Exception:  # noqa: BLE001
            ERRORS.append("startup: " + traceback.format_exc())
            return
        threading.Thread(target=driver, args=(app, steps, ready, done), daemon=True).start()

    def shooter():
        from playwright.sync_api import sync_playwright

        time.sleep(6)
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(args=["--use-gl=swiftshader", "--enable-unsafe-swiftshader"])
                page = browser.new_page(viewport={"width": vw, "height": vh})
                page.goto(f"http://127.0.0.1:{PORT}", wait_until="networkidle", timeout=120_000)
                shot = 0
                deadline = time.time() + 240 + 8 * len(steps)
                while len(done) < len(steps) and time.time() < deadline:
                    if ready.wait(1):
                        ready.clear()
                        time.sleep(3)  # let Flutter paint
                        page.screenshot(path=str(args.out / f"{shot:02d}-{done[-1]}.png"))
                        shot += 1
                browser.close()
        except Exception:  # noqa: BLE001
            ERRORS.append("browser: " + traceback.format_exc())
        for e in ERRORS:
            print("ERROR", e, flush=True)
        print(f"screens: {[p.name for p in sorted(args.out.glob('*.png'))]}", flush=True)
        os._exit(1 if ERRORS else 0)

    threading.Thread(target=shooter, daemon=True).start()
    from frameport.ui.app import assets_dir

    ft.run(app_main, view=ft.AppView.WEB_BROWSER, port=PORT, assets_dir=assets_dir())
    return 1


if __name__ == "__main__":
    sys.exit(main())
