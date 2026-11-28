# Release checklist

A release is a push of `manifest.json` with a new version: the release
workflow (`.github/workflows/release.yml`) runs the checks, builds the
package and publishes `v<version>`; the Fruit Store follows through mFruit
OS's catalogue sync. Never tag by hand. Status words follow
[Testing](TESTING.md): one never stands in for another.

## Before the version bump

- [ ] `bash tools/check.sh` passes (the same as CI).
- [ ] `./run.sh --preview /tmp/mcode-preview`: look at the screens that
      changed.
- [ ] Browser check of the changed screens: phone (≤ 760 px), tablet and
      desktop widths, light and dark; no console errors.
- [ ] Every confirmed bug fixed in the release has a regression test with a
      negative control (it failed before the fix).
- [ ] [Audit](AUDIT_2.0.md) and [Roadmap](ROADMAP_2.0.md) statuses current.
- [ ] Docs changed with the behaviour: README, USER_GUIDE, SECURITY,
      CONFIGURATION (generated: `python3 tools/gen-config-doc.py`),
      HARDWARE, CLAUDE_CODE, TROUBLESHOOTING, IOS_MOBILE, ARCHITECTURE.
- [ ] `CHANGELOG.md` has a `## [x.y.z] — date` section (it becomes the
      release notes) that says what was **not** verified.
- [ ] `CONTINUE.md` verification table current.

## Version

- [ ] `manifest.json` `version` and `app/__init__.py` `VERSION` are equal.
- [ ] `min_os_version` still right (mFruit OS app contract and SDK copy:
      `bash tools/check.sh` checks the copies).
- [ ] Package stays well under 100 MB (`bash tools/build-release.sh` locally;
      0.1.x was about 0.6 MB).

## Upgrade and rollback (on a board, with disposable data where possible)

- [ ] Upgrade from the previous release through the Fruit Store or
      `mfruitctl sideload`: projects in `~/mcode-projects`, `settings.json`,
      `.env`, backups and paired browsers survive (0.2.0: browsers paired
      with 0.1.x are migrated on their next request).
- [ ] mFruit OS rollback to the previous version starts and keeps the same
      data (older versions keep and ignore unknown settings keys, and fall
      back to the default for a value they do not know, such as
      `ui.theme: auto`). After a rollback from 0.2.0 to 0.1.x every browser
      pairs again: 0.1.x ignores `web-sessions.json`.
- [ ] `mfruitctl jobs` idle before installing on a board in use.

## Hardware (record in `docs/records/`)

- [ ] Launch from Home, first frame, 4× back, leave during a build (gone in
      5 s).
- [ ] Pair a phone from the LCD; Security page lists it; sign it out.
- [ ] Build, flash and serial monitor for at least one ESP32 and one AVR
      board; a wrong board is refused.
- [ ] Claude request in Ask mode end to end (small, it costs the owner's plan).

Anything not done is written as **NOT VERIFIED** in the CHANGELOG entry.
