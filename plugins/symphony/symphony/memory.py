"""Redaction for text Symphony is about to persist."""

import re


_SECRET_PATTERNS = (
    re.compile(
        r"(?i)\b(?:password|passwd|secret|api[_-]?key|access[_-]?token|refresh[_-]?token)\b\s*[:=]\s*\S+"
    ),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(
        r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----.*?-----END (?:[A-Z]+ )?PRIVATE KEY-----",
        re.DOTALL,
    ),
    re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----"),
)


def redact_secrets(text: str) -> str:
    """Redact credential-shaped text before durable storage.

    A second line of defence only: the persistence allowlist in `model.py` is
    what keeps free text out of the store, because no pattern set can recognise
    every credential someone might paste into a prompt.
    """
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text
