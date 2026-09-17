"""
Versioned Prompts for Structured Extraction with Candidate Concept Injection.
"""
from __future__ import annotations

import json
from typing import Any

PROMPT_VERSION = "v1.0"

SYSTEM_FINANCIAL_ANALYST = """You are an expert IDX (Indonesia Stock Exchange) financial statement extractor and investment analyst.
Your task is to extract exact financial statement data from Indonesian/English bilingual company filings.

CRITICAL RULES:
1. PRESERVE AS-REPORTED NUMBERS: Always extract numbers EXACTLY as printed in `raw_text` (e.g. "14,159,490", "(19,941,401)", "-"). NEVER parse numbers into decimals yourself or alter negative brackets.
2. PRESERVE BILINGUAL LABELS: Extract Indonesian label in `label_raw` and English label in `label_raw_alt_lang`.
3. CONSTRAIN CONCEPT IDS: Assign `concept_candidates` ONLY from the candidate list provided in the user prompt. DO NOT invent arbitrary concept IDs.
4. EXACT GROUNDING: All extracted values, quotes, and note references must appear verbatim on the cited page.
5. COMPLETE RECONCILIATION: Extract all line items including subtotals (Jumlah/Total) and hierarchy indentation.
"""

STATEMENT_EXTRACTION_PROMPT = """Extract the financial statement from the provided text for:
Issuer: {ticker} ({entity_name})
Statement Type: {statement_type}
Reporting Period: {fiscal_period} {fiscal_year} (ended {period_end})
Presentation Currency: {currency}
Scale: {scale_raw} ({scale})
Number Locale: {number_locale}
Page Range: {start_page} to {end_page}

CANDIDATE CONCEPTS (Pick candidate IDs ONLY from this list):
{candidate_concepts_json}

FEW-SHOT MAPPING EXAMPLES (Use these established rules to map raw labels to canonical candidate concepts):
* Balance Sheet:
  - "Kas dan setara kas" / "Cash and cash equivalents" -> fs.bs.cash_and_equivalents
  - "Piutang usaha" / "Trade receivables" -> fs.bs.trade_receivables
  - "Persediaan" / "Inventories" -> fs.bs.inventories
  - "Aset tetap" / "Fixed assets" / "Property, plant and equipment" -> fs.bs.property_plant_equipment
  - "Tanaman menghasilkan" / "Bearer plants" -> fs.bs.bearer_plants
  - "Properti pertambangan" / "Mining properties" -> fs.bs.mining_properties
  - "Jumlah aset lancar" / "Total current assets" -> fs.bs.total_current_assets
  - "Jumlah aset tidak lancar" / "Total non-current assets" -> fs.bs.total_non_current_assets
  - "Jumlah aset" / "Total assets" -> fs.bs.total_assets
  - "Utang usaha" / "Trade payables" -> fs.bs.trade_payables
  - "Pinjaman bank" / "Utang bank" / "Bank loans" -> fs.bs.short_term_debt (or long_term_debt)
  - "Jumlah liabilitas jangka pendek" / "Total current liabilities" -> fs.bs.total_current_liabilities
  - "Jumlah liabilitas" / "Total liabilities" -> fs.bs.total_liabilities
  - "Dana syirkah temporer" / "Temporary syirkah funds" -> fs.bs.sharia.temporary_syirkah_funds
  - "Modal saham" / "Share capital" -> fs.bs.equity_parent
  - "Saldo laba" / "Retained earnings" -> fs.bs.retained_earnings
  - "Kepentingan nonpengendali" / "Non-controlling interests" -> fs.bs.non_controlling_interests
  - "Jumlah ekuitas" / "Total equity" -> fs.bs.total_equity
* Income Statement:
  - "Penjualan dan pendapatan usaha" / "Pendapatan usaha" / "Revenue" -> fs.is.revenue
  - "Beban pokok pendapatan" / "Beban pokok penjualan" / "Cost of revenue" -> fs.is.cogs
  - "Beban pengupasan tanah" / "Overburden removal" -> fs.is.cogs
  - "Laba bruto" / "Gross profit" -> fs.is.gross_profit
  - "Pendapatan bunga" / "Interest income" -> fs.is.interest_income
  - "Beban bunga" / "Interest expense" -> fs.is.interest_expense
  - "Pendapatan bunga bersih" / "Net interest income" -> fs.is.net_interest_income
  - "Beban penjualan" / "Beban umum dan administrasi" -> fs.is.operating_expenses
  - "Laba usaha" / "Operating profit" -> fs.is.operating_income
  - "Laba sebelum pajak penghasilan" / "Profit before tax" -> fs.is.profit_before_tax
  - "Beban pajak penghasilan" / "Income tax expense" -> fs.is.income_tax_expense
  - "Laba tahun/periode berjalan" / "Profit for the year" -> fs.is.profit_for_the_period
  - "Operasi yang dihentikan" / "Discontinued operations" -> fs.is.discontinued_operations
* Cash Flow Statement:
  - "Arus kas bersih yang diperoleh dari aktivitas operasi" -> fs.cf.net_cash_operating
  - "Arus kas bersih yang digunakan untuk aktivitas investasi" -> fs.cf.net_cash_investing
  - "Arus kas bersih yang digunakan untuk aktivitas pendanaan" -> fs.cf.net_cash_financing
  - "Perolehan aset tetap" / "Acquisition of fixed assets" -> fs.cf.acquisition_ppe
  - "Pembayaran dividen" / "Dividends paid" -> fs.cf.dividend_paid
  - "Kenaikan (penurunan) bersih kas dan setara kas" -> fs.cf.net_change_cash
  - "Efek perubahan nilai kurs terhadap kas" -> fs.cf.fx_effect
  - "Kas dan setara kas pada awal tahun/periode" -> fs.cf.cash_beginning
  - "Kas dan setara kas pada akhir tahun/periode" -> fs.cf.cash_ending

PAGE TEXT:
{page_text}

INSTRUCTIONS:
1. Output a single JSON object matching the StatementExtraction schema.
2. Ensure statement_type is set to '{statement_type_code}' (balance_sheet, income_statement, changes_in_equity, cash_flow).
3. In `periods`:
   - For duration statements (income_statement, cash_flow): `period_type` is "duration" and `start_date` MUST be specified (e.g. '{fiscal_year}-01-01').
   - For instant statements (balance_sheet): `period_type` is "instant" and `start_date` must be null.
4. In Cell.source, provide `page`. Omit `bbox` and `quote` to keep output concise.
5. Extract all line items with their raw cell texts exactly as printed. IMPORTANT: Every LineItem MUST have at least one Cell with a value. DO NOT output LineItem objects for section header labels, category titles, or blank rows that have empty cells (`cells: []`).
6. For Cash Flows (cash_flow), ensure you extract all major section balances:
   - Operating cash flow total -> assign candidate `fs.cf.net_cash_operating`
   - Investing cash flow total -> assign candidate `fs.cf.net_cash_investing`
   - Financing cash flow total -> assign candidate `fs.cf.net_cash_financing`
   - Net change in cash -> assign candidate `fs.cf.net_change_cash`
   - Cash at beginning of year -> assign candidate `fs.cf.cash_beginning`
   - Cash at end of year -> assign candidate `fs.cf.cash_ending`
   - Capex / Acquisition of fixed assets -> assign candidate `fs.cf.acquisition_ppe`
7. For Banking statements (income_statement):
   - Pendapatan bunga / Interest income -> assign candidate `fs.is.interest_income`
   - Beban bunga / Interest expense -> assign candidate `fs.is.interest_expense`
   - Pendapatan bunga bersih / Net interest income -> assign candidate `fs.is.net_interest_income`
   - Laba tahun/periode berjalan / Profit for the year -> assign candidate `fs.is.profit_for_the_period`
8. In `concept_candidates`, assign at most 1 primary candidate per line item to keep payload size optimal.
"""

AUDIT_REPORT_PROMPT = """You are extracting the Independent Auditor's Report for:
Issuer: {ticker} ({entity_name})
Fiscal Year: {fiscal_year}
Page Range: {start_page} to {end_page}

PAGE TEXT:
{page_text}

INSTRUCTIONS:
Extract the Independent Auditor's Report information:
- audit_firm_raw: Name of Public Accounting Firm (KAP) exactly as printed
- network_raw: Global audit network if mentioned (PwC, EY, KPMG, Deloitte, BDO, RSM, etc.)
- signing_partner_raw: Name of signing Public Accountant / partner
- report_date: Audit opinion date (YYYY-MM-DD)
- opinion: 'unmodified' (wajar tanpa pengecualian / clean), 'qualified' (wajar dengan pengecualian), 'adverse' (tidak wajar), or 'disclaimer' (tidak menyatakan pendapat)
- emphasis_of_matter: true if Penekanan Suatu Hal / Emphasis of Matter paragraph is present, else false
- material_uncertainty_going_concern: true if going concern uncertainty paragraph is present, else false
- key_audit_matters: list of Key Audit Matter (Hal Audit Utama / KAM) titles only
- source: SourceRef with page number
"""

KPI_EXTRACTION_PROMPT = """You are an expert equity research and operational due-diligence analyst extracting Operational KPIs and physical production/sales metrics from Indonesian corporate Annual Reports (MD&A / Tinjauan Operasional).
Issuer: {ticker} ({entity_name})
Reporting Period: {fiscal_period} {fiscal_year} (ended {period_end})
Page Range: {start_page} to {end_page}

CANDIDATE KPI CONCEPTS (Map to these standard concepts when applicable, or use standard naming op.<sector>.<metric>):
{candidate_kpis_json}

MANDATORY HIGH-RECALL EXTRACTION GUIDELINES:
1. EXTRACT ALL REPORTED BUSINESS SEGMENTS & PRODUCTS:
   Annual Reports and MD&A chapters review multiple distinct business segments, operational divisions, and subsidiaries. You MUST extract operational facts across EVERY business segment discussed, including:
   - Mining, Metals & Natural Resources: Ore mined/extracted, ore milled/processed, waste/overburden removal (bcm/Mbcm), stripping ratio, recovery rate (%), head grade (% or g/t), concentrate produced, refined metal output (tonnes, kg, oz, lbs), coal sales, nickel sales (wmt/dmt, saprolite/limonite), Average Selling Price (ASP), unit cash cost (C1/AISC), certified reserves and resources.
   - Agribusiness & Plantations: Planted hectarage (mature vs immature, nucleus vs plasma), FFB production and yield (ton/ha), CPO and PK production, Oil Extraction Rate (OER %), palm oil mill processing capacity and utilization, CPO ASP.
   - Power Generation, Utilities & Infrastructure: Electricity generated and sold (MWh/GWh), steam sales, installed power capacity (MW), toll road length (km), average daily traffic (ADT), vessel fleet count, cargo volume/throughput.
   - Distribution, Logistics & Industrial Land: Petroleum/chemical distribution volumes (kL, MT), storage tank capacity, industrial estate land bank and land sold (ha), logistics fleet / vessels / trucks count.
   - Manufacturing & Industrial: Installed production capacity, capacity utilization rate (%), total production volume, sales volume by product line.
   - Retail & Consumer Services: Store network count, new store openings, net selling area (sqm), Same-Store Sales Growth (SSSG %), transactions count, basket size.
   - Banking & Financial Services: CASA ratio (%), gross/net NPL (%), CAR / capital adequacy (%), LDR/FDR (%), NIM (%), BOPO (%), total loans, customer deposits.
   - Mining Services & Construction: Overburden removal (Mbcm), coal getting / mined (Mt), heavy equipment fleet count (excavators, dump trucks), contract backlog.

2. MULTI-YEAR COMPARATIVE DATA:
   Operational tables present comparative figures across multiple years (e.g. 2025, 2024, 2023).
   Extract operational KPI facts for ALL reported years present in the table or text.
   For each fact, set `period`:
   - Duration (annual total e.g. production, sales, overburden): period_type="duration", start_date="{fiscal_year}-01-01" (or corresponding comparative year start date), end_date="{fiscal_year}-12-31", fiscal_year={fiscal_year}, fiscal_period="FY".
   - Instant (year-end e.g. reserves, capacity, planted area, store count, fleet count): period_type="instant", start_date=null, end_date="{fiscal_year}-12-31", fiscal_year={fiscal_year}, fiscal_period="FY".

3. VERBATIM ACCURACY & UNITS:
   - Extract raw_text EXACTLY as printed on the page (e.g., "30,59", "188.581", "235.50", "4.023"). DO NOT round or convert numbers.
   - Extract unit_raw EXACTLY as printed (e.g., "juta wmt", "ton", "Mbcm", "ha", "MW", "GWh", "US$/oz", "%").
   - Extract kpi_label_raw preserving bilingual or reported phrasing.

4. CANDIDATE CONCEPT MAPPING:
   - In `kpi_candidates`, provide at most 1 primary candidate from the candidate list if there is a match (e.g. op.nickel.ore_production, op.coal.overburden_removal, op.plant.cpo_production, op.cugold.ore_milled, op.core.installed_capacity).
   - If no candidate directly matches, assign a clean namespaced concept (e.g. op.mining.copper_cathode_production).

5. DIMENSIONS & ATTRIBUTES:
   - In `dims`, include segment, commodity, facility, or product when stated in column or row headers.
   - In `basis`, specify "gross_100pct" or "attributable" if stated, otherwise "not_stated".
6. SCOPE & VOLUME:
   - Extract the 15 to 25 most significant consolidated and segment operational KPIs and production/sales figures.
   - Focus on key operational totals (total production, sales volume, planted area, processing mills, recovery rate, capacity) rather than reciting repetitive minor sub-estate or sub-branch tables.
   - Do not output duplicate facts. Keep the output focused and under 25 items.

7. CONCISE JSON OUTPUT FORMAT (CRITICAL TO PREVENT TOKEN TRUNCATION):
   - In `source`: specify ONLY `page` (an integer). Always leave `quote=null`, `bbox=null`, `section_path=null`, and `printed_page=null`.
   - Always leave `definition_text=null`.
   - In `kpi_candidates`: specify at most 1 candidate.
   - Output valid, compact JSON.

PAGE TEXT:
{page_text}

Output a list of KPIFact objects wrapped in a KPIExtractionResult container.
"""


OFFICER_POSITION_PROMPT = """You are extracting Board of Directors and Board of Commissioners governance profiles for:
Issuer: {ticker} ({entity_name})
Fiscal Year: {fiscal_year}
Page Range: {start_page} to {end_page}

PAGE TEXT:
{page_text}

Extract each Director and Commissioner with their person_name_raw, role (president_director, director, president_commissioner, commissioner, independent_commissioner), and independence status.
"""

FORWARD_GUIDANCE_PROMPT = """You are extracting management forward guidance, outlook, and capex targets from the Annual Report for:
Issuer: {ticker} ({entity_name})
Fiscal Year: {fiscal_year}
Page Range: {start_page} to {end_page}

PAGE TEXT:
{page_text}

Extract each forward guidance target with metric_label_raw, scenario (guidance/target/budget), point_raw or low/high range, unit_raw, target_period_raw (e.g. 'tahun 2026', 'FY2026'), and funding_source_raw if capex.
"""

CONCEPT_MAPPING_PROMPT = """You are an expert Indonesian financial taxonomist specializing in IDX (Indonesia Stock Exchange) filings.
Your task is to map a novel reported line item label to either a canonical taxonomy concept or a standardized extension concept.

CANDIDATE TAXONOMY CONCEPTS:
{candidate_concepts_json}

FEW-SHOT EXAMPLES ACROSS DIVERSE INDUSTRIES:
1. Mining / Natural Resources:
   - "Beban pengupasan tanah" -> {{"concept_id": "fs.is.cogs", "confidence": 0.95, "reasoning": "Overburden removal is a direct production cost recognized in COGS"}}
   - "Properti pertambangan" -> {{"concept_id": "fs.bs.mining_properties", "confidence": 0.98, "reasoning": "Mining exploration, evaluation, and development assets"}}
   - "Beban royalti" -> {{"concept_id": "fs.is.cogs", "confidence": 0.90, "reasoning": "Production royalties to government are included in cost of revenue"}}

2. Banking & Financial Institutions:
   - "Pendapatan bunga dan syariah" -> {{"concept_id": "fs.is.interest_income", "confidence": 0.98, "reasoning": "Interest and sharia financing income"}}
   - "Beban bunga dan bagi hasil" -> {{"concept_id": "fs.is.interest_expense", "confidence": 0.98, "reasoning": "Interest expense and profit sharing distributions"}}
   - "Jumlah dana syirkah temporer" -> {{"concept_id": "fs.bs.sharia.temporary_syirkah_funds", "confidence": 1.0, "reasoning": "PSAK 101 Sharia profit-sharing investment deposits"}}
   - "Penyisihan kerugian penurunan nilai" -> {{"concept_id": "fs.is.operating_expenses", "confidence": 0.92, "reasoning": "Allowance for expected credit losses under PSAK 71"}}

3. Agribusiness & Plantations:
   - "Tanaman menghasilkan" -> {{"concept_id": "fs.bs.bearer_plants", "confidence": 0.98, "reasoning": "Mature bearer plants under PSAK 69 Agriculture"}}
   - "Tanaman belum menghasilkan" -> {{"concept_id": "fs.bs.property_plant_equipment", "confidence": 0.90, "reasoning": "Immature biological assets capitalized under PPE"}}
   - "Penjualan minyak kelapa sawit dan inti sawit" -> {{"concept_id": "fs.is.revenue", "confidence": 0.99, "reasoning": "CPO and palm kernel sales are primary operating revenue"}}

4. Transportation, Ports & Infrastructure:
   - "Pendapatan jasa kebandarudaraan" -> {{"concept_id": "fs.is.revenue", "confidence": 0.99, "reasoning": "Aeronautical airport services are core operational revenue"}}
   - "Pendapatan jasa pelayanan kapal dan barang" -> {{"concept_id": "fs.is.revenue", "confidence": 0.99, "reasoning": "Vessel and cargo handling services are core port revenue"}}
   - "Aset konsesi jasa" -> {{"concept_id": "fs.bs.intangible_assets", "confidence": 0.95, "reasoning": "ISAK 16 Service concession arrangements"}}

5. Non-Standard / Industry Extensions:
   - If no canonical concept accurately captures the item, assign a clean namespaced extension: `ext.<ticker_or_sector>.<slug>`
   - Example: "Dana jaminan reklamasi" -> {{"concept_id": "ext.mining.reclamation_guarantee_fund", "confidence": 0.85, "reasoning": "Environmental escrow deposit specific to mining operations"}}

TARGET LABEL TO MAP:
- Raw Label (ID): {label_raw}
- Alternative Label (EN): {label_raw_alt}
- Statement Type: {statement_type}
- Filing Context: {context}

Return a single JSON object with:
{{
  "concept_id": "<canonical_or_extension_id>",
  "confidence": <float_between_0_and_1>,
  "reasoning": "<brief explanation referencing accounting logic>"
}}
"""

LLM_VALIDATION_RECONCILIATION_PROMPT = """You are an expert senior financial auditor specializing in Indonesian Financial Accounting Standards (SAK / PSAK) and IDX filings.

An automated accounting identity check has identified a discrepancy:
Rule ID: {rule_id}
Accounting Period: {period}
Reported LHS: {lhs}
Reported RHS: {rhs}
Difference: {diff}
Implicated Line Items:
{implicated_facts_json}

RELEVANT STATEMENT PAGE TEXT:
{page_text}

TASK:
Analyze the reported statement line items on the provided pages. Determine whether this difference is explained by a legitimate non-standard accounting line item or presentation standard (for example:
- Sharia Banking: Temporary Syirkah Funds (Dana Syirkah Temporer under PSAK 101)
- Discontinued Operations: Profit/loss from discontinued operations after tax (PSAK 58)
- Cash Flow Reconciliation: Foreign exchange translation gain/loss on cash (PSAK 2)
- Multi-component Subtotals: Non-controlling interests or specialized statutory reserves).

Return a JSON object:
{{
  "has_reconciling_item": true | false,
  "reconciling_concept": "<concept_id_or_empty>",
  "reconciling_label": "<verbatim_reported_label_or_empty>",
  "reconciling_value": <exact_numeric_value_as_reported_or_0>,
  "reconciling_sign": "+1" | "-1",
  "accounting_standard": "<e.g. PSAK 101, PSAK 58, PSAK 2, or SAK General>",
  "explanation": "<concise professional audit explanation>",
  "is_algebraically_sound": true | false
}}
"""

FILING_METADATA_PROMPT = """You are an expert indexer of Indonesian corporate filings.
Extract filing metadata from the cover and general information pages of this filing.

PAGE TEXT:
{page_text}

INSTRUCTIONS:
Extract the following metadata as a JSON object:
- ticker: Stock ticker symbol (e.g. 'NCKL', 'BBCA', 'ADRO'). If not explicitly stated, infer from legal entity name.
- legal_name: Full registered corporate entity name (e.g. 'PT Bank Central Asia Tbk').
- doc_type: 'fs_annual' (audited annual LK), 'fs_interim' (quarterly/half-year LK), 'annual_report' (Laporan Tahunan), or 'sustainability_report'.
- fiscal_year: Year of reporting (e.g. 2025).
- period_end: Date of period end in 'YYYY-MM-DD' format (e.g. '2025-12-31', '2025-09-30').
- currency: ISO 4217 presentation currency code (e.g. 'IDR', 'USD').
- scale: Scale factor for presented numbers:
    * 1 for full units ('satuan penuh' or unscaled)
    * 1000 for thousands ('ribuan' / 'in thousands')
    * 1000000 for millions ('jutaan' / 'in millions')
    * 1000000000 for billions ('miliar' / 'in billions')
    * 1000000000000 for trillions ('triliun' / 'in trillions')
- number_locale: 'id' (uses dot for thousands, comma for decimals: 1.234,56) or 'en' (uses comma for thousands, dot for decimals: 1,234.56).
- presentation_format: 'classified' (standard current vs non-current split) or 'order_of_liquidity' (banks, financial institutions, insurance companies).
- accounting_regime: 'general_corporate', 'conventional_banking', 'sharia_banking', or 'insurance_psak117'.

Return ONLY valid JSON.
"""

TOC_EXTRACTION_PROMPT = """You are an expert indexer of Indonesian corporate Annual Reports.
Parse this Table of Contents (Daftar Isi) text and identify the exact starting and ending page numbers for key sections.

PAGE TEXT:
{page_text}

INSTRUCTIONS:
Find the 1-based page numbers for:
1. "ar.highlights": Financial & Operational Highlights (Ikhtisar Keuangan dan Operasional, Kilas Balik Kinerja, Jejak Langkah)
2. "ar.mdna.kpis": Management Discussion & Analysis / Operational Review (Analisis dan Pembahasan Manajemen, Tinjauan Operasional per Segmen)
3. "ar.governance.officers": Board of Directors & Commissioners Profiles (Profil Direksi dan Dewan Komisaris, Tata Kelola Perusahaan)
4. "fs.primary": Audited Financial Statements (Laporan Keuangan Konsolidasian / Laporan Auditor Independen)

Return a JSON object:
{{
  "sections": [
    {{
      "section_type": "ar.highlights" | "ar.mdna.kpis" | "ar.governance.officers" | "fs.primary",
      "title": "<chapter_title_as_listed>",
      "start_page": <int_page_number>,
      "end_page": <int_page_number>
    }}
  ]
}}
"""

