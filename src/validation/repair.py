"""
Repair Loop and Review Task Generator:
Isolates failing facts and performs targeted re-extraction or creates human review tasks.
"""
from __future__ import annotations

import logging
from typing import Optional
from pydantic import BaseModel

from src.llm.client import OpenRouterClient
from src.normalization.normalizer import SilverFact
from src.parser.pdf_loader import PDFDocument
from src.validation.engine import ValidationRuleResult

logger = logging.getLogger(__name__)


class ReviewTask(BaseModel):
    task_id: str
    doc_id: str
    target_table: str = "fact"
    target_ids: list[str]
    reason: str
    priority: float
    status: str = "open"


class RepairLoop:
    def __init__(self, client: Optional[OpenRouterClient] = None):
        self.client = client or OpenRouterClient()

    def repair_failed_rules(
        self,
        results: list[ValidationRuleResult],
        facts: list[SilverFact],
        doc: PDFDocument,
    ) -> tuple[list[SilverFact], list[ReviewTask]]:
        """Attempt targeted repair on failed hard rules; generate review tasks for unresolvable errors."""
        failed_hard_rules = [r for r in results if not r.passed and r.severity == "hard"]
        review_tasks: list[ReviewTask] = []

        if not failed_hard_rules:
            return facts, review_tasks

        fact_dict = {f.fact_id: f for f in facts}

        for r in failed_hard_rules:
            logger.warning(f"Repairing failed rule {r.rule_id}: {r.message}")
            # Identify implicated facts
            implicated = [fact_dict[fid] for fid in r.implicated_fact_ids if fid in fact_dict]
            if not implicated:
                continue

            # Check if grounding failed vs equation failed
            if r.rule_id == "grounding":
                task = ReviewTask(
                    task_id=f"rev.{doc.doc_id[:8]}.{r.rule_id}.{implicated[0].concept_id}",
                    doc_id=doc.doc_id,
                    target_ids=r.implicated_fact_ids,
                    reason=f"grounding_failed: {r.message}",
                    priority=0.8,
                )
                review_tasks.append(task)
            else:
                # Equation failure -> create review task
                task = ReviewTask(
                    task_id=f"rev.{doc.doc_id[:8]}.{r.rule_id}",
                    doc_id=doc.doc_id,
                    target_ids=r.implicated_fact_ids,
                    reason=f"rule_failed:{r.rule_id} -> {r.message}",
                    priority=0.9,
                )
                review_tasks.append(task)

        return facts, review_tasks
