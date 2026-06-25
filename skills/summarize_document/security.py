"""
Zero-Trust Security Layer for the summarize_document skill.

Execution order (MANDATORY):
    1. detect_injection(raw_input)            → raises InjectionDetectedError if hostile
    2. mask_pii(sanitized_input)             → returns masked copy, stores PII map locally
    3. [core.py executes LLM call]
    4. scrub_secrets_from_output(raw_output) → strips any leaked secrets / PII
    5. restore_pii(masked_output)            → optionally re-hydrates safe fields

This module has ZERO external dependencies beyond the Python standard library.
It MUST NOT import from core.py, config.py, or make any LLM calls.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any


# ─────────────────────────────────────────────────────────────────────────────
# Custom Exceptions
# ─────────────────────────────────────────────────────────────────────────────

class SecurityError(Exception):
    """Base class for all security violations."""


class InjectionDetectedError(SecurityError):
    """Raised when a prompt injection attempt is detected in input."""


class PIIError(SecurityError):
    """Raised when PII handling fails in a way that risks leakage."""


# ─────────────────────────────────────────────────────────────────────────────
# PII Patterns
# ─────────────────────────────────────────────────────────────────────────────

_PII_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    (
        "EMAIL",
        re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"),
        "[MASKED_EMAIL]",
    ),
    (
        "PHONE_US",
        re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"),
        "[MASKED_PHONE]",
    ),
    (
        "SSN",
        re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
        "[MASKED_SSN]",
    ),
    (
        "CREDIT_CARD",
        re.compile(r"\b(?:\d[ \-]?){13,16}\b"),
        "[MASKED_CC]",
    ),
    (
        "IPV4",
        re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
        "[MASKED_IP]",
    ),
    (
        "AWS_KEY",
        re.compile(r"(?:AKIA|AIPA|ASIA|AROA)[A-Z0-9]{16}"),
        "[MASKED_AWS_KEY]",
    ),
    (
        "JWT",
        re.compile(r"ey[A-Za-z0-9_\-]+\.ey[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"),
        "[MASKED_JWT]",
    ),
    (
        "GENERIC_SECRET",
        re.compile(
            r"(?i)(?:api[_\-]?key|secret|token|password|passwd|pwd)\s*[:=]\s*[\"']?[\w\-]{8,}[\"']?"
        ),
        "[MASKED_SECRET]",
    ),
]

# ─────────────────────────────────────────────────────────────────────────────
# Prompt Injection Indicators
# ─────────────────────────────────────────────────────────────────────────────

_INJECTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"ignore\s+(all\s+)?(?:previous|prior|above)\s+instructions?", re.I),
    re.compile(r"disregard\s+(?:all\s+)?(?:previous|prior|above)\s+instructions?", re.I),
    re.compile(r"forget\s+(?:everything|all)\s+(?:above|before|previously)", re.I),
    re.compile(r"you\s+are\s+now\s+(?:a|an|the)\s+", re.I),
    re.compile(r"act\s+as\s+(?:a|an|the)\s+(?:different|new)\s+", re.I),
    re.compile(r"new\s+(?:persona|role|identity|instructions?)", re.I),
    re.compile(r"</?(system|user|assistant|human|ai)\s*>", re.I),
    re.compile(r"\[/?(?:INST|SYS|SYSTEM|END)\]", re.I),
    re.compile(r"<</?(?:SYS|SYSTEM)>>", re.I),
    re.compile(r"<\|(?:system|user|assistant|im_start|im_end)\|>", re.I),
    # Covers "reveal your prompt", "output the context", etc.
    re.compile(
        r"(?:repeat|print|output|reveal|show|display)\s+(?:your\s+)?(?:system\s+)?(?:prompt|instructions?|context)",
        re.I,
    ),
    # Covers "reveal the contents of your context window" (words between verb and object)
    re.compile(
        r"(?:reveal|show|display)\s+(?:the\s+)?(?:contents?\s+of\s+(?:your\s+)?)?(?:(?:system\s+)?(?:prompt|instructions?)|context(?:\s+window)?)",
        re.I,
    ),
    # Covers "what are your system instructions" (optional qualifier before object)
    re.compile(
        r"what\s+(?:are\s+)?your\s+(?:(?:system|all|prior|previous?)\s+)?(?:instructions?|rules?|guidelines?|prompt)",
        re.I,
    ),
    re.compile(r"(?:leak|exfil(?:trate)?|dump)\s+(?:the\s+)?(?:context|prompt|memory|history)", re.I),
    re.compile(r"\bDAN\b"),
    re.compile(r"jailbreak", re.I),
    re.compile(r"developer\s+mode", re.I),
    re.compile(r"sudo\s+mode", re.I),
]

# ─────────────────────────────────────────────────────────────────────────────
# PII Map — ephemeral, never persisted
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PIIMap:
    """
    Ephemeral mapping from placeholder token to original PII value.
    Lives only in memory, within a single skill invocation's call stack.
    MUST NOT be serialized, logged, or returned to callers.
    """
    _store: dict[str, str] = field(default_factory=dict)

    def put(self, original: str, label: str) -> str:
        """Store original PII and return a stable, unique placeholder."""
        token = f"[{label}_{hashlib.sha256(original.encode()).hexdigest()[:8].upper()}]"
        self._store[token] = original
        return token

    def restore(self, text: str) -> str:
        """Re-hydrate masked tokens in text. Only call on final output to trusted caller."""
        for token, original in self._store.items():
            text = text.replace(token, original)
        return text

    def clear(self) -> None:
        """Wipe the PII map. Call after the skill invocation completes."""
        self._store.clear()


# ─────────────────────────────────────────────────────────────────────────────
# Security Layer Class
# ─────────────────────────────────────────────────────────────────────────────

class SummarizeDocumentSecurityLayer:
    """
    Stateless security processor. Instantiate fresh per skill invocation.
    Do NOT reuse an instance across calls — the PIIMap must be request-scoped.
    """

    def __init__(self) -> None:
        self._pii_map = PIIMap()

    # ── Phase 1: Injection Detection ─────────────────────────────────────────

    def detect_injection(self, text: str) -> None:
        """
        Scan text for prompt injection signatures.
        Raises InjectionDetectedError immediately on first match.
        Does NOT attempt to sanitize — hostile input is rejected, not cleaned.
        """
        for pattern in _INJECTION_PATTERNS:
            if pattern.search(text):
                raise InjectionDetectedError(
                    f"Prompt injection pattern detected: {pattern.pattern[:60]!r}"
                )

    def scan_input_fields(self, data: dict[str, Any]) -> None:
        """
        Recursively scan all string values in input data for injection signatures.
        Call this on the raw dict BEFORE constructing the Pydantic model.
        """
        for key, value in data.items():
            if isinstance(value, str):
                self.detect_injection(value)
            elif isinstance(value, dict):
                self.scan_input_fields(value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, str):
                        self.detect_injection(item)
                    elif isinstance(item, dict):
                        self.scan_input_fields(item)

    # ── Phase 2: PII Masking ──────────────────────────────────────────────────

    def mask_pii(self, text: str) -> str:
        """
        Detect and replace PII in text with stable placeholder tokens.
        The original values are stored in self._pii_map for optional restoration.
        """
        masked = text
        for label, pattern, _default_replacement in _PII_PATTERNS:
            def _replacer(m: re.Match, _label: str = label) -> str:
                return self._pii_map.put(m.group(0), _label)
            masked = pattern.sub(_replacer, masked)
        return masked

    def mask_sensitive_fields(self, model_instance: Any) -> dict[str, Any]:
        """
        Given a Pydantic model instance, return a dict with sensitive fields masked.
        Reads json_schema_extra on each field to determine masking behavior.
        """
        schema = type(model_instance).model_fields
        result = model_instance.model_dump()
        for field_name, field_info in schema.items():
            extra = field_info.json_schema_extra or {}
            value = result.get(field_name)
            if isinstance(value, str):
                if extra.get("sanitize"):
                    result[field_name] = self.mask_pii(value)
                if extra.get("sensitive"):
                    result[field_name] = "[REDACTED]"
        return result

    # ── Phase 3: Output Sanitization ─────────────────────────────────────────

    def scrub_secrets_from_output(self, text: str) -> str:
        """
        Run the PII and secret patterns over model output.
        Catches cases where the LLM echoes or hallucinates secrets from context.
        """
        scrubbed = text
        for _label, pattern, replacement in _PII_PATTERNS:
            scrubbed = pattern.sub(replacement, scrubbed)
        return scrubbed

    def restore_pii(self, text: str) -> str:
        """
        Re-hydrate masked tokens. Only call when returning to a TRUSTED caller
        who originally provided the PII and is authorized to receive it back.
        """
        return self._pii_map.restore(text)

    def cleanup(self) -> None:
        """Wipe ephemeral PII map. Called in the finally block inside core.py."""
        self._pii_map.clear()
