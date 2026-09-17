"""
Deterministic Table and Line-Item Parser for Structured IDX Filing Pages.
Parses face statements into lines, values, and note references.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from src.normalization.number_parser import parse_reported_number


@dataclass
class ParsedRow:
    label_id: str
    label_en: Optional[str]
    values: list[str]
    note_ref: Optional[str] = None
    indent_level: int = 0
    is_total: bool = False


class StructuredTableParser:
    """Extracts structured rows from text of standardized IDX electronic LK pages."""

    def __init__(self, locale: str = "en"):
        self.locale = locale

    def parse_page_lines(self, text: str) -> list[ParsedRow]:
        """
        Extract line items from standardized IDX statement page text.
        Structure typically follows:
        [Indonesian label]
        [Value column 1]
        [Value column 2]
        [English label]
        """
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        rows: list[ParsedRow] = []

        # Find header periods
        # e.g., 31 December 2025, 31 December 2024
        i = 0
        while i < len(lines):
            line = lines[i]

            # Skip section header tags like [1210000]
            if line.startswith("[") and "]" in line:
                i += 1
                continue

            # Look for line with numbers following
            # Check if next 1 or 2 lines are numbers
            if i + 2 < len(lines):
                val1 = lines[i + 1]
                val2 = lines[i + 2] if i + 2 < len(lines) else ""
                num1 = parse_reported_number(val1, self.locale)
                num2 = parse_reported_number(val2, self.locale)

                if num1 is not None and num2 is not None:
                    # Potential line item
                    label_id = line
                    val_list = [val1, val2]
                    label_en = lines[i + 3] if i + 3 < len(lines) else None
                    is_tot = any(kw in label_id.lower() for kw in ["jumlah", "total", "laba bruto", "gross profit"])
                    indent = 1 if not is_tot and not line.isupper() else 0

                    rows.append(
                        ParsedRow(
                            label_id=label_id,
                            label_en=label_en,
                            values=val_list,
                            indent_level=indent,
                            is_total=is_tot,
                        )
                    )
                    i += 4
                    continue

            i += 1

        return rows
