#!/bin/sh
# Install hook. mFruit OS runs it on install, reinstall, update and downgrade,
# as the user, without a terminal; a non-zero exit keeps the previous version.
# It never uses sudo, changes nothing outside mCode's data folder, and installs
# no development tools: those are installed on request from Settings > Development
# tools (Claude Code, Arduino CLI, PlatformIO, esptool).
#
#   Python 3.9+, Pillow 9.0+   required   (Debian: python3-pil)
#   arecord, aplay             voice      (alsa-utils)
#   git                        optional   (version history, clone)
set -eu
cd "$(dirname "$0")"
export PYTHONDONTWRITEBYTECODE=1
DATA="${WHISPLAY_OS_APP_DATA:-${XDG_DATA_HOME:-$HOME/.local/share}/mcode}"

if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))'; then
  echo "mcode: Python 3.9 or newer is needed"; exit 1
fi
if ! python3 -c 'import PIL' 2>/dev/null; then
  echo "mcode: missing python3-pil (install with: sudo apt install python3-pil)"; exit 1
fi
for tool in arecord aplay; do
  command -v "$tool" >/dev/null 2>&1 || echo "mcode: warning: $tool not found (sudo apt install alsa-utils); voice stays off"
done
command -v git >/dev/null 2>&1 || echo "mcode: note: git not found; version history is off (sudo apt install git)"
if ! id -nG | tr ' ' '\n' | grep -qx dialout; then
  echo "mcode: warning: $(id -un) is not in the dialout group; boards cannot be opened until: sudo usermod -aG dialout $(id -un)"
fi

mkdir -p "$DATA"
chmod 700 "$DATA"
# The keys file (speech recognition only): created once, owner-only, never overwritten.
if [ ! -e "$DATA/.env" ]; then
  umask 077
  cp .env.example "$DATA/.env"
  chmod 600 "$DATA/.env"
  echo "mcode: created $DATA/.env (optional: OPENAI_API_KEY for voice requests)"
fi
python3 -c 'import PIL, sys; print("mcode: dependencies ok: Python", sys.version.split()[0], "Pillow", PIL.__version__)'
