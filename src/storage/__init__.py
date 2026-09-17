"""
Storage package for DuckDB database management and Gold views.
"""
from src.storage.database import DatabaseStore
from src.storage.gold_views import create_gold_views

__all__ = ["DatabaseStore", "create_gold_views"]
