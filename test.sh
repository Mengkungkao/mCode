#!/bin/sh
# Smoke test ("test" in manifest.json): mFruit OS runs it after installing and
# before activating; a non-zero exit keeps the previous version. It imports the
# app, checks settings, creates projects of every type in a temporary folder,
# runs a real job, renders every LCD screen and starts the desktop IDE on a
# free local port: no display, microphone, speaker, network or board is used.
set -e
export PYTHONDONTWRITEBYTECODE=1   # leave the installed version folder as shipped
cd "$(dirname "$0")"
python3 -m app.main --selftest
echo "mcode: test passed"
