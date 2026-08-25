"""Local redaction — anything that leaves this process toward the Host goes through here first.
Ported from Sensei's redact.ts (github.com/Claude-Ovo/sensei, packages/cli/src/lib/redact.ts).
Rule: over-redact rather than leak. Private-net IPs are kept (useful for diagnosis); public IPs are masked.
"""
import os, re

RULES = [
    # URLs with embedded credentials — must run BEFORE the email rule (user:pass@host looks like an email)
    (re.compile(r"(\w+://)[^\s/:@]+:[^\s/@]+@"), r"\1<REDACTED_CRED>@"),
    # common API key / token shapes
    (re.compile(r"\b(sk|rk|pk)_(live|test|proj)?_?[A-Za-z0-9]{16,}\b"), "<REDACTED_KEY>"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"), "<REDACTED_KEY>"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"), "<REDACTED_KEY>"),
    (re.compile(r"\bAQ\.[A-Za-z0-9_-]{20,}\b"), "<REDACTED_KEY>"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "<REDACTED_KEY>"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), "<REDACTED_KEY>"),
    (re.compile(r"\bxox[abpr]-[A-Za-z0-9-]{10,}\b"), "<REDACTED_KEY>"),
    (re.compile(r"\bya29\.[A-Za-z0-9._-]{20,}\b"), "<REDACTED_KEY>"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"), "<REDACTED_JWT>"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"), "<REDACTED_PRIVATE_KEY>"),
    (re.compile(r"(Bearer\s+)[A-Za-z0-9._~+/=-]{16,}", re.I), r"\1<REDACTED_TOKEN>"),
    (re.compile(r"(Basic\s+)[A-Za-z0-9+/=]{8,}", re.I), r"\1<REDACTED_TOKEN>"),
    # KEY=value / "key": "value" style secrets
    (re.compile(r"((?:api[_-]?key|secret[_-]?(?:access[_-]?)?key|private[_-]?key|client[_-]?secret|secret|token|passw(?:or)?d|authorization)\s*[=:]\s*[\"']?)(?!Bearer\b|Basic\b)[^\s\"'&]{6,}", re.I), r"\1<REDACTED>"),
    # email
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "<EMAIL>"),
    # public IPv4 (private ranges kept)
    (re.compile(r"\b(?!10\.|127\.|192\.168\.|172\.(?:1[6-9]|2\d|3[01])\.)\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"), "<IP>"),
]

_HOME = os.environ.get("USERPROFILE") or os.environ.get("HOME") or ""
_USER = os.environ.get("USERNAME") or os.environ.get("USER") or ""
_HOME_RE = re.compile(re.escape(_HOME).replace(r"\\", r"[\\/]"), re.I) if _HOME else None
_USER_RE = re.compile(rf"(?<=[\\/]){re.escape(_USER)}(?=[\\/])", re.I) if len(_USER) >= 3 else None

def redact(text: str, extra_secrets=()) -> str:
    """Redact secrets, emails, public IPs, and the local username/home from text."""
    if not text:
        return text
    s = text
    for sec in extra_secrets:
        if sec and len(sec) >= 6:
            s = s.replace(sec, "<REDACTED>")
    for pat, rep in RULES:
        s = pat.sub(rep, s)
    # username first (any path containing it), then the full home dir → "~"
    if _USER_RE:
        s = _USER_RE.sub("<USER>", s)
    if _HOME_RE:
        s = _HOME_RE.sub("~", s)
    return s

def redact_card(card: dict) -> dict:
    """Redact every string field of a resolution card (the only thing returned to the Host)."""
    if not card:
        return card
    return {k: (redact(v) if isinstance(v, str) else v) for k, v in card.items()}
