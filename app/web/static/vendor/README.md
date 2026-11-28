# Vendored web libraries

Copied unmodified from npm (2026-11-27) so the desktop IDE works offline.
Do not edit these files; replace them with a newer release instead.

| Library | Version | Licence | Files |
|---|---|---|---|
| CodeMirror | 5.65.21 | MIT ([codemirror/LICENSE](codemirror/LICENSE)) | lib, the modes and addons mCode uses |
| xterm.js | 6.0.0 | MIT ([xterm/LICENSE](xterm/LICENSE)) | lib/xterm.js, css/xterm.css |
| @xterm/addon-fit | 0.11.0 | MIT (same repository) | lib/addon-fit.js |

`SHA256SUMS` records every vendored file; `bash tools/check.sh` fails when one
changes. After replacing a library with a newer release, regenerate it:
`cd app/web/static/vendor && find . -type f ! -name SHA256SUMS ! -name README.md | sort | xargs sha256sum > SHA256SUMS`.
