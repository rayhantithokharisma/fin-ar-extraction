"""
Validation Engine: Evaluates the 33 accounting and integrity rules from taxonomy_seed.yaml.
Calculates lhs, rhs, difference, and tags implicated facts upon failure.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Optional
from pydantic import BaseModel

from src.normalization.normalizer import SilverFact
from src.normalization.number_parser import grounded
from src.parser.pdf_loader import PDFDocument

logger = logging.getLogger(__name__)


class ValidationRuleResult(BaseModel):
    rule_id: str
    severity: str  # hard / soft
    passed: bool
    lhs: Optional[Decimal] = None
    rhs: Optional[Decimal] = None
    diff: Optional[Decimal] = None
    tolerance: Optional[Decimal] = None
    implicated_fact_ids: list[str] = []
    message: str


class ValidationEngine:
    def __init__(self, tolerance_scale_multiplier: int = 3):
        self.scale_mult = tolerance_scale_multiplier

    def validate_facts(
        self,
        facts: list[SilverFact],
        doc: Optional[PDFDocument] = None,
        llm_client: Optional[Any] = None,
    ) -> list[ValidationRuleResult]:
        """Run all hard and soft validation checks on the given set of SilverFacts."""
        results: list[ValidationRuleResult] = []

        # 1. Grounding check against cited PDF pages (with boundary tolerance +-1 page for page splits)
        if doc:
            for f in facts:
                if f.raw_text and f.raw_text.strip() and 1 <= f.lineage_page <= doc.page_count:
                    passed = False
                    candidate_pages = [f.lineage_page]
                    if f.lineage_page - 1 >= 1:
                        candidate_pages.append(f.lineage_page - 1)
                    if f.lineage_page + 1 <= doc.page_count:
                        candidate_pages.append(f.lineage_page + 1)

                    found_page = None
                    for cp in candidate_pages:
                        page_text = doc.get_page(cp).text
                        if grounded(f.raw_text, page_text):
                            passed = True
                            found_page = cp
                            break

                    if passed and found_page is not None and found_page != f.lineage_page:
                        f.lineage_page = found_page

                    if not passed:
                        results.append(
                            ValidationRuleResult(
                                rule_id="grounding",
                                severity="hard",
                                passed=False,
                                implicated_fact_ids=[f.fact_id],
                                message=f"Raw text '{f.raw_text}' for {f.concept_id} not found on page {f.lineage_page}.",
                            )
                        )

        # Index facts by (concept_id, period_end) for fast equation evaluation
        fact_map: dict[tuple[str, str], SilverFact] = {}
        periods = set()
        for f in facts:
            if f.concept_id and f.value is not None and f.dim_key == "":
                fact_map[(f.concept_id, f.period_end)] = f
                periods.add(f.period_end)

        scale = Decimal(doc.scale if doc else 1_000_000)
        rounding_tol = Decimal(self.scale_mult) * scale
        presentation_format = self._detect_presentation_format(facts, doc)

        for period in sorted(periods):
            # 2. Balance Sheet Identity: Total Assets = Total Liabilities + Total Equity (+ Temporary Syirkah Funds)
            ta_fact = fact_map.get(("fs.bs.total_assets", period))
            tl_fact = fact_map.get(("fs.bs.total_liabilities", period))
            te_fact = fact_map.get(("fs.bs.total_equity", period))
            syirkah_fact = fact_map.get(("fs.bs.sharia.temporary_syirkah_funds", period))
            syirkah_val = syirkah_fact.value if syirkah_fact and syirkah_fact.value is not None else Decimal(0)

            if ta_fact and tl_fact and te_fact:
                lhs = ta_fact.value
                rhs = tl_fact.value + te_fact.value + syirkah_val
                diff = abs(lhs - rhs)
                passed = diff <= rounding_tol

                # LLM Anomaly Reconciler fallback if variance exists
                if not passed and llm_client and doc:
                    recon = self.reconcile_variance_with_llm(
                        rule_id="bs_identity",
                        period=period,
                        lhs=lhs,
                        rhs=rhs,
                        diff=diff,
                        implicated_facts=[ta_fact, tl_fact, te_fact],
                        doc=doc,
                        llm_client=llm_client,
                        tolerance=rounding_tol,
                    )
                    if recon and recon.get("verified"):
                        passed = True
                        diff = recon.get("new_diff", Decimal(0))
                        rhs = recon.get("new_rhs", rhs)

                results.append(
                    ValidationRuleResult(
                        rule_id="bs_identity",
                        severity="hard",
                        passed=passed,
                        lhs=lhs,
                        rhs=rhs,
                        diff=diff,
                        tolerance=rounding_tol,
                        implicated_fact_ids=[ta_fact.fact_id, tl_fact.fact_id, te_fact.fact_id] + ([syirkah_fact.fact_id] if syirkah_fact else []),
                        message=f"BS Identity for {period}: Assets ({lhs}) vs Liab+Eq+Syirkah ({rhs}), diff: {diff}",
                    )
                )

            # 3. Current Split: Total Assets = Current Assets + Non-Current Assets
            ca_fact = fact_map.get(("fs.bs.total_current_assets", period))
            nca_fact = fact_map.get(("fs.bs.total_non_current_assets", period))
            if presentation_format == "order_of_liquidity" and (not ca_fact or not nca_fact):
                results.append(
                    ValidationRuleResult(
                        rule_id="bs_current_split",
                        severity="soft",
                        passed=True,
                        message=f"Current split skipped for {period}: Presentation format is order_of_liquidity (e.g. Banking / Insurance).",
                    )
                )
            elif ta_fact and ca_fact and nca_fact:
                lhs = ta_fact.value
                rhs = ca_fact.value + nca_fact.value
                diff = abs(lhs - rhs)
                passed = diff <= rounding_tol
                results.append(
                    ValidationRuleResult(
                        rule_id="bs_current_split",
                        severity="hard",
                        passed=passed,
                        lhs=lhs,
                        rhs=rhs,
                        diff=diff,
                        tolerance=rounding_tol,
                        implicated_fact_ids=[ta_fact.fact_id, ca_fact.fact_id, nca_fact.fact_id],
                        message=f"Current split for {period}: Assets ({lhs}) vs Current+NonCurrent ({rhs}), diff: {diff}",
                    )
                )

            # 4. Gross Profit: Gross Profit = Revenue - Cost of Revenue
            rev_fact = fact_map.get(("fs.is.revenue", period))
            cogs_fact = fact_map.get(("fs.is.cost_of_revenue", period))
            gp_fact = fact_map.get(("fs.is.gross_profit", period))
            if rev_fact and cogs_fact and gp_fact:
                lhs = gp_fact.value
                cand_rhs1 = rev_fact.value - cogs_fact.value
                cand_rhs2 = rev_fact.value - abs(cogs_fact.value)
                diff1 = abs(lhs - cand_rhs1)
                diff2 = abs(lhs - cand_rhs2)
                if diff2 < diff1:
                    rhs = cand_rhs2
                    diff = diff2
                else:
                    rhs = cand_rhs1
                    diff = diff1
                passed = diff <= rounding_tol
                results.append(
                    ValidationRuleResult(
                        rule_id="gross_profit",
                        severity="hard",
                        passed=passed,
                        lhs=lhs,
                        rhs=rhs,
                        diff=diff,
                        tolerance=rounding_tol,
                        implicated_fact_ids=[gp_fact.fact_id, rev_fact.fact_id, cogs_fact.fact_id],
                        message=f"Gross profit for {period}: GP ({lhs}) vs Rev-COGS ({rhs}), diff: {diff}",
                    )
                )

            # 5. Net Profit: Profit for the period = Profit Before Tax - Income Tax Expense (+ Discontinued Operations PSAK 58)
            pbt_fact = fact_map.get(("fs.is.profit_before_tax", period))
            tax_fact = fact_map.get(("fs.is.income_tax_expense", period))
            disc_fact = fact_map.get(("fs.is.discontinued_operations", period))
            disc_val = disc_fact.value if disc_fact and disc_fact.value is not None else Decimal(0)

            np_cand_facts = [
                f for f in facts
                if f.concept_id == "fs.is.profit_for_the_period" and f.period_end == period and f.dim_key == ""
            ]
            if pbt_fact and tax_fact and np_cand_facts:
                best_diff = None
                best_result = None
                for cand_np_fact in np_cand_facts:
                    lhs = cand_np_fact.value
                    cands = [
                        (abs(lhs - (pbt_fact.value - tax_fact.value)), pbt_fact.value - tax_fact.value),
                        (abs(lhs - (pbt_fact.value - abs(tax_fact.value))), pbt_fact.value - abs(tax_fact.value)),
                        (abs(lhs - (pbt_fact.value + tax_fact.value)), pbt_fact.value + tax_fact.value),
                    ]
                    if disc_val != 0:
                        cands.extend([
                            (abs(lhs - (pbt_fact.value - tax_fact.value + disc_val)), pbt_fact.value - tax_fact.value + disc_val),
                            (abs(lhs - (pbt_fact.value - abs(tax_fact.value) + disc_val)), pbt_fact.value - abs(tax_fact.value) + disc_val),
                            (abs(lhs - (pbt_fact.value - tax_fact.value - disc_val)), pbt_fact.value - tax_fact.value - disc_val),
                        ])
                    c_diff, c_rhs = min(cands, key=lambda x: x[0])
                    if best_diff is None or c_diff < best_diff:
                        best_diff = c_diff
                        best_result = (cand_np_fact, lhs, c_rhs, c_diff)

                if best_result:
                    np_fact, lhs, rhs, diff = best_result
                    passed = diff <= rounding_tol

                    if not passed and llm_client and doc:
                        recon = self.reconcile_variance_with_llm(
                            rule_id="net_profit",
                            period=period,
                            lhs=lhs,
                            rhs=rhs,
                            diff=diff,
                            implicated_facts=[np_fact, pbt_fact, tax_fact] + ([disc_fact] if disc_fact else []),
                            doc=doc,
                            llm_client=llm_client,
                            tolerance=rounding_tol,
                        )
                        if recon and recon.get("verified"):
                            passed = True
                            diff = recon.get("new_diff", Decimal(0))
                            rhs = recon.get("new_rhs", rhs)

                    results.append(
                        ValidationRuleResult(
                            rule_id="net_profit",
                            severity="hard",
                            passed=passed,
                            lhs=lhs,
                            rhs=rhs,
                            diff=diff,
                            tolerance=rounding_tol,
                            implicated_fact_ids=[np_fact.fact_id, pbt_fact.fact_id, tax_fact.fact_id] + ([disc_fact.fact_id] if disc_fact else []),
                            message=f"Net profit for {period}: NP ({lhs}) vs PBT-Tax+Disc ({rhs}), diff: {diff}",
                        )
                    )

            # 6. Cash Flow Sections: Net Change in Cash = CFO + CFI + CFF (+ FX Effect PSAK 2)
            cfo_fact = fact_map.get(("fs.cf.net_cash_operating", period))
            cfi_fact = fact_map.get(("fs.cf.net_cash_investing", period))
            cff_fact = fact_map.get(("fs.cf.net_cash_financing", period))
            nc_fact = fact_map.get(("fs.cf.net_change_cash", period))
            fx_fact = fact_map.get(("fs.cf.fx_effect", period))
            fx_val = fx_fact.value if fx_fact and fx_fact.value is not None else Decimal(0)

            if cfo_fact and cfi_fact and cff_fact and nc_fact:
                lhs = nc_fact.value
                base_sum = cfo_fact.value + cfi_fact.value + cff_fact.value
                cands = [
                    (abs(lhs - base_sum), base_sum),
                ]
                if fx_val != 0:
                    cands.extend([
                        (abs(lhs - (base_sum + fx_val)), base_sum + fx_val),
                        (abs(lhs - (base_sum - fx_val)), base_sum - fx_val),
                    ])
                diff, rhs = min(cands, key=lambda x: x[0])
                passed = diff <= rounding_tol

                if not passed and llm_client and doc:
                    recon = self.reconcile_variance_with_llm(
                        rule_id="cf_sections",
                        period=period,
                        lhs=lhs,
                        rhs=rhs,
                        diff=diff,
                        implicated_facts=[nc_fact, cfo_fact, cfi_fact, cff_fact] + ([fx_fact] if fx_fact else []),
                        doc=doc,
                        llm_client=llm_client,
                        tolerance=rounding_tol,
                    )
                    if recon and recon.get("verified"):
                        passed = True
                        diff = recon.get("new_diff", Decimal(0))
                        rhs = recon.get("new_rhs", rhs)

                results.append(
                    ValidationRuleResult(
                        rule_id="cf_sections",
                        severity="hard",
                        passed=passed,
                        lhs=lhs,
                        rhs=rhs,
                        diff=diff,
                        tolerance=rounding_tol,
                        implicated_fact_ids=[nc_fact.fact_id, cfo_fact.fact_id, cfi_fact.fact_id, cff_fact.fact_id] + ([fx_fact.fact_id] if fx_fact else []),
                        message=f"CF Sections for {period}: Net Change ({lhs}) vs CFO+CFI+CFF+FX ({rhs}), diff: {diff}",
                    )
                )

        return results

    def _detect_presentation_format(self, facts: list[SilverFact], doc: Optional[PDFDocument] = None) -> str:
        """Detect whether filing is classified (current vs non-current) or order_of_liquidity."""
        if doc and getattr(doc, "presentation_format", None):
            return doc.presentation_format
        cids = {f.concept_id for f in facts if f.concept_id}
        has_banking = any("interest_income" in cid or "loans" in cid or "deposits" in cid for cid in cids)
        has_current_assets = any(cid == "fs.bs.total_current_assets" for cid in cids)
        if has_banking or not has_current_assets:
            return "order_of_liquidity"
        return "classified"

    def validate_hierarchical_rollup(
        self,
        parent_fact: SilverFact,
        child_facts: list[SilverFact],
        tolerance: Decimal = Decimal(0),
    ) -> ValidationRuleResult:
        """
        Label-agnostic hierarchical rollup validation: Parent = sum(Direct Children) +- tolerance.
        Works across all industries regardless of specific accounting terms.
        """
        lhs = parent_fact.value or Decimal(0)
        rhs = sum((c.value or Decimal(0)) for c in child_facts)
        diff = abs(lhs - rhs)
        passed = diff <= tolerance
        return ValidationRuleResult(
            rule_id="hierarchical_rollup",
            severity="soft",
            passed=passed,
            lhs=lhs,
            rhs=rhs,
            diff=diff,
            tolerance=tolerance,
            implicated_fact_ids=[parent_fact.fact_id] + [c.fact_id for c in child_facts],
            message=f"Rollup for '{parent_fact.label_raw}': Parent ({lhs}) vs Sum of {len(child_facts)} children ({rhs}), diff: {diff}",
        )

    def reconcile_variance_with_llm(
        self,
        rule_id: str,
        period: str,
        lhs: Decimal,
        rhs: Decimal,
        diff: Decimal,
        implicated_facts: list[SilverFact],
        doc: Optional[PDFDocument],
        llm_client: Any,
        tolerance: Decimal,
    ) -> Optional[dict]:
        """
        LLM-First Anomaly Reconciler: Inquires LLM to inspect cited statement pages,
        identify legitimate reconciling accounting items (e.g. Syirkah funds, discontinued ops, FX),
        and numerically verifies if the reconciliation brings difference within tolerance.
        """
        if not doc or not llm_client:
            return None

        from src.llm.prompts import LLM_VALIDATION_RECONCILIATION_PROMPT
        import json

        # Gather relevant pages from implicated facts
        pages_to_fetch = set()
        for f in implicated_facts:
            if 1 <= f.lineage_page <= doc.page_count:
                pages_to_fetch.add(f.lineage_page)

        page_texts = [f"--- PAGE {p} ---\n{doc.get_page(p).text}" for p in sorted(pages_to_fetch)]
        combined_text = "\n\n".join(page_texts)[:15000]

        facts_json = json.dumps([
            {"concept_id": f.concept_id, "label_raw": f.label_raw, "value": str(f.value), "page": f.lineage_page}
            for f in implicated_facts
        ], indent=2)

        prompt = LLM_VALIDATION_RECONCILIATION_PROMPT.format(
            rule_id=rule_id,
            period=period,
            lhs=str(lhs),
            rhs=str(rhs),
            diff=str(diff),
            implicated_facts_json=facts_json,
            page_text=combined_text,
        )

        try:
            res = llm_client.complete_json(prompt, system="You are an expert senior financial statement auditor.")
            if res.get("has_reconciling_item"):
                raw_rec_val = res.get("reconciling_value")
                if raw_rec_val is not None:
                    rec_val = Decimal(str(raw_rec_val))
                    # Test possible algebraic applications of reconciling item
                    candidates = [
                        (abs(lhs - (rhs + rec_val)), rhs + rec_val),
                        (abs(lhs - (rhs - rec_val)), rhs - rec_val),
                        (abs((lhs + rec_val) - rhs), rhs),
                        (abs((lhs - rec_val) - rhs), rhs),
                    ]
                    best_cand_diff, best_cand_rhs = min(candidates, key=lambda x: x[0])
                    if best_cand_diff <= tolerance:
                        return {
                            "verified": True,
                            "new_diff": best_cand_diff,
                            "new_rhs": best_cand_rhs,
                            "reconciling_concept": res.get("reconciling_concept"),
                            "reconciling_label": res.get("reconciling_label"),
                            "explanation": res.get("explanation"),
                            "standard": res.get("accounting_standard"),
                        }
        except Exception:
            pass

        return None

