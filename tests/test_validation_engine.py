"""
Unit tests for accounting validation equations.
"""
import unittest
from decimal import Decimal
from src.normalization.normalizer import SilverFact
from src.validation.engine import ValidationEngine


def create_sample_fact(concept_id: str, value: Decimal, period_end: str = "2025-12-31") -> SilverFact:
    return SilverFact(
        fact_id=f"fid_{concept_id}_{period_end}",
        issuer_id="NCKL",
        doc_id="test_doc_id",
        concept_id=concept_id,
        label_raw=concept_id,
        period_type="duration" if "is." in concept_id or "cf." in concept_id else "instant",
        period_end=period_end,
        fiscal_year=2025,
        fiscal_period="FY",
        scenario="actual",
        value=value,
        raw_text=str(value),
        scale_as_reported=1_000_000,
        knowledge_ts="2026-05-26T00:00:00",
        ingested_at="2026-05-26T00:00:00",
        lineage_page=1,
    )


class TestValidationEngine(unittest.TestCase):
    def test_validation_engine_bs_identity_passed(self):
        engine = ValidationEngine()
        facts = [
            create_sample_fact("fs.bs.total_assets", Decimal("61770000000000")),
            create_sample_fact("fs.bs.total_liabilities", Decimal("15014000000000")),
            create_sample_fact("fs.bs.total_equity", Decimal("46756000000000")),
        ]
        results = engine.validate_facts(facts)
        bs_result = next(r for r in results if r.rule_id == "bs_identity")
        self.assertTrue(bs_result.passed)
        self.assertEqual(bs_result.diff, Decimal("0"))

    def test_validation_engine_gross_profit_passed(self):
        engine = ValidationEngine()
        facts = [
            create_sample_fact("fs.is.revenue", Decimal("29632799000000")),
            create_sample_fact("fs.is.cost_of_revenue", Decimal("19941401000000")),
            create_sample_fact("fs.is.gross_profit", Decimal("9691398000000")),
        ]
        results = engine.validate_facts(facts)
        gp_result = next(r for r in results if r.rule_id == "gross_profit")
        self.assertTrue(gp_result.passed)
        self.assertEqual(gp_result.diff, Decimal("0"))

    def test_validation_sharia_temporary_syirkah_funds(self):
        engine = ValidationEngine()
        facts = [
            create_sample_fact("fs.bs.total_assets", Decimal("1586828536000000")),
            create_sample_fact("fs.bs.total_liabilities", Decimal("1294508286000000")),
            create_sample_fact("fs.bs.total_equity", Decimal("281687555000000")),
            create_sample_fact("fs.bs.sharia.temporary_syirkah_funds", Decimal("10632695000000")),
        ]
        results = engine.validate_facts(facts)
        bs_result = next(r for r in results if r.rule_id == "bs_identity")
        self.assertTrue(bs_result.passed)
        self.assertEqual(bs_result.diff, Decimal("0"))

    def test_validation_discontinued_operations(self):
        engine = ValidationEngine()
        # Profit for period = PBT - Tax + Discontinued Ops
        facts = [
            create_sample_fact("fs.is.profit_before_tax", Decimal("1000000000")),
            create_sample_fact("fs.is.income_tax_expense", Decimal("220000000")),
            create_sample_fact("fs.is.discontinued_operations", Decimal("50000000")),
            create_sample_fact("fs.is.profit_for_the_period", Decimal("830000000")),
        ]
        results = engine.validate_facts(facts)
        np_result = next(r for r in results if r.rule_id == "net_profit")
        self.assertTrue(np_result.passed)
        self.assertEqual(np_result.diff, Decimal("0"))

    def test_validation_cf_fx_effect(self):
        engine = ValidationEngine()
        # Net change cash = CFO + CFI + CFF + FX
        facts = [
            create_sample_fact("fs.cf.net_cash_operating", Decimal("500000000")),
            create_sample_fact("fs.cf.net_cash_investing", Decimal("-200000000")),
            create_sample_fact("fs.cf.net_cash_financing", Decimal("-100000000")),
            create_sample_fact("fs.cf.fx_effect", Decimal("15000000")),
            create_sample_fact("fs.cf.net_change_cash", Decimal("215000000")),
        ]
        results = engine.validate_facts(facts)
        cf_result = next(r for r in results if r.rule_id == "cf_sections")
        self.assertTrue(cf_result.passed)
        self.assertEqual(cf_result.diff, Decimal("0"))

    def test_validation_order_of_liquidity_skips_current_split(self):
        engine = ValidationEngine()
        # Banking balance sheet without current/non-current asset breakdown
        facts = [
            create_sample_fact("fs.bs.total_assets", Decimal("1000000000000")),
            create_sample_fact("fs.bs.total_liabilities", Decimal("800000000000")),
            create_sample_fact("fs.bs.total_equity", Decimal("200000000000")),
            create_sample_fact("fs.is.interest_income", Decimal("50000000000")),
        ]
        results = engine.validate_facts(facts)
        cs_result = next(r for r in results if r.rule_id == "bs_current_split")
        self.assertTrue(cs_result.passed)
        self.assertIn("order_of_liquidity", cs_result.message)

    def test_hierarchical_rollup_validation(self):
        engine = ValidationEngine()
        parent = create_sample_fact("fs.bs.total_current_assets", Decimal("1000000000"))
        children = [
            create_sample_fact("fs.bs.cash_and_equivalents", Decimal("400000000")),
            create_sample_fact("fs.bs.trade_receivables", Decimal("350000000")),
            create_sample_fact("fs.bs.inventories", Decimal("250000000")),
        ]
        rollup_res = engine.validate_hierarchical_rollup(parent, children)
        self.assertTrue(rollup_res.passed)
        self.assertEqual(rollup_res.diff, Decimal("0"))

    def test_concept_mapper_few_shots_and_persistence(self):
        import duckdb
        from src.mapping.concept_mapper import ConceptMapper
        mapper = ConceptMapper()
        shots = mapper.get_few_shot_examples(statement_type="income_statement")
        self.assertGreaterEqual(len(shots), 5)
        self.assertTrue(any(s["concept_id"] == "fs.is.revenue" for s in shots))

        # Test persistence with in-memory duckdb
        conn = duckdb.connect(":memory:")
        conn.execute("""
            CREATE SCHEMA idxfin_ref;
            CREATE TABLE idxfin_ref.label_mapping (
                label_raw VARCHAR PRIMARY KEY,
                concept_id VARCHAR NOT NULL,
                mapping_method VARCHAR,
                confidence DOUBLE,
                statement_type VARCHAR,
                updated_at TIMESTAMP
            );
        """)
        mapper.persist_mapping(conn, "beban angkut penjualan", "fs.is.operating_expenses", statement_type="income_statement")
        resolved = mapper.map_label("beban angkut penjualan")
        self.assertEqual(resolved, "fs.is.operating_expenses")

        # Create fresh mapper and verify it loads persisted mapping
        new_mapper = ConceptMapper()
        loaded = new_mapper.load_persisted_mappings(conn)
        self.assertEqual(loaded, 1)
        self.assertEqual(new_mapper.map_label("beban angkut penjualan"), "fs.is.operating_expenses")
        conn.close()

    def test_llm_anomaly_reconciler_loop(self):
        class MockLLMClient:
            def complete_json(self, prompt, system=None):
                return {
                    "has_reconciling_item": True,
                    "reconciling_concept": "fs.bs.sharia.temporary_syirkah_funds",
                    "reconciling_label": "Jumlah dana syirkah temporer",
                    "reconciling_value": 10632695000000,
                    "reconciling_sign": "+1",
                    "accounting_standard": "PSAK 101",
                    "explanation": "Temporary Syirkah Funds under PSAK 101 sit between liabilities and equity.",
                    "is_algebraically_sound": True,
                }

        class MockPage:
            text = "Jumlah liabilitas: 1,294,508,286,000,000\nJumlah dana syirkah temporer: 10,632,695,000,000\nJumlah ekuitas: 281,687,555,000,000\nJumlah aset: 1,586,828,536,000,000"

        class MockPDFDoc:
            page_count = 10
            scale = 1_000_000
            def get_page(self, p):
                return MockPage()

        engine = ValidationEngine()
        # Missing syirkah fact from raw facts -> creates 10.63T variance
        facts = [
            create_sample_fact("fs.bs.total_assets", Decimal("1586828536000000")),
            create_sample_fact("fs.bs.total_liabilities", Decimal("1294508286000000")),
            create_sample_fact("fs.bs.total_equity", Decimal("281687555000000")),
        ]

        # Validate with mock LLM client
        results = engine.validate_facts(facts, doc=MockPDFDoc(), llm_client=MockLLMClient())
        bs_result = next(r for r in results if r.rule_id == "bs_identity")
        self.assertTrue(bs_result.passed)
        self.assertEqual(bs_result.diff, Decimal("0"))


if __name__ == "__main__":
    unittest.main()
