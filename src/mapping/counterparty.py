"""
Counterparty Normalizer and Resolution Engine.
Resolves mentions like 'PT Bank Mandiri (Persero) Tbk' -> canonical counterparty.
"""
from __future__ import annotations

import re


def normalize_counterparty_name(name: str) -> str:
    """Normalize legal entity name by stripping PT, Tbk, Persero, and punctuation."""
    s = name.lower()
    # Strip common legal prefixes/suffixes
    s = re.sub(r"\b(pt|tbk|persero|cv|ltd|corp|inc|plc|nv)\b", "", s)
    s = re.sub(r"[^\w\s]", "", s)
    return re.sub(r"\s+", " ", s).strip().title()
