"""
Normalization package for numbers, periods, dimensions, and facts.
"""
from src.normalization.number_parser import parse_reported_number, apply_scale, grounded
from src.normalization.period_resolver import resolve_period_ref, parse_date_string
from src.normalization.normalizer import Normalizer, SilverFact, build_dim_key, make_fact_id

__all__ = [
    "parse_reported_number",
    "apply_scale",
    "grounded",
    "resolve_period_ref",
    "parse_date_string",
    "Normalizer",
    "SilverFact",
    "build_dim_key",
    "make_fact_id",
]
