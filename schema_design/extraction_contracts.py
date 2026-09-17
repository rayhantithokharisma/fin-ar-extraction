"""
extraction_contracts.py — Pydantic v2 contracts for IDX filing extraction.

These are the shapes every extractor (XBRL loader, table parser, LLM) MUST emit.
They are deliberately AS-REPORTED:
  * labels stay in the issuer's words (ID + EN on bilingual pages)
  * numbers stay as raw cell text; code parses them, not the model
  * canonical concept mapping, unit conversion and FX happen downstream

Three output shapes (see DESIGN.md §1.2):
  Facts      -> StatementExtraction, NoteTableExtraction, KPIFact
  Records    -> DebtInstrument, DerivativeContract, CommitmentContingency, Facility,
                GroupEntity, ShareholderPosition, OfficerPosition, SupplyChainLink,
                LicenseAgreement, SubsequentEvent, AuditReport
  Assertions -> ForwardGuidance, DriverAttribution, RiskFactor, AccountingPolicy
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Generic, Literal, Optional, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator


# =============================================================== enums
class DocType(str, Enum):
    FS_ANNUAL = "fs_annual"                  # LK tahunan (audited)
    FS_INTERIM = "fs_interim"                # LK Q1/H1/9M (reviewed or unaudited)
    ANNUAL_REPORT = "annual_report"
    SUSTAINABILITY_REPORT = "sustainability_report"
    PUBLIC_EXPOSE = "public_expose"
    INVESTOR_PRESENTATION = "investor_presentation"
    PROSPECTUS = "prospectus"
    IDX_DISCLOSURE = "idx_disclosure"        # keterbukaan informasi
    RUPS_MATERIAL = "rups_material"
    PRESS_RELEASE = "press_release"


class Template(str, Enum):
    GENERAL = "GENERAL"
    PROPERTY = "PROPERTY"
    INFRASTRUCTURE = "INFRASTRUCTURE"
    BANK = "BANK"
    FINANCING = "FINANCING"
    SECURITIES = "SECURITIES"
    INSURANCE = "INSURANCE"                  # pre-PSAK 117
    INSURANCE_P117 = "INSURANCE_P117"        # PSAK 117, FY2025+


class StatementType(str, Enum):
    BS = "balance_sheet"
    IS = "income_statement"
    OCI = "comprehensive_income"             # often the same page as IS
    SOCE = "changes_in_equity"
    CF = "cash_flow"


class PeriodType(str, Enum):
    INSTANT = "instant"
    DURATION = "duration"


class FiscalPeriod(str, Enum):
    FY = "FY"      # 12M cumulative
    Q1 = "Q1"      # 3M (cumulative and discrete are the same thing)
    H1 = "H1"      # 6M cumulative
    M9 = "9M"      # 9M cumulative
    Q2 = "Q2"      # 3M discrete — only when a filing/deck shows a standalone quarter column
    Q3 = "Q3"
    Q4 = "Q4"
    OTHER = "OTHER"  # first period after listing, fiscal-year change, etc.


_CUMULATIVE_MONTHS = {"Q1": 3, "H1": 6, "9M": 9, "FY": 12}
_DISCRETE_MONTHS = {"Q1": 3, "Q2": 3, "Q3": 3, "Q4": 3}


class Assurance(str, Enum):
    AUDITED = "audited"
    REVIEWED = "reviewed"
    UNAUDITED = "unaudited"
    MANAGEMENT_NARRATIVE = "management_narrative"
    THIRD_PARTY = "third_party"              # e.g. competent-person reserve statement


class Scenario(str, Enum):
    ACTUAL = "actual"
    BUDGET = "budget"
    GUIDANCE = "guidance"
    TARGET = "target"
    SENSITIVITY = "sensitivity"


class NumberLocale(str, Enum):
    ID = "id"   # 1.234.567,89
    EN = "en"   # 1,234,567.89


# =============================================================== base + deterministic parsing
class Base(BaseModel):
    model_config = ConfigDict(extra="ignore", use_enum_values=True)


_NIL = {"-", "–", "—", "nihil", "nil"}
_STRIP = re.compile(r"(rp\.?|us\$|usd|idr|\$|%|\s|[()\-−])")


def parse_reported_number(raw: str, locale: NumberLocale | str) -> Optional[Decimal]:
    """The model never has the final say on a number. Scale is applied by the loader."""
    s = raw.strip().lower()
    if not s:
        return None
    if s in _NIL:
        return Decimal(0)
    negative = (s.startswith("(") and s.endswith(")")) or s[0] in "-−"
    s = _STRIP.sub("", s)
    if NumberLocale(locale) is NumberLocale.ID:
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", "")
    try:
        v = Decimal(s)
    except InvalidOperation:
        return None
    return -v if negative else v


def grounded(raw_text: str, page_text: str) -> bool:
    """Hard rule: an extracted value must appear verbatim (whitespace-insensitive) on its cited page."""
    squash = lambda x: re.sub(r"\s+", "", x)
    return squash(raw_text) in squash(page_text)


# =============================================================== shared building blocks
class SourceRef(Base):
    page: int = Field(ge=1, description="1-based PDF page index, not the printed page number")
    printed_page: Optional[str] = None
    bbox: Optional[list[float]] = Field(None, min_length=4, max_length=4, description="x0,y0,x1,y1 in PDF points")
    table_id: Optional[str] = None
    row: Optional[int] = None
    col: Optional[int] = None
    section_path: Optional[str] = Field(None, description="e.g. 'LK/Notes/15 Utang bank', 'AR/MD&A/Tinjauan operasional'")
    quote: Optional[str] = Field(None, description="Short evidence span used for grounding")


class PresentationContext(Base):
    currency: str = Field(description="ISO 4217 presentation currency, e.g. IDR, USD")
    scale: Literal[1, 1_000, 1_000_000, 1_000_000_000]
    scale_raw: str = Field(description="e.g. 'Dalam jutaan Rupiah', 'Expressed in thousands of US Dollars'")
    consolidation_scope: Literal["consolidated", "parent_only", "combined"] = "consolidated"


class PeriodRef(Base):
    column_label_raw: str = Field(description="e.g. '31 Desember 2024', '30 September 2025 (9 bulan)', '1 Januari 2023'")
    period_type: PeriodType
    start_date: Optional[date] = None
    end_date: date
    fiscal_year: int
    fiscal_period: FiscalPeriod
    is_cumulative_ytd: bool = Field(True, description="IDX interim IS/CF are cumulative YTD (Q1=3M, H1=6M, 9M)")
    is_restated: bool = Field(False, description="Header says 'Disajikan kembali' / 'As restated'")
    assurance_label_raw: Optional[str] = Field(None, description="Column label, e.g. 'Tidak diaudit', 'Unaudited', 'Diaudit'")
    assurance: Optional[Assurance] = Field(
        None, description="Per COLUMN: in an interim LK the current column is unaudited but the prior year-end column is audited")

    @model_validator(mode="after")
    def _period_shape(self):
        ptype = self.period_type.value if hasattr(self.period_type, "value") else str(self.period_type)
        if ptype == "instant" or self.period_type == PeriodType.INSTANT:
            self.start_date = None
        elif ptype == "duration" or self.period_type == PeriodType.DURATION:
            if self.start_date is None:
                year = self.fiscal_year or (self.end_date.year if self.end_date else 2025)
                self.start_date = date(year, 1, 1)
            if self.end_date and self.start_date > self.end_date:
                self.start_date = date(self.end_date.year, 1, 1)
        return self


class Dim(Base):
    axis: str = Field(description="Axis id from taxonomy_seed.yaml, e.g. segment_business, movement, counterparty_relation")
    member_raw: str = Field(description="As printed, e.g. 'Pihak berelasi', 'Penambahan', 'Batubara'")
    member: Optional[str] = Field(None, description="Standard member id only if certain; else null for the mapper")


class ConceptCandidate(Base):
    concept_id: str = Field(description="MUST come from the candidate list injected in the prompt")
    score: float = Field(ge=0, le=1)


class CounterpartyMention(Base):
    name_raw: str
    kind: Literal["bank", "bond_trustee", "supplier", "customer", "contractor", "offtaker", "distributor",
                  "government", "related_party", "auditor", "insurer", "shareholder", "other"]
    country: Optional[str] = None
    is_related_party: Optional[bool] = None
    relationship_raw: Optional[str] = Field(None, description="e.g. 'Entitas sepengendali', 'Pemegang saham utama'")


class PeriodValue(Base):
    """A raw value bound to a period — used inside records; the loader explodes these into facts."""
    period: PeriodRef
    raw_text: str
    currency: Optional[str] = None
    dims: list[Dim] = Field(default_factory=list)
    source: SourceRef


# =============================================================== FACTS
class Cell(Base):
    period_index: int = Field(ge=0, description="Index into the parent table's `periods`")
    col_dims: list[Dim] = Field(default_factory=list, description="Column-level dims: equity_component (SOCE), segment, asset_class, ...")
    raw_text: str = Field(description="Exactly as printed, e.g. '(1.234.567)', '-'")
    source: SourceRef

    @field_validator("raw_text", mode="before")
    @classmethod
    def _coerce_raw_text(cls, v):
        if v is None:
            return "-"
        return str(v)


class LineItem(Base):
    label_raw: str
    label_raw_alt_lang: Optional[str] = Field(None, description="Other-language label on bilingual pages")
    note_ref: Optional[str] = Field(None, description="'Catatan/Notes' column, e.g. '5', '2d,15'")
    indent_level: int = Field(0, ge=0)
    is_total: bool = False
    parent_label_raw: Optional[str] = None
    row_dims: list[Dim] = Field(default_factory=list, description="Row-level dims: movement (rollforwards), counterparty_relation, aging_bucket, ...")
    cells: list[Cell] = Field(default_factory=list)
    concept_candidates: list[ConceptCandidate] = Field(default_factory=list, max_length=5)


class _TableExtraction(Base):
    context: PresentationContext
    periods: list[PeriodRef] = Field(min_length=1)
    line_items: list[LineItem]

    @model_validator(mode="after")
    def _period_indexes(self):
        n = len(self.periods)
        for li in self.line_items:
            for c in li.cells:
                if c.period_index >= n:
                    raise ValueError(f"'{li.label_raw}': period_index {c.period_index} out of range ({n} periods)")
        return self


class StatementExtraction(_TableExtraction):
    statement_type: StatementType
    title_raw: str
    template_hint: Optional[Template] = None
    bs_format: Optional[Literal["current_noncurrent", "liquidity"]] = None
    cf_method: Optional[Literal["direct", "indirect"]] = None


class NoteTableExtraction(_TableExtraction):
    note_number: str = Field(description="e.g. '12', '12a'")
    note_title_raw: str
    table_title_raw: Optional[str] = None
    topic: Literal["ppe_rollforward", "intangibles_rollforward", "rou_rollforward", "mine_properties", "borrowings",
                   "receivables_aging", "ecl_allowance", "inventory", "segment", "related_party", "employee_benefits",
                   "tax", "cost_breakdown", "opex_breakdown", "revenue_disaggregation", "financial_risk_sensitivity",
                   "fair_value", "major_customers_suppliers", "earnings_per_share", "revenue_gross_to_net",
                   "research_development", "royalties", "share_based_payment", "goodwill_impairment",
                   "contract_assets_liabilities", "excise", "other"]


class KPIFact(Base):
    kpi_label_raw: str
    kpi_label_raw_alt_lang: Optional[str] = Field(None, description="Other-language label on bilingual pages")
    kpi_candidates: list[ConceptCandidate] = Field(default_factory=list, max_length=5)
    raw_text: str
    unit_raw: str = Field(description="e.g. 'juta ton', 'wmt', 'US$/ton', 'boepd', 'Ha', '%', 'kkal/kg GAR'")
    period: PeriodRef
    scenario: Scenario = Scenario.ACTUAL
    dims: list[Dim] = Field(default_factory=list, description="facility, product, commodity, reserve_class, moisture_basis, cv_basis, ...")
    basis: Literal["gross_100pct", "attributable", "not_stated"] = "not_stated"
    is_company_defined: bool = Field(False, description="Non-GAAP / company-defined (EBITDA, core profit, cash cost, SSSG)")
    definition_text: Optional[str] = Field(None, max_length=500)
    source: SourceRef


# =============================================================== RECORDS
class Covenant(Base):
    metric_raw: str = Field(description="e.g. 'Debt Service Coverage Ratio', 'Rasio utang terhadap ekuitas'")
    metric_code: Optional[Literal["dscr", "icr", "der", "net_debt_to_ebitda", "debt_to_ebitda", "current_ratio",
                                  "min_tangible_net_worth", "gearing", "other"]] = None
    operator: Literal[">=", ">", "<=", "<", "="]
    threshold_raw: str
    test_frequency: Optional[Literal["quarterly", "semiannual", "annual", "other"]] = None
    issuer_states_compliance: Optional[bool] = None
    source: SourceRef


class DebtInstrument(Base):
    instrument_name_raw: str = Field(description="e.g. 'Obligasi Berkelanjutan III Tahap 2 Seri A', 'Fasilitas Kredit Investasi'")
    instrument_type: Literal["term_loan", "revolving", "working_capital", "syndicated_loan", "bond", "sukuk_ijarah",
                             "sukuk_mudharabah", "mtn", "shareholder_loan", "export_credit", "other"]
    borrower_entity_raw: str
    lenders_or_trustee: list[CounterpartyMention] = Field(min_length=1)
    currency: str
    facility_limit_raw: Optional[str] = None
    rate_type: Optional[Literal["fixed", "floating", "mixed"]] = None
    rate_raw: Optional[str] = Field(None, description="e.g. 'INDONIA + 2,50%', 'Term SOFR 3M + 3.25%', '7,65% per tahun'")
    benchmark: Optional[str] = None
    spread_bps: Optional[int] = None
    coupon_pct: Optional[Decimal] = None
    signing_or_issue_date: Optional[date] = None
    maturity_date: Optional[date] = None
    secured: Optional[bool] = None
    collateral_raw: Optional[str] = Field(None, max_length=500)
    guarantor_raw: Optional[str] = None
    rating_raw: Optional[str] = Field(None, description="e.g. 'idAA (Pefindo)'")
    covenants: list[Covenant] = Field(default_factory=list)
    balances: list[PeriodValue] = Field(default_factory=list, description="Outstanding principal; exploded to facts with instrument dim")
    source: SourceRef


class DerivativeContract(Base):
    hedged_risk: Literal["fx", "interest_rate", "commodity", "other"]
    instrument: Literal["forward", "swap", "cross_currency_swap", "option", "collar", "futures", "other"]
    counterparty: Optional[CounterpartyMention] = None
    underlying_raw: Optional[str] = Field(None, description="e.g. 'USD/IDR', 'LME nickel', 'Newcastle coal'")
    notional_raw: Optional[str] = None
    notional_currency_or_unit: Optional[str] = None
    maturity_date: Optional[date] = None
    hedge_designation: Literal["cash_flow", "fair_value", "net_investment", "none", "not_stated"] = "not_stated"
    fair_values: list[PeriodValue] = Field(default_factory=list)
    source: SourceRef


class CommitmentContingency(Base):
    kind: Literal["capex_commitment", "purchase_commitment", "take_or_pay", "offtake", "guarantee_given",
                  "litigation", "tax_dispute", "environmental", "regulatory", "other"]
    counterparty: Optional[CounterpartyMention] = None
    description: str = Field(max_length=500)
    amount_raw: Optional[str] = None
    currency: Optional[str] = None
    status_raw: Optional[str] = None
    likelihood: Literal["probable", "possible", "remote", "not_stated"] = "not_stated"
    provision_recognized: Optional[bool] = None
    source: SourceRef


class Facility(Base):
    name_raw: str
    facility_type: Literal["mine_concession", "processing_plant", "smelter", "refinery", "hpal", "power_plant",
                           "plantation_estate", "palm_oil_mill", "cement_plant", "factory", "pharma_plant",
                           "food_beverage_plant", "assembly_plant", "warehouse", "distribution_center", "store",
                           "hospital", "clinic_lab", "data_center", "hotel", "farm_hatchery", "feed_mill",
                           "tower_site", "toll_section", "port_jetty", "office", "other"]
    facility_type_raw: Optional[str] = Field(None, description="As printed, always filled when facility_type='other'")
    owning_entity_raw: Optional[str] = None
    status: Literal["operating", "commissioning", "construction", "planned", "suspended", "closed", "not_stated"] = "not_stated"
    province: Optional[str] = None
    regency: Optional[str] = None
    latitude: Optional[float] = Field(None, ge=-90, le=90)
    longitude: Optional[float] = Field(None, ge=-180, le=180)
    license_type: Optional[Literal["IUP", "IUPK", "PKP2B", "KK", "HGU", "HGB", "other"]] = None
    license_expiry: Optional[date] = None
    area_raw: Optional[str] = Field(None, description="e.g. '18.610 Ha'")
    primary_products: list[str] = Field(default_factory=list)
    nameplate_capacity_raw: Optional[str] = None
    capacity_unit_raw: Optional[str] = Field(None, description="e.g. 'ton Ni/tahun', 'Mtpa', 'MW', 'ton TBS/jam'")
    commercial_operation_date: Optional[date] = None
    source: SourceRef


class GroupEntity(Base):
    entity_name_raw: str
    relation: Literal["subsidiary", "associate", "joint_venture", "joint_operation"]
    direct_parent_raw: Optional[str] = None
    domicile: Optional[str] = None
    principal_activity_raw: Optional[str] = None
    effective_ownership_pct: Optional[Decimal] = Field(None, ge=0, le=100)
    commercial_operation_year: Optional[int] = None
    total_assets: Optional[PeriodValue] = None
    source: SourceRef


class ShareholderPosition(Base):
    holder_name_raw: str
    holder_type: Literal["corporate", "individual", "government", "public_below_5pct", "treasury", "management", "other"]
    share_class: Optional[str] = None
    shares_raw: str
    ownership_pct: Optional[Decimal] = Field(None, ge=0, le=100)
    as_of: date
    source: SourceRef


class OfficerPosition(Base):
    person_name_raw: str
    role: Literal["president_director", "director", "president_commissioner", "commissioner",
                  "independent_commissioner", "audit_committee", "sharia_supervisory_board",
                  "corporate_secretary", "other"]
    appointed_date: Optional[date] = None
    term_end: Optional[date] = None
    is_independent: Optional[bool] = None
    concurrent_positions_raw: Optional[str] = None
    source: SourceRef


class SupplyChainLink(Base):
    counterparty: CounterpartyMention
    relation: Literal["supplier", "customer", "contractor", "offtaker", "distributor", "logistics", "licensor",
                      "brand_principal", "toll_manufacturer", "franchisor", "franchisee", "technology_partner", "other"]
    item_raw: Optional[str] = Field(None, description="e.g. 'jasa penambangan', 'sulfur', 'bijih nikel', 'CPO'")
    share_pct: Optional[Decimal] = Field(None, ge=0, le=100)
    share_basis: Literal["revenue", "purchases", "cogs", "volume", "not_stated"] = "not_stated"
    from_major_counterparty_note: bool = Field(False, description="True if sourced from the >10% customers/suppliers note (audited)")
    contract_term_raw: Optional[str] = None
    origin_or_destination_country: Optional[str] = None
    source: SourceRef


class LicenseAgreement(Base):
    """Brand licenses (retail), in-licensed drugs/technology (pharma, tech), franchise and distribution rights."""
    agreement_type: Literal["brand_license", "franchise", "exclusive_distribution", "technology_license",
                            "product_in_license", "toll_manufacturing", "trademark_from_parent", "other"]
    direction: Literal["in_licensed", "out_licensed"]
    counterparty: CounterpartyMention
    brands_or_products: list[str] = Field(default_factory=list)
    territory_raw: Optional[str] = Field(None, description="e.g. 'Indonesia', 'Indonesia, Vietnam, Filipina'")
    exclusive: Optional[bool] = None
    start_date: Optional[date] = None
    expiry_date: Optional[date] = None
    renewal_terms_raw: Optional[str] = Field(None, max_length=300)
    royalty_basis_raw: Optional[str] = Field(None, description="e.g. '3% dari penjualan neto', 'fixed fee per store'")
    minimum_commitment_raw: Optional[str] = None
    source: SourceRef


class SubsequentEvent(Base):
    event_date: Optional[date] = None
    category: Literal["financing", "dividend", "m_and_a", "capital", "litigation", "regulatory", "operational", "other"]
    description: str = Field(max_length=500)
    amount_raw: Optional[str] = None
    currency: Optional[str] = None
    source: SourceRef


class AuditReport(Base):
    audit_firm_raw: str = Field(description="KAP name as printed")
    network_raw: Optional[str] = None
    signing_partner_raw: Optional[str] = None
    report_date: Optional[date] = None
    opinion: Literal["unmodified", "qualified", "adverse", "disclaimer"]
    emphasis_of_matter: bool = False
    material_uncertainty_going_concern: bool = False
    key_audit_matters: list[str] = Field(default_factory=list, description="KAM titles only")
    source: SourceRef


# =============================================================== ASSERTIONS
class ForwardGuidance(Base):
    metric_label_raw: str
    metric_candidates: list[ConceptCandidate] = Field(default_factory=list, max_length=5)
    scenario: Literal["guidance", "target", "budget"]
    point_raw: Optional[str] = None
    low_raw: Optional[str] = None
    high_raw: Optional[str] = None
    unit_raw: str
    target_period_raw: str = Field(description="e.g. 'tahun 2026', 'FY2026', 'by 2028'")
    target_fiscal_year: Optional[int] = None
    dims: list[Dim] = Field(default_factory=list)
    conditions_raw: Optional[str] = Field(None, max_length=300)
    funding_source_raw: Optional[str] = Field(None, description="For capex plans: kas internal, pinjaman bank, dana IPO ...")
    source: SourceRef

    @model_validator(mode="after")
    def _has_value(self):
        if not (self.point_raw or self.low_raw or self.high_raw):
            raise ValueError("guidance needs point_raw or a low/high range")
        return self


class DriverAttribution(Base):
    metric_label_raw: str
    metric_candidates: list[ConceptCandidate] = Field(default_factory=list, max_length=5)
    period: PeriodRef
    direction: Literal["up", "down", "flat"]
    change_raw: Optional[str] = Field(None, description="e.g. '12,4%', 'USD 45 juta'")
    driver: Literal["volume", "price", "mix", "fx", "input_cost", "royalty_tax", "one_off", "accounting_change",
                    "acquisition_disposal", "regulatory", "weather_seasonal", "financing", "other"]
    driver_summary: str = Field(max_length=280)
    contribution_raw: Optional[str] = None
    source: SourceRef


class RiskFactor(Base):
    category: Literal["commodity_price", "fx", "interest_rate", "credit", "liquidity", "regulatory_policy",
                      "operational", "supply_chain", "customer_concentration", "legal", "climate_environment",
                      "geopolitical", "technology_cyber", "reserve_resource", "social_license", "competition",
                      "license_renewal", "price_regulation", "product_quality_recall", "input_import_dependency",
                      "other"]
    title_raw: str
    summary: str = Field(max_length=400)
    mitigation_summary: Optional[str] = Field(None, max_length=400)
    is_quantified: bool = False
    source: SourceRef


class AccountingPolicy(Base):
    topic: Literal["ppe_measurement", "land_measurement", "investment_property_measurement", "inventory_costing",
                   "depreciation_method", "useful_lives", "revenue_recognition", "leases", "functional_currency",
                   "ecl_approach", "biological_assets", "stripping_costs", "exploration_evaluation", "consolidation",
                   "principal_vs_agent", "customer_incentives", "consignment_sales", "construction_contracts",
                   "development_costs", "software_capitalization", "share_based_payment", "excise",
                   "service_concession", "other"]
    choice_raw: str = Field(max_length=300)
    choice_code: Optional[Literal["cost_model", "revaluation_model", "fair_value_model", "fifo", "weighted_average",
                                  "moving_average", "specific_identification", "straight_line", "declining_balance",
                                  "units_of_production", "point_in_time", "over_time", "gross_principal",
                                  "net_agent", "expensed", "capitalized", "usd", "idr", "other"]] = None
    useful_life_min_years: Optional[float] = None
    useful_life_max_years: Optional[float] = None
    changed_this_period: bool = False
    change_impact_raw: Optional[str] = Field(None, max_length=300)
    source: SourceRef


# =============================================================== envelope + routing
T = TypeVar("T", bound=BaseModel)


class ExtractionRun(Base):
    doc_id: str
    ticker: str
    doc_type: DocType
    extractor: str = Field(description="e.g. 'fs.statement', 'notes.borrowings', 'ar.guidance'")
    extractor_version: str
    method: Literal["xbrl", "table_parser", "llm", "vlm", "hybrid", "manual"]
    model: Optional[str] = None
    prompt_version: Optional[str] = None
    run_id: str
    number_locale: NumberLocale
    assurance: Assurance


class ExtractionResult(Base, Generic[T]):
    run: ExtractionRun
    items: list[T]
    warnings: list[str] = Field(default_factory=list)


# section_type -> (contract, shape). Sectionizer output drives this.
ROUTES: dict[str, tuple[type[BaseModel], str]] = {
    "lk.auditor_report":              (AuditReport, "record"),
    "lk.statement":                   (StatementExtraction, "fact"),
    "lk.note.table":                  (NoteTableExtraction, "fact"),
    "lk.note.borrowings":             (DebtInstrument, "record"),
    "lk.note.derivatives":            (DerivativeContract, "record"),
    "lk.note.commitments":            (CommitmentContingency, "record"),
    "lk.note.subsidiaries":           (GroupEntity, "record"),
    "lk.note.major_customers":        (SupplyChainLink, "record"),
    "lk.note.license_agreements":     (LicenseAgreement, "record"),
    "lk.note.subsequent_events":      (SubsequentEvent, "record"),
    "lk.note.policies":               (AccountingPolicy, "assertion"),
    "ar.shareholders":                (ShareholderPosition, "record"),
    "ar.governance.officers":         (OfficerPosition, "record"),
    "ar.operations.facilities":       (Facility, "record"),
    "ar.operations.supply_chain":     (SupplyChainLink, "record"),
    "ar.operations.licenses":         (LicenseAgreement, "record"),
    "ar.mdna.kpis":                   (KPIFact, "fact"),
    "ar.mdna.drivers":                (DriverAttribution, "assertion"),
    "ar.mdna.outlook":                (ForwardGuidance, "assertion"),
    "ar.risk":                        (RiskFactor, "assertion"),
    "sr.esg_metrics":                 (KPIFact, "fact"),
    "deck.kpis":                      (KPIFact, "fact"),
    "deck.guidance":                  (ForwardGuidance, "assertion"),
}
