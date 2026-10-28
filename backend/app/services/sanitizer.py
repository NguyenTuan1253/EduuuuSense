"""Prompt-injection sanitization & data-block isolation (Invariant 13).

Retrieved document text and user documents are UNTRUSTED DATA, never
instructions. The sanitizer:
- strips anything resembling the adapter's own fences so data cannot escape
  the untrusted block (block-escape defense),
- neutralizes Markdown/role markers commonly used to fake authority,
- detects known injection phrasing for telemetry (detection never silently
  deletes user-visible content — flagging and fencing are the defenses).

The model is additionally given no tools and no action permissions anywhere.
"""

import re
from typing import Any

# The adapter's fences — data must never contain these.
BLOCK_MARKERS = (
    "<<<<<BEGIN_UNTRUSTED_DATA>>>>>",
    "<<<<<END_UNTRUSTED_DATA>>>>>",
)

# Authority-simulation markers stripped from untrusted content.
_ROLE_MARKER = re.compile(r"^(system|assistant|developer)\s*:", re.IGNORECASE | re.MULTILINE)

# Known injection phrasings — flagged for telemetry, never auto-deleted.
_INJECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ignore_previous", re.compile(r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions", re.IGNORECASE)),
    ("disregard_above", re.compile(r"disregard\s+(all\s+)?(previous|prior|the)\s+(instructions|rules|above)", re.IGNORECASE)),
    ("reveal_prompt", re.compile(r"(reveal|show|print|repeat)\s+(your\s+)?(system\s+)?(prompt|instructions)", re.IGNORECASE)),
    ("role_hijack", re.compile(r"you\s+are\s+now\s+(a|an|the)\s+", re.IGNORECASE)),
    ("new_instructions", re.compile(r"new\s+instructions?\s*:", re.IGNORECASE)),
)


def strip_block_markers(text: str) -> str:
    """Remove fence markers so data cannot escape the untrusted block."""
    cleaned = text
    for marker in BLOCK_MARKERS:
        cleaned = cleaned.replace(marker, "[blocked-marker]")
    return cleaned


def neutralize_role_markers(text: str) -> str:
    """Prefix fake role headers so they cannot simulate system messages."""
    return _ROLE_MARKER.sub(lambda m: f"[data-role-marker:{m.group(1).lower()}]", text)


def detect_injection_phrases(text: str) -> list[str]:
    """Names of known injection patterns present in the text (telemetry)."""
    return [name for name, pattern in _INJECTION_PATTERNS if pattern.search(text)]


def sanitize_untrusted(text: str) -> str:
    """Full sanitize pass for untrusted content before prompt assembly."""
    return neutralize_role_markers(strip_block_markers(text))


def build_data_block(chunks: list[dict[str, Any]], content_key: str = "content") -> str:
    """Render sanitized chunks as a numbered, fenced data block.

    Each chunk is labeled with its citation number so the model can reference
    [n] markers; the mapping n -> (document_id, chunk_index, page_number) is
    kept by the caller for the GroundingChecker (Invariant 6).
    """
    lines: list[str] = []
    for position, chunk in enumerate(chunks, start=1):
        content = sanitize_untrusted(str(chunk.get(content_key, "")))
        lines.append(f"[{position}] {content}")
    return "\n".join(lines)
