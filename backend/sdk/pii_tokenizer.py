"""Reversible PII tokenization for conversation messages.

Pure text transform — knows nothing about the database, FastAPI, asyncpg, or
the LLM. ``tokenize`` replaces PII with stable ``<pii:TYPE:N>`` tokens and
returns a map of token → original; ``detokenize`` reverses it. The map is
stored alongside the message so the LLM only ever sees tokens while the user
always sees the original text.

Regexes are sourced from ``sdk.redactor`` (single source of truth). IP_ADDRESS
is excluded — it is observability metadata, not conversational PII.
"""
from __future__ import annotations

import re

from sdk.redactor import PATTERNS

# Ordered structured PII types. Order follows redactor.PATTERNS so a 13–16 digit
# card is matched before narrower numeric patterns can shadow it.
_TYPES: list[str] = [name for name in PATTERNS if name != "IP_ADDRESS"]

# Matches the tokens this module emits, for the reverse pass.
# NOTE: format is [PII:TYPE:N] (square brackets). Angle-bracket tokens read as XML to LLMs,
# which made models echo them back literally. Legacy rows stored with the old <pii:TYPE:N>
# format will not detokenize — this regex no longer matches them, so detokenize returns the
# token as-is (it never raises). Acceptable for demo; production would run a one-off backfill.
_TOKEN_RE = re.compile(r"\[PII:([A-Z_]+):(\d+)\]")


class PiiTokenizer:
    def tokenize(self, text: str) -> tuple[str, dict | None]:
        """Replace PII with ``<pii:TYPE:N>`` tokens.

        Returns ``(tokenized_text, pii_map)`` where ``pii_map`` is
        ``{"TYPE:N": original}``. ``N`` is 1-based and resets per type per call.
        If no PII is found, returns ``(text, None)``.
        """
        if not text:
            return text, None

        pii_map: dict[str, str] = {}
        for pii_type in _TYPES:
            counter = {"n": 0}

            def _replace(match: re.Match, _type=pii_type, _counter=counter) -> str:
                _counter["n"] += 1
                key = f"{_type}:{_counter['n']}"
                pii_map[key] = match.group(0)
                return f"[PII:{_type}:{_counter['n']}]"

            text = PATTERNS[pii_type].sub(_replace, text)

        return text, (pii_map or None)

    def detokenize(self, text: str, pii_map: dict | None) -> str:
        """Restore original values from ``pii_map``. Never raises.

        A token whose key is missing from the map is left untouched.
        """
        if not pii_map or not text:
            return text

        def _restore(match: re.Match) -> str:
            key = f"{match.group(1)}:{match.group(2)}"
            return pii_map.get(key, match.group(0))

        return _TOKEN_RE.sub(_restore, text)
