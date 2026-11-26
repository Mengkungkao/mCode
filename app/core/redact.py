"""Remove secrets from text before it is logged, shown or sent to the AI.

Covers API keys and tokens in the shapes mCode can meet: Anthropic
(``sk-ant-...``), OpenAI (``sk-...``), GitHub (``ghp_``/``github_pat_``),
AWS access keys, bearer headers, ``KEY=value`` lines whose name says secret,
URL query parameters such as ``code=``/``token=`` and passwords in URLs.
It is a safety net, not a guarantee: mCode never puts its own secrets into
logs in the first place.
"""

from __future__ import annotations

import re

_PATTERNS = [
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"), "sk-ant-…"),
    (re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{16,}"), "sk-…"),
    (re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}"), "gh…"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"), "github_pat_…"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AKIA…"),
    (re.compile(r"(?i)(authorization:\s*bearer\s+)[^\s\"']+"), r"\1…"),
    (re.compile(r"(?i)\b([A-Z0-9_]*(?:API_KEY|TOKEN|SECRET|PASSWORD|PASSWD)[A-Z0-9_]*\s*[=:]\s*)"
                r"(?!\s)([^\s\"']+)"), r"\1…"),
    (re.compile(r"(?i)([?&](?:code|token|access_token|refresh_token|key|state|code_verifier)=)"
                r"[^&\s#\"']+"), r"\1…"),
    (re.compile(r"(?i)(https?://[^/\s:@]+:)[^@\s/]+@"), r"\1…@"),
]


def redact(text: str) -> str:
    if not text:
        return text
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text
