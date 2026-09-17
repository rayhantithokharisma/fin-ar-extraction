"""
Period Resolver: Normalizes dates, duration vs instant, and cumulative interim periods.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Optional, Tuple
import schema_design.extraction_contracts as contracts

MONTH_MAP = {
    "januari": 1, "january": 1, "jan": 1,
    "februari": 2, "february": 2, "feb": 2,
    "maret": 3, "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "mei": 5, "may": 5,
    "juni": 6, "june": 6, "jun": 6,
    "juli": 7, "july": 7, "jul": 7,
    "agustus": 8, "august": 8, "aug": 8,
    "september": 9, "sep": 9,
    "oktober": 10, "october": 10, "okt": 10, "oct": 10,
    "november": 11, "nov": 11,
    "desember": 12, "december": 12, "des": 12, "dec": 12,
}


def parse_date_string(s: str) -> Optional[date]:
    """Parse dates like '31 Desember 2025', 'December 31, 2024', '31/12/2025', '2025-12-31'."""
    s = s.strip().lower()
    # YYYY-MM-DD
    m_iso = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m_iso:
        return date(int(m_iso.group(1)), int(m_iso.group(2)), int(m_iso.group(3)))

    # DD/MM/YYYY
    m_slash = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m_slash:
        return date(int(m_slash.group(3)), int(m_slash.group(2)), int(m_slash.group(1)))

    # DD Month YYYY
    m_word = re.search(r"(\d{1,2})\s+([a-z]+)\s+(\d{4})", s)
    if m_word:
        day = int(m_word.group(1))
        m_str = m_word.group(2)
        year = int(m_word.group(3))
        month = MONTH_MAP.get(m_str)
        if month:
            return date(year, month, day)

    # Month DD, YYYY
    m_word_en = re.search(r"([a-z]+)\s+(\d{1,2}),?\s+(\d{4})", s)
    if m_word_en:
        m_str = m_word_en.group(1)
        day = int(m_word_en.group(2))
        year = int(m_word_en.group(3))
        month = MONTH_MAP.get(m_str)
        if month:
            return date(year, month, day)

    return None


def resolve_period_ref(
    column_label_raw: str,
    period_type: str | contracts.PeriodType,
    statement_fiscal_year: int = 2025,
    statement_fiscal_period: str = "FY",
) -> contracts.PeriodRef:
    """
    Build a validated PeriodRef from a column header string.
    Handles '31 December 2025', '30 September 2025 (9 bulan)', 'Disajikan kembali'.
    """
    pt = contracts.PeriodType(period_type)
    is_restated = any(kw in column_label_raw.lower() for kw in ["disajikan kembali", "as restated", "restated"])

    end_d = parse_date_string(column_label_raw)
    if not end_d:
        # Fallback to statement end date
        end_d = date(statement_fiscal_year, 12, 31)

    f_year = end_d.year
    # Detect months & fiscal period
    if end_d.month == 3:
        f_period = contracts.FiscalPeriod.Q1
        months = 3
    elif end_d.month == 6:
        f_period = contracts.FiscalPeriod.H1
        months = 6
    elif end_d.month == 9:
        f_period = contracts.FiscalPeriod.M9
        months = 9
    else:
        f_period = contracts.FiscalPeriod.FY
        months = 12

    if pt == contracts.PeriodType.INSTANT:
        start_d = None
        is_cum = True
    else:
        # Duration: start date is beginning of the fiscal year for cumulative YTD periods
        start_d = date(f_year, 1, 1)
        is_cum = True

    return contracts.PeriodRef(
        column_label_raw=column_label_raw,
        period_type=pt,
        start_date=start_d,
        end_date=end_d,
        fiscal_year=f_year,
        fiscal_period=f_period,
        is_cumulative_ytd=is_cum,
        is_restated=is_restated,
        assurance=contracts.Assurance.AUDITED if f_period == contracts.FiscalPeriod.FY else contracts.Assurance.UNAUDITED,
    )
