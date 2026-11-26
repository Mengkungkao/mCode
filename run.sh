#!/bin/sh
# Entrypoint. mFruit OS starts it through mfruit-run with the working directory
# set to the active version and WHISPLAY_APP_ID / WHISPLAY_OS_APP_DATA set.
# By hand: ./run.sh --desktop, --preview DIR, --diagnose, cli --help.
cd "$(dirname "$0")" || exit 1
export PYTHONUNBUFFERED=1
exec python3 -m app.main "$@"
