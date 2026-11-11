"""ログに認証情報を出さないためのフィルタ。"""
from __future__ import annotations

import logging
import re

_PATTERNS = [
    re.compile(r"(password[\"']?\s*[:=]\s*[\"']?)([^\"'\s,}]+)", re.I),
    re.compile(r"(token[\"']?\s*[:=]\s*[\"']?)([^\"'\s,}]+)", re.I),
    re.compile(r"(secret[\"']?\s*[:=]\s*[\"']?)([^\"'\s,}]+)", re.I),
    re.compile(r"(authorization:\s*bearer\s+)(\S+)", re.I),
    re.compile(r"(sessionid=)([^;\s]+)", re.I),
    re.compile(r"(csrftoken=)([^;\s]+)", re.I),
]


class RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        redacted = msg
        for p in _PATTERNS:
            redacted = p.sub(r"\1[REDACTED]", redacted)
        if redacted != msg:
            record.msg = redacted
            record.args = ()
        return True
