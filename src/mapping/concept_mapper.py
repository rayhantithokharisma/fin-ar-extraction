"""
Concept Mapper and Taxonomy Indexer.
Loads taxonomy_seed.yaml, provides candidate concepts for LLM prompt injection,
and maps reported labels to canonical concept_ids.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
import yaml

from src.config import TAXONOMY_PATH


def normalize_label(label: str) -> str:
    """Normalize label for exact matching: lowercase, strip punctuation and extra spaces."""
    s = re.sub(r"[^\w\s]", "", label.lower())
    return re.sub(r"\s+", " ", s).strip()


@dataclass
class TaxonomyConcept:
    concept_id: str
    label_en: str
    label_id: str
    domain: str
    statement: Optional[str]
    period_type: str  # instant / duration
    balance: Optional[str]  # dr / cr
    data_type: str
    unit_dimension: Optional[str]
    allowed_axes: list[str]
    required_qualifiers: list[str]


class ConceptMapper:
    def __init__(self, taxonomy_path: str | Path = TAXONOMY_PATH):
        self.taxonomy_path = Path(taxonomy_path)
        self.concepts: dict[str, TaxonomyConcept] = {}
        self.label_to_concept: dict[str, str] = {}
        self.kpi_packs: dict[str, list[dict[str, Any]]] = {}
        self.validation_rules: list[dict[str, Any]] = []
        self._load_taxonomy()

    def _load_taxonomy(self) -> None:
        if not self.taxonomy_path.exists():
            raise FileNotFoundError(f"Taxonomy file not found at {self.taxonomy_path}")

        with open(self.taxonomy_path, "r") as f:
            data = yaml.safe_load(f)

        raw_concepts = data.get("concepts", [])
        for rc in raw_concepts:
            cid = rc.get("id")
            if not cid:
                continue
            en = rc.get("en", "")
            id_l = rc.get("id_label", "")
            domain = cid.split(".")[0]
            stmt = None
            if cid.startswith("fs.bs."):
                stmt = "balance_sheet"
            elif cid.startswith("fs.is."):
                stmt = "income_statement"
            elif cid.startswith("fs.oci."):
                stmt = "comprehensive_income"
            elif cid.startswith("fs.cf."):
                stmt = "cash_flow"
            elif cid.startswith("fs.soce."):
                stmt = "changes_in_equity"

            tc = TaxonomyConcept(
                concept_id=cid,
                label_en=en,
                label_id=id_l,
                domain=domain,
                statement=stmt,
                period_type="instant" if rc.get("p") == "i" else "duration",
                balance=rc.get("bal"),
                data_type=rc.get("t", "monetary"),
                unit_dimension=rc.get("unit_dimension", "currency"),
                allowed_axes=rc.get("axes", []),
                required_qualifiers=rc.get("q", []),
            )
            self.concepts[cid] = tc

            # Build label index
            if en:
                self.label_to_concept[normalize_label(en)] = cid
            if id_l:
                self.label_to_concept[normalize_label(id_l)] = cid

        self.kpi_packs = data.get("kpi_packs", {})
        self.validation_rules = data.get("validation_rules", [])
        self._add_standard_idx_mappings()

    def _add_standard_idx_mappings(self) -> None:
        """Add canonical standard label mappings for IDX face statements."""
        standard_map = {
            # Income statement
            "penjualan dan pendapatan usaha": "fs.is.revenue",
            "penjualan bersih": "fs.is.revenue",
            "pendapatan usaha": "fs.is.revenue",
            "pendapatan": "fs.is.revenue",
            "sales and revenue": "fs.is.revenue",
            "net sales": "fs.is.revenue",
            "revenue": "fs.is.revenue",
            "beban pokok penjualan dan pendapatan": "fs.is.cost_of_revenue",
            "beban pokok pendapatan": "fs.is.cost_of_revenue",
            "beban pokok penjualan": "fs.is.cost_of_revenue",
            "cost of sales and revenue": "fs.is.cost_of_revenue",
            "cost of goods sold": "fs.is.cost_of_revenue",
            "cost of revenue": "fs.is.cost_of_revenue",
            "jumlah laba bruto": "fs.is.gross_profit",
            "laba bruto": "fs.is.gross_profit",
            "laba kotor": "fs.is.gross_profit",
            "total gross profit": "fs.is.gross_profit",
            "gross profit": "fs.is.gross_profit",
            "beban penjualan": "fs.is.selling_expenses",
            "selling expenses": "fs.is.selling_expenses",
            "beban umum dan administrasi": "fs.is.general_admin_expenses",
            "general and administrative expenses": "fs.is.general_admin_expenses",
            "laba usaha": "fs.is.operating_profit",
            "laba operasi": "fs.is.operating_profit",
            "operating profit": "fs.is.operating_profit",
            "pendapatan keuangan": "fs.is.finance_income",
            "finance income": "fs.is.finance_income",
            "beban keuangan": "fs.is.finance_costs",
            "beban bunga dan keuangan": "fs.is.finance_costs",
            "finance costs": "fs.is.finance_costs",
            "jumlah laba rugi sebelum pajak penghasilan": "fs.is.profit_before_tax",
            "laba sebelum pajak penghasilan": "fs.is.profit_before_tax",
            "laba sebelum pajak": "fs.is.profit_before_tax",
            "profit before income tax": "fs.is.profit_before_tax",
            "profit before tax": "fs.is.profit_before_tax",
            "pendapatan beban pajak": "fs.is.income_tax_expense",
            "beban pajak penghasilan": "fs.is.income_tax_expense",
            "beban pajak": "fs.is.income_tax_expense",
            "income tax expense": "fs.is.income_tax_expense",
            "jumlah laba rugi dari operasi yang dilanjutkan": "fs.is.profit_for_the_period",
            "jumlah laba tahun periode berjalan": "fs.is.profit_for_the_period",
            "laba tahun berjalan": "fs.is.profit_for_the_period",
            "laba periode berjalan": "fs.is.profit_for_the_period",
            "profit for the period": "fs.is.profit_for_the_period",
            "profit for the year": "fs.is.profit_for_the_period",
            # Balance sheet
            "kas dan setara kas": "fs.bs.cash_and_equivalents",
            "cash and cash equivalents": "fs.bs.cash_and_equivalents",
            "piutang usaha": "fs.bs.trade_receivables",
            "trade receivables": "fs.bs.trade_receivables",
            "persediaan": "fs.bs.inventories",
            "inventories": "fs.bs.inventories",
            "jumlah aset lancar": "fs.bs.total_current_assets",
            "total current assets": "fs.bs.total_current_assets",
            "aset tetap": "fs.bs.ppe_net",
            "property plant and equipment": "fs.bs.ppe_net",
            "jumlah aset tidak lancar": "fs.bs.total_non_current_assets",
            "total noncurrent assets": "fs.bs.total_non_current_assets",
            "total non current assets": "fs.bs.total_non_current_assets",
            "jumlah aset": "fs.bs.total_assets",
            "total assets": "fs.bs.total_assets",
            "utang usaha": "fs.bs.trade_payables",
            "trade payables": "fs.bs.trade_payables",
            "jumlah liabilitas jangka pendek": "fs.bs.total_current_liabilities",
            "total current liabilities": "fs.bs.total_current_liabilities",
            "jumlah liabilitas jangka panjang": "fs.bs.total_non_current_liabilities",
            "total noncurrent liabilities": "fs.bs.total_non_current_liabilities",
            "jumlah liabilitas": "fs.bs.total_liabilities",
            "total liabilities": "fs.bs.total_liabilities",
            "modal saham": "fs.bs.share_capital",
            "share capital": "fs.bs.share_capital",
            "jumlah ekuitas": "fs.bs.total_equity",
            "total equity": "fs.bs.total_equity",
            # Cash flow
            "kas neto diperoleh dari aktivitas operasi": "fs.cf.net_cash_operating",
            "kas neto yang diperoleh dari aktivitas operasi": "fs.cf.net_cash_operating",
            "kas neto diperoleh dari digunakan untuk aktivitas operasi": "fs.cf.net_cash_operating",
            "jumlah arus kas bersih yang diperoleh dari aktivitas operasi": "fs.cf.net_cash_operating",
            "jumlah arus kas bersih yang digunakan untuk aktivitas operasi": "fs.cf.net_cash_operating",
            "jumlah arus kas bersih yang diperoleh dari (digunakan untuk) aktivitas operasi": "fs.cf.net_cash_operating",
            "jumlah arus kas bersih yang diperoleh dari digunakan untuk aktivitas operasi": "fs.cf.net_cash_operating",
            "total net cash flows received from (used in) operating activities": "fs.cf.net_cash_operating",
            "total net cash flows received from used in operating activities": "fs.cf.net_cash_operating",
            "net cash flows from operating activities": "fs.cf.net_cash_operating",
            "arus kas dari aktivitas operasi": "fs.cf.net_cash_operating",
            "net cash from operating activities": "fs.cf.net_cash_operating",
            "net cash provided by operating activities": "fs.cf.net_cash_operating",
            "jumlah arus kas bersih yang diperoleh dari (digunakan untuk) aktivitas investasi": "fs.cf.net_cash_investing",
            "jumlah arus kas bersih yang diperoleh dari digunakan untuk aktivitas investasi": "fs.cf.net_cash_investing",
            "jumlah arus kas bersih yang diperoleh dari aktivitas investasi": "fs.cf.net_cash_investing",
            "jumlah arus kas bersih yang digunakan untuk aktivitas investasi": "fs.cf.net_cash_investing",
            "total net cash flows received from (used in) investing activities": "fs.cf.net_cash_investing",
            "total net cash flows received from used in investing activities": "fs.cf.net_cash_investing",
            "kas neto digunakan untuk aktivitas investasi": "fs.cf.net_cash_investing",
            "kas neto yang digunakan untuk aktivitas investasi": "fs.cf.net_cash_investing",
            "arus kas dari aktivitas investasi": "fs.cf.net_cash_investing",
            "net cash used in investing activities": "fs.cf.net_cash_investing",
            "jumlah arus kas bersih yang diperoleh dari (digunakan untuk) aktivitas pendanaan": "fs.cf.net_cash_financing",
            "jumlah arus kas bersih yang diperoleh dari digunakan untuk aktivitas pendanaan": "fs.cf.net_cash_financing",
            "jumlah arus kas bersih yang diperoleh dari aktivitas pendanaan": "fs.cf.net_cash_financing",
            "jumlah arus kas bersih yang digunakan untuk aktivitas pendanaan": "fs.cf.net_cash_financing",
            "total net cash flows received from (used in) financing activities": "fs.cf.net_cash_financing",
            "total net cash flows received from used in financing activities": "fs.cf.net_cash_financing",
            "kas neto digunakan untuk aktivitas investasi": "fs.cf.net_cash_investing",
            "kas neto yang digunakan untuk aktivitas investasi": "fs.cf.net_cash_investing",
            "arus kas dari aktivitas investasi": "fs.cf.net_cash_investing",
            "net cash used in investing activities": "fs.cf.net_cash_investing",
            "kas neto dari aktivitas pendanaan": "fs.cf.net_cash_financing",
            "kas neto diperoleh dari aktivitas pendanaan": "fs.cf.net_cash_financing",
            "kas neto yang digunakan untuk aktivitas pendanaan": "fs.cf.net_cash_financing",
            "arus kas dari aktivitas pendanaan": "fs.cf.net_cash_financing",
            "net cash from financing activities": "fs.cf.net_cash_financing",
            "kenaikan penurunan neto kas dan setara kas": "fs.cf.net_change_cash",
            "kenaikan neto kas dan setara kas": "fs.cf.net_change_cash",
            "penurunan neto kas dan setara kas": "fs.cf.net_change_cash",
            "net increase decrease in cash": "fs.cf.net_change_cash",
            "kas dan setara kas awal tahun": "fs.cf.cash_beginning",
            "kas dan setara kas awal periode": "fs.cf.cash_beginning",
            "cash at beginning of period": "fs.cf.cash_beginning",
            "kas dan setara kas akhir tahun": "fs.cf.cash_ending",
            "kas dan setara kas akhir periode": "fs.cf.cash_ending",
            "cash at end of period": "fs.cf.cash_ending",
            # Cash Flow & Capex
            "pembayaran untuk perolehan aset tetap": "fs.cf.acquisition_ppe",
            "pembelian aset tetap": "fs.cf.acquisition_ppe",
            "penambahan aset tetap": "fs.cf.acquisition_ppe",
            "perolehan aset tetap": "fs.cf.acquisition_ppe",
            "penerimaan dari penjualan (perolehan) aset tetap": "fs.cf.acquisition_ppe",
            "pembayaran uang muka pembelian aset tetap": "fs.cf.acquisition_ppe",
            "acquisition of fixed assets": "fs.cf.acquisition_ppe",
            "payments for acquisition of property, plant and equipment": "fs.cf.acquisition_ppe",
            "purchase of property plant and equipment": "fs.cf.acquisition_ppe",
            "pembayaran untuk perolehan tanaman perkebunan": "fs.cf.acquisition_ppe",
            # Banking Concepts (Generalizable to all commercial banks)
            "pendapatan bunga": "fs.is.interest_income",
            "pendapatan bunga dan syariah": "fs.is.interest_income",
            "interest income": "fs.is.interest_income",
            "beban bunga": "fs.is.interest_expense",
            "beban bunga dan syariah": "fs.is.interest_expense",
            "interest expense": "fs.is.interest_expense",
            "pendapatan bunga bersih": "fs.is.net_interest_income",
            "pendapatan bunga dan syariah neto": "fs.is.net_interest_income",
            "pendapatan bunga neto": "fs.is.net_interest_income",
            "net interest income": "fs.is.net_interest_income",
            "kredit yang diberikan": "fs.bs.bank.loans_gross",
            "pinjaman yang diberikan": "fs.bs.bank.loans_gross",
            "loans gross": "fs.bs.bank.loans_gross",
            "simpanan nasabah": "fs.bs.bank.customer_deposits",
            "dana pihak ketiga": "fs.bs.bank.customer_deposits",
            "customer deposits": "fs.bs.bank.customer_deposits",
            "giro pihak ketiga": "fs.bs.bank.demand_deposits",
            "giro pihak berelasi": "fs.bs.bank.demand_deposits_related",
            "giro": "fs.bs.bank.demand_deposits",
            "tabungan pihak ketiga": "fs.bs.bank.savings_deposits",
            "tabungan pihak berelasi": "fs.bs.bank.savings_deposits_related",
            "tabungan": "fs.bs.bank.savings_deposits",
            "deposito berjangka pihak ketiga": "fs.bs.bank.time_deposits",
            "deposito berjangka pihak berelasi": "fs.bs.bank.time_deposits_related",
            "deposito berjangka": "fs.bs.bank.time_deposits",
            "giro mudharabah": "fs.bs.sharia.demand_deposits_mudharabah",
            "tabungan mudharabah": "fs.bs.sharia.savings_deposits_mudharabah",
            "deposito berjangka mudharabah": "fs.bs.sharia.time_deposits_mudharabah",
            "jumlah dana syirkah temporer": "fs.bs.sharia.temporary_syirkah_funds",
            "dana syirkah temporer": "fs.bs.sharia.temporary_syirkah_funds",
            "temporary syirkah funds": "fs.bs.sharia.temporary_syirkah_funds",
            "penempatan pada bank indonesia": "fs.bs.bank.placements",
            "surat berharga yang dimiliki": "fs.bs.bank.marketable_securities",
            # Agribusiness & Plantations (e.g. LSIP)
            "tanaman menghasilkan": "fs.bs.bearer_plants",
            "tanaman belum menghasilkan": "fs.bs.immature_plantations",
            "aset biologis": "fs.bs.biological_assets",
            "penjualan produk kelapa sawit": "fs.is.revenue",
            "penjualan minyak sawit mentah dan turunannya": "fs.is.revenue",
            "penjualan cpo": "fs.is.revenue",
            "penjualan karet": "fs.is.revenue",
            "beban pokok perkebunan": "fs.is.cost_of_revenue",
            # Aviation & Logistics (e.g. CASS)
            "pendapatan jasa aviasi": "fs.is.revenue",
            "pendapatan jasa penerbangan": "fs.is.revenue",
            "pendapatan jasa boga penerbangan": "fs.is.revenue",
            "pendapatan jasa penanganan kargo": "fs.is.revenue",
            "pendapatan jasa pergudangan": "fs.is.revenue",
            "beban pokok jasa aviasi": "fs.is.cost_of_revenue",
            "beban pokok jasa non aviasi": "fs.is.cost_of_revenue",
            # Information Technology & Services (e.g. ATIC)
            "pendapatan jasa integrasi sistem": "fs.is.revenue",
            "pendapatan jasa konsultasi it": "fs.is.revenue",
            "penjualan perangkat keras dan lunak": "fs.is.revenue",
            "beban pokok penjualan dan jasa": "fs.is.cost_of_revenue",
            # Infrastructure & Port Services (e.g. IPCM)
            "pendapatan dari kontrak dengan pelanggan": "fs.is.revenue",
            "pendapatan jasa kapal": "fs.is.revenue",
            "pendapatan usaha jasa": "fs.is.revenue",
            "revenue from contracts with customers": "fs.is.revenue",
            "beban pokok pendapatan jasa": "fs.is.cost_of_revenue",
            # Mining Contracting & Energy Services (e.g. ABMM, ADRO)
            "pendapatan kontraktor penambangan": "fs.is.revenue",
            "pendapatan jasa penambangan": "fs.is.revenue",
            "pendapatan sewa alat": "fs.is.revenue",
            "penjualan batu bara": "fs.is.revenue",
            "mining contracting revenue": "fs.is.revenue",
            "coal sales": "fs.is.revenue",
            "beban pokok kontraktor penambangan": "fs.is.cost_of_revenue",
            # Renewable Energy & Utilities (e.g. BREN)
            "pendapatan penjualan uap dan listrik": "fs.is.revenue",
            "pendapatan penjualan listrik": "fs.is.revenue",
            "pendapatan penjualan uap": "fs.is.revenue",
            "sale of steam and electricity": "fs.is.revenue",
            "revenue from steam and electricity": "fs.is.revenue",
            "beban pokok penjualan uap dan listrik": "fs.is.cost_of_revenue",
            # Commercial Mega-Banking (e.g. BBCA, BBSI)
            "simpanan dari nasabah": "fs.bs.bank.customer_deposits",
            "liabilitas segera": "fs.bs.bank.demand_deposits",
            "simpanan berjangka": "fs.bs.bank.time_deposits",
            "kredit yang diberikan pihak ketiga": "fs.bs.bank.loans_gross",
            "kredit yang diberikan pihak berelasi": "fs.bs.bank.loans_gross",
            "loans": "fs.bs.bank.loans_gross",
            "deposits from customers": "fs.bs.bank.customer_deposits",
        }
        for label, cid in standard_map.items():
            self.label_to_concept[normalize_label(label)] = cid

    def get_candidates_for_statement(self, statement_type: str, limit: int = 100) -> list[dict[str, str]]:
        """Return candidate concepts for a statement type (balance_sheet, income_statement, cash_flow)."""
        candidates = []
        for cid, c in self.concepts.items():
            if c.statement == statement_type:
                candidates.append({
                    "concept_id": cid,
                    "label_en": c.label_en,
                    "label_id": c.label_id,
                })
        return candidates[:limit]

    def get_candidates_for_kpis(self, packs: Optional[list[str]] = None) -> list[dict[str, Any]]:
        """Return candidate KPIs for specific packs, or all operational packs if None."""
        if packs is None:
            candidates = []
            for pack_items in self.kpi_packs.values():
                for item in pack_items:
                    candidates.append(item)
            return candidates
        candidates = []
        for pack in packs:
            pack_items = self.kpi_packs.get(pack, [])
            for item in pack_items:
                candidates.append(item)
        return candidates

    def map_label(self, label: Optional[str], statement_hint: Optional[str] = None) -> Optional[str]:
        """Attempt exact or token-overlap matching against taxonomy labels."""
        if not label:
            return None
        norm = normalize_label(label)
        # 1. Exact normalized match
        if norm in self.label_to_concept:
            return self.label_to_concept[norm]

        # 2. Token overlap match (only for phrases with >= 3 words and high overlap)
        tokens = set(norm.split())
        if len(tokens) >= 3:
            best_cid = None
            best_score = 0.0
            for registered_norm, cid in self.label_to_concept.items():
                if statement_hint and cid in self.concepts and self.concepts[cid].statement != statement_hint:
                    continue
                reg_tokens = set(registered_norm.split())
                if len(reg_tokens) < 3:
                    continue
                intersection = len(tokens & reg_tokens)
                union = len(tokens | reg_tokens)
                jaccard = intersection / union if union > 0 else 0
                if jaccard > 0.80 and jaccard > best_score:
                    best_score = jaccard
                    best_cid = cid
            if best_cid:
                return best_cid

        return None

    def load_persisted_mappings(self, conn: Any) -> int:
        """Load previously learned mappings from idxfin_ref.label_mapping table in DuckDB."""
        try:
            rows = conn.execute("SELECT label_raw, concept_id FROM idxfin_ref.label_mapping").fetchall()
            loaded = 0
            for label, cid in rows:
                if label and cid:
                    self.label_to_concept[normalize_label(label)] = cid
                    loaded += 1
            return loaded
        except Exception:
            return 0

    def persist_mapping(
        self,
        conn: Any,
        label_raw: str,
        concept_id: str,
        mapping_method: str = "llm_few_shot",
        confidence: float = 0.95,
        statement_type: Optional[str] = None,
    ) -> None:
        """Persist newly resolved mapping to idxfin_ref.label_mapping."""
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO idxfin_ref.label_mapping 
                (label_raw, concept_id, mapping_method, confidence, statement_type, updated_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                [label_raw, concept_id, mapping_method, confidence, statement_type]
            )
            self.label_to_concept[normalize_label(label_raw)] = concept_id
        except Exception:
            pass

    def get_few_shot_examples(self, statement_type: Optional[str] = None, limit: int = 15) -> list[dict[str, Any]]:
        """Return representative few-shot mapping examples across established rules."""
        shots = [
            # Balance sheet
            {"label_id": "kas dan setara kas", "label_en": "cash and cash equivalents", "concept_id": "fs.bs.cash_and_equivalents", "statement": "balance_sheet"},
            {"label_id": "piutang usaha", "label_en": "trade receivables", "concept_id": "fs.bs.trade_receivables", "statement": "balance_sheet"},
            {"label_id": "persediaan", "label_en": "inventories", "concept_id": "fs.bs.inventories", "statement": "balance_sheet"},
            {"label_id": "aset tetap", "label_en": "property, plant and equipment", "concept_id": "fs.bs.property_plant_equipment", "statement": "balance_sheet"},
            {"label_id": "tanaman menghasilkan", "label_en": "bearer plants", "concept_id": "fs.bs.bearer_plants", "statement": "balance_sheet"},
            {"label_id": "properti pertambangan", "label_en": "mining properties", "concept_id": "fs.bs.mining_properties", "statement": "balance_sheet"},
            {"label_id": "jumlah dana syirkah temporer", "label_en": "temporary syirkah funds", "concept_id": "fs.bs.sharia.temporary_syirkah_funds", "statement": "balance_sheet"},
            {"label_id": "jumlah liabilitas", "label_en": "total liabilities", "concept_id": "fs.bs.total_liabilities", "statement": "balance_sheet"},
            {"label_id": "jumlah ekuitas", "label_en": "total equity", "concept_id": "fs.bs.total_equity", "statement": "balance_sheet"},
            # Income statement
            {"label_id": "penjualan dan pendapatan usaha", "label_en": "revenue", "concept_id": "fs.is.revenue", "statement": "income_statement"},
            {"label_id": "pendapatan bunga", "label_en": "interest income", "concept_id": "fs.is.interest_income", "statement": "income_statement"},
            {"label_id": "beban pokok pendapatan", "label_en": "cost of revenue", "concept_id": "fs.is.cogs", "statement": "income_statement"},
            {"label_id": "beban pengupasan tanah", "label_en": "overburden removal", "concept_id": "fs.is.cogs", "statement": "income_statement"},
            {"label_id": "laba bruto", "label_en": "gross profit", "concept_id": "fs.is.gross_profit", "statement": "income_statement"},
            {"label_id": "beban bunga", "label_en": "interest expense", "concept_id": "fs.is.interest_expense", "statement": "income_statement"},
            {"label_id": "laba sebelum pajak penghasilan", "label_en": "profit before tax", "concept_id": "fs.is.profit_before_tax", "statement": "income_statement"},
            {"label_id": "beban pajak penghasilan", "label_en": "income tax expense", "concept_id": "fs.is.income_tax_expense", "statement": "income_statement"},
            {"label_id": "laba tahun berjalan", "label_en": "profit for the year", "concept_id": "fs.is.profit_for_the_period", "statement": "income_statement"},
            {"label_id": "laba dari operasi yang dihentikan", "label_en": "discontinued operations", "concept_id": "fs.is.discontinued_operations", "statement": "income_statement"},
            # Cash flow
            {"label_id": "arus kas bersih diperoleh dari aktivitas operasi", "label_en": "net cash from operating activities", "concept_id": "fs.cf.net_cash_operating", "statement": "cash_flow"},
            {"label_id": "arus kas bersih digunakan untuk aktivitas investasi", "label_en": "net cash used in investing activities", "concept_id": "fs.cf.net_cash_investing", "statement": "cash_flow"},
            {"label_id": "arus kas bersih digunakan untuk aktivitas pendanaan", "label_en": "net cash used in financing activities", "concept_id": "fs.cf.net_cash_financing", "statement": "cash_flow"},
            {"label_id": "kenaikan bersih kas dan setara kas", "label_en": "net change in cash and cash equivalents", "concept_id": "fs.cf.net_change_cash", "statement": "cash_flow"},
            {"label_id": "perolehan aset tetap", "label_en": "acquisition of fixed assets", "concept_id": "fs.cf.acquisition_ppe", "statement": "cash_flow"},
            {"label_id": "efek perubahan nilai kurs terhadap kas", "label_en": "effect of exchange rate fluctuations on cash", "concept_id": "fs.cf.fx_effect", "statement": "cash_flow"},
        ]
        if statement_type:
            filtered = [s for s in shots if s["statement"] == statement_type]
            return filtered[:limit] if filtered else shots[:limit]
        return shots[:limit]

    def resolve_unmapped_with_llm(
        self,
        label_raw: str,
        label_raw_alt: Optional[str] = None,
        statement_type: Optional[str] = None,
        context: Optional[str] = None,
        llm_client: Optional[Any] = None,
        db_conn: Optional[Any] = None,
    ) -> tuple[Optional[str], float, str]:
        """
        LLM-first concept resolution for novel line item labels.
        Injects candidates and established few-shot rules into the prompt.
        Persists newly resolved mappings to DuckDB for future O(1) lookups.
        """
        # Check cache first
        cached = self.map_label(label_raw, statement_hint=statement_type) or (self.map_label(label_raw_alt, statement_hint=statement_type) if label_raw_alt else None)
        if cached:
            return cached, 1.0, "exact_cached"

        if not llm_client:
            slug = re.sub(r"[^\w]", "_", label_raw.lower())[:30].strip("_")
            ext_cid = f"ext.general.{slug}"
            return ext_cid, 0.5, "extension_fallback"

        from src.llm.prompts import CONCEPT_MAPPING_PROMPT
        import json

        candidates = self.get_candidates_for_statement(statement_type) if statement_type else []
        candidates_json = json.dumps(candidates[:60], indent=2)

        prompt = CONCEPT_MAPPING_PROMPT.format(
            candidate_concepts_json=candidates_json,
            label_raw=label_raw,
            label_raw_alt=label_raw_alt or "None",
            statement_type=statement_type or "general",
            context=context or "IDX Corporate Filing",
        )

        try:
            res = llm_client.complete_json(prompt, system="You are an expert IDX financial statement taxonomy indexer.")
            cid = res.get("concept_id")
            conf = float(res.get("confidence", 0.90))
            if cid:
                norm_label = normalize_label(label_raw)
                self.label_to_concept[norm_label] = cid
                if db_conn:
                    self.persist_mapping(db_conn, label_raw, cid, "llm_few_shot", conf, statement_type)
                return cid, conf, "llm_few_shot"
        except Exception:
            pass

        slug = re.sub(r"[^\w]", "_", label_raw.lower())[:30].strip("_")
        return f"ext.general.{slug}", 0.5, "extension_fallback"

