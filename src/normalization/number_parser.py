"""
Number Parser and Scale/Sign Normalizer.
Deterministic parsing: code parses numbers, never the LLM.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Optional

_NIL = {"-", "–", "—", "nihil", "nil", "none", "n/a", "na", ""}
_STRIP_REGEX = re.compile(r"(rp\.?|us\$|usd|idr|\$|%|\s|[()\-−])", re.IGNORECASE)


def parse_reported_number(raw: str, locale: str = "id") -> Optional[Decimal]:
    """
    Parse as-reported numeric strings into Decimal.
    Supports parentheses for negatives, various dash types for nil, and locale-based separators.
    """
    if not raw:
        return None
    s = raw.strip()
    if s.lower() in _NIL:
        return Decimal(0)

    # Check if negative via parentheses or negative signs
    negative = (s.startswith("(") and s.endswith(")")) or s.startswith("-") or s.startswith("−")
    # Clean inner content
    s_clean = _STRIP_REGEX.sub("", s)
    if not s_clean:
        return Decimal(0)

    loc = str(locale).lower()
    if loc in ("id", "numberlocale.id"):
        # Indonesian: dot is thousand separator, comma is decimal separator (e.g. 1.234.567,89)
        s_clean = s_clean.replace(".", "").replace(",", ".")
    else:
        # English: comma is thousand separator, dot is decimal separator (e.g. 1,234,567.89)
        s_clean = s_clean.replace(",", "")

    try:
        val = Decimal(s_clean)
        return -val if negative else val
    except InvalidOperation:
        return None


def apply_scale(val: Optional[Decimal], scale: int) -> Optional[Decimal]:
    """Applies reporting scale (e.g. scale=1_000_000 for millions)."""
    if val is None:
        return None
    return val * Decimal(scale)


def grounded(raw_text: str, page_text: str) -> bool:
    """Hard rule: extracted value must appear verbatim (whitespace-squashed) on its cited page."""
    if not raw_text or not page_text:
        return False
    squash = lambda x: re.sub(r"[\s()\-−,.]+", "", str(x)).lower()
    s_raw = squash(raw_text)
    if not s_raw:
        return True
    return s_raw in squash(page_text)
