import re

_SECRET_PATTERNS = [
    re.compile(r"gh[posur]_[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
    re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
]
_KV_SECRET_RE = re.compile(
    r"(?i)\b(password|passwd|secret|token|api[_-]?key|access[_-]?key|client[_-]?secret)\b"
    r"(\s*[=:]\s*)(['\"]?)([^\s'\"]{6,})\3"
)


def scrub_secrets(text):
    """Redact secret-shaped strings from any content bound for GitHub (KTD9)."""
    if not text:
        return ""
    out = text
    for pat in _SECRET_PATTERNS:
        out = pat.sub("[REDACTED]", out)
    out = _KV_SECRET_RE.sub(lambda m: m.group(1) + m.group(2) + "[REDACTED]", out)
    return out
