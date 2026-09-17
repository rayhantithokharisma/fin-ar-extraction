# IDX Statement & Annual Report Extraction System — Complete Design Log

## 1. Architectural Philosophy & Core Principles

The IDX Statement Extraction pipeline is an enterprise-grade AI extraction engine engineered to ingest audited financial statements (LK) and 400+ page annual reports (AR) published by Indonesian public companies (Emiten) listed on the Indonesia Stock Exchange (IDX).

### Six Fundamental Design Decisions

1. **Industry Variance Lives in Dictionaries, Not Columns**:
   - Traditional extraction pipelines fail by creating wide, hardcoded relational tables like `bank_balance_sheet` or `mining_operational_report`. These brittle structures break the moment a company diversifies, an insurance firm is added, or an issuer changes line item presentation.
   - Instead, the pipeline follows the XBRL model: Every data point is an atomic **fact**:
     $$( \text{issuer}, \text{concept}, \text{period\_end}, \text{dimensions}, \text{scenario} ) \longrightarrow ( \text{value}, \text{unit}, \text{provenance}, \text{assurance} )$$
   - Adding a new industry sector (such as Geothermal Energy, Sharia Banking, or Nickel HPAL) requires **zero DDL changes**—only additional semantic mapping concepts.

2. **Split by Output Shape, Not Source Section**:
   - Source filings mix tabular data and narrative prose unpredictably: MD&A chapters contain mining production tables; Notes to Financial Statements contain textual debt covenant clauses.
   - Extraction decomposes documents into three clean shapes:
     - **Facts**: Time-series numeric values tied to accounting periods (statements, note disclosures, KPI rollforwards, ESG statistics).
     - **Records**: Persistent, multi-attribute entities (debt facilities, bank accounts, board members, subsidiaries).
     - **Assertions**: Textual claims and strategic commitments (guidance, risk factors, accounting policies).

3. **Preserve As-Reported Data, Decouple Concept Mapping**:
   - The extraction layer records raw text strings, row labels, original bilingual strings (Bahasa Indonesia & English), note references, and table structures exactly as printed on the PDF page.
   - Mapping raw labels to canonical concept IDs (`concept_id`) is an independent post-processing step. When taxonomy definitions evolve, data is remapped in milliseconds without costly LLM re-extractions.
   - Any line item lacking a canonical standard mapping automatically becomes a namespaced extension concept: `ext.<TICKER>.<slug>`, ensuring zero data loss.

4. **Deterministic Math, Probabilistic Structure**:
   - LLMs are reserved for what they excel at: spatial orientation, contextual reading, layout comprehension, and semantic tagging.
   - All arithmetic, number parsing, scaling, currency conversion, and balance sheet equation checks are executed in **strictly deterministic Python code**.
   - Numbers are parsed with locale awareness (e.g. Indonesian `1.234,50` vs English `1,234.50`), parenthesized negative accounting conventions, and nil dash markers.

5. **Multi-Stage Grounding & Verbatim Provenance**:
   - Every extracted fact carries exact document metadata: file SHA-256 hash, page number, section identifier, table coordinates, model name, and prompt hash.
   - Every extracted value is checked against the raw text layer of the cited page using the `grounded()` rule. Any hallucinated or fabricated figure is immediately intercepted.

6. **Medallion Data Lakehouse Architecture in DuckDB**:
   - The persistence layer follows the Medallion architecture:
     - `idxfin_ref`: Static taxonomy concepts, template definitions, and validation rules.
     - `idxfin_bronze`: Raw execution logs, uploaded PDF documents, and unparsed LLM JSON outputs.
     - `idxfin_silver`: Normalized, scale-adjusted, type-cast facts and governance records.
     - `idxfin_gold`: Deduplicated analytical views (`fact_latest`, `financial_summary`, `financial_summary_provenance`) with built-in time-travel and restatement handling.

---

## 2. End-to-End System Architecture

<p align="center">
  <img src="img/system_architecture.png" alt="IDX Financial Statement, Market Data & Conversational GenBI Pipeline Architecture" width="100%" />
</p>

The extraction pipeline processes raw corporate filings, macroeconomic market benchmarks, and conversational BI inquiries through seven sequential, decoupled architectural phases:

- **1. Document Ingestion & Context Detection (`src/parser/pdf_loader.py`)**:
  - **PDF Text & Layout Extraction**: Ingests raw PDF documents using PyMuPDF and pypdf to extract embedded text layers, document metadata, font hierarchies, and page layouts.
  - **Metadata Sniffing**: Inspects headers and cover pages to determine the issuer ticker, legal corporate name, document type (`fs_annual`, `fs_interim`, `annual_report`), reporting period end date, reported currency (`IDR` vs `USD`), scale multiplier (1, 1,000, 1,000,000, 1,000,000,000), and number formatting locale (`id` vs `en`).
  - **Sectionizer & Span Router (`src/parser/sectionizer.py`)**: Scans IDX XBRL taxonomy header codes (e.g. `[1210000]` for balance sheets, `[1312000]` for income statements, `[1510000]` for cash flows) and Annual Report chapter titles to identify discrete section boundaries across multi-hundred page documents.

- **2. Prompt Engine & Structured LLM Extraction (`src/extraction/`)**:
  - **Prompt Construction**: Dynamically assembles system analyst instructions, section-specific textual contexts, target XBRL line item definitions, and bilingual translation rules.
  - **Pydantic Schema Enforcement (`schema_design/extraction_contracts.py`)**: Directs LLM generation through strict Pydantic v2 schemas (`StatementExtraction`, `TableContext`, `ExtractedLineItem`, `ExtractedCell`) via OpenRouter API.
  - **High-Token Budgeting**: Configured with a `max_tokens=32,768` limit to support massive statements (such as 150-item banking balance sheets spanning 9 pages) in a single invocation without truncation.
  - **Resilience & Auto-Repair**: Intercepts validation errors or incomplete JSON outputs, applying error-correction prompts and structural repair routines.
  - **Bronze Persistence**: Preserves unedited raw LLM JSON responses, execution latency, token counts, and run hashes in `idxfin_bronze.extraction_run`.

- **3. Normalization & Verbatim Grounding (`src/normalization/`)**:
  - **Deterministic Number Parsing (`src/normalization/number_parser.py`)**: Converts raw strings to `Decimal` types with locale-aware thousand and decimal separators, resolves parenthesized strings `"(1,237)"` to negative values, and treats accounting dashes as zero.
  - **Scale Adjustment**: Multiplies parsed numbers by document scale (e.g. $\times 1,000,000$ for million IDR, $\times 1,000$ for thousand USD) into standardized base units.
  - **Verbatim Grounding Validator**: Verifies that every extracted numeric string exists verbatim on its cited source page (or adjacent boundary pages) via `grounded()`, eliminating hallucinations.
  - **Taxonomy Concept Mapping (`src/mapping/concept_mapper.py`)**: Maps raw bilingual labels to canonical concepts (`fs.bs.*`, `fs.is.*`, `fs.cf.*`, `fs.bs.bank.*`) using semantic dictionaries, fuzzy matching, and automatic fallbacks to `ext.<ticker>.<slug>` extension concepts.
  - **Silver Storage**: Loads normalized, typed facts and governance records into `idxfin_silver.fact` and `idxfin_silver.officer_position`.

- **4. Multi-Layer Accounting Validation Engine (`src/validation/engine.py`)**:
  - **Rule Verification**: Evaluates 33 accounting equations across Hard rules (zero tolerance within scale bounds) and Soft rules (quality warnings).
  - **Balance Sheet Identity**: Confirms $\text{Total Assets} = \text{Total Liabilities} + \text{Total Equity} + \text{Temporary Syirkah Funds}$ (PSAK 101).
  - **Classification & Statement Splits**: Validates current vs non-current asset splits, operating cash flow rollforwards, and net profit reconciliation ($\text{NP} = \text{PBT} - \text{Tax}$).
  - **Quality Scoring & Review Routing**: Generates pass rates and automatically flags anomalies or non-reconciled rows for review.

- **5. Gold Analytical Warehouse & Consumption Layer (`src/storage/gold_views.py`)**:
  - **Deduplicated Actuals (`idxfin_gold.fact_latest`)**: Uses SQL window functions partitioned by `(issuer_id, period_end, concept_id, dim_key)` ordered by restatement status, lineage page, and fact ID to resolve superseding disclosures.
  - **Standardized Summary (`idxfin_gold.financial_summary`)**: Aggregates standardized multi-company financial metrics (assets, liabilities, equity, revenues, net profit, cash flow, capex, bank deposits, bank loans) with native multi-currency support (`IDR` vs `USD`).
  - **Provenance & Lineage Tracking (`idxfin_gold.financial_summary_provenance`)**: Pairs each financial metric with its source PDF page citation (e.g. `p.8`, `p.16`) for immediate auditability.
  - **Operational KPIs (`idxfin_gold.operational_kpi`)**: Isolates physical production, sales, and operational volume metrics (e.g. Ammonia tonnes, LPG tonnes, Gold oz).
  - **Cross-Asset Valuation (`idxfin_gold.market_valuation`)**: Joins fundamental statement dates with period-end closing equity prices to compute performance % and multiples.
  - **Foreign Exchange Rates (`idxfin_gold.fx_rate_daily`)**: Provides daily spot rates with 30-day and 365-day moving averages for currency translation.

- **6. Market Data, Commodities & FX Ingestion (`src/market_data/yfinance_loader.py`)**:
  - **Equity Trading Bars (`idxfin_market.stock_daily`)**: 4 years of daily OHLCV bars across Indonesian issuers (`.JK`).
  - **Physical Ammonia Benchmark (`NH3=F`)**: 1,821 daily resampled bars from historical spot assessments ($/ton).
  - **Global Energy & Proxies**: Gold (`GC=F`), WTI (`CL=F`), Brent (`BZ=F`), Natural Gas (`NG=F`), and corporate proxies (`ESSA.JK`, `CF`).
  - **Spot FX Rates (`idxfin_market.fx_daily`)**: 1,039 daily trading bars of USD/IDR (`IDR=X`).

- **7. Conversational GenBI & Agentic Semantic Layer (`wren/chat_agent.py`)**:
  - **Wren AI Engine & MDL Models**: Governed Modeling Definition Language contracts abstracting SQL logic, relationships, and calculated metrics.
  - **Qdrant Vector Database**: Vector store indexing schema definitions, descriptions, and few-shot SQL patterns.
  - **LangGraph ReAct Agent**: Autonomous multi-turn agent with tool calling (`wren_ask_bi`, `wren_list_models`, `duckdb_query`) grounded by accounting rules.
  - **Wren UI**: Web-based analytical canvas on port 3000 for visual SQL exploration and dashboarding.

---

## 3. Detailed Dataflow by Medallion Layer

### Layer 1: Bronze (Raw Ingestion & Provenance)
- **Table `idxfin_bronze.document`**:
  Stores the document catalog: SHA-256 fingerprint, source file path, ticker, document type (`fs_annual`, `fs_interim`, `annual_report`), fiscal year, period end date, reported currency (`IDR`, `USD`), scale multiplier, and raw page count.
- **Table `idxfin_bronze.extraction_run`**:
  Tracks individual execution runs: timestamp, target section ID, LLM model used, prompt tokens, completion tokens, latency, and full unparsed raw JSON response.

### Layer 2: Silver (Normalized, Cleaned Facts & Records)
- **Table `idxfin_silver.fact`**:
  - Grain: One normalized numeric fact per row.
  - Columns: `fact_id`, `issuer_id`, `doc_id`, `concept_id`, `label_raw`, `label_raw_alt`, `period_start`, `period_end`, `dim_key`, `scenario`, `value`, `unit`, `scale_reported`, `decimals`, `is_restated`, `lineage_page`, `lineage_table_coord`, `confidence`.
- **Table `idxfin_silver.officer_position`**:
  - Board members and executive profiles extracted from governance chapters.
  - Columns: `person_id`, `issuer_id`, `full_name`, `position_raw`, `position_norm`, `board_type` (Direksi vs Dewan Komisaris), `is_independent`, `tenure_start`, `lineage_page`.

### Layer 3: Gold (Analytical & Consumption Views)
- **View `idxfin_gold.fact_latest`**:
  Uses SQL window functions to resolve duplicates and prioritize the latest restated filing:
  ```sql
  ROW_NUMBER() OVER (
      PARTITION BY issuer_id, period_end, concept_id, dim_key
      ORDER BY is_restated DESC, lineage_page DESC, fact_id DESC
  )
  ```
- **View `idxfin_gold.financial_summary`**:
  Pivots the latest facts across all 14 companies into standardized financial metrics:
  `total_assets`, `current_assets`, `total_liabilities`, `current_liabilities`, `total_equity`, `temporary_syirkah_funds`, `revenue`, `cost_of_revenue`, `gross_profit`, `net_profit`, `operating_cash_flow`, `capex`, `interest_income`, `bank_loans`, and `bank_deposits`.
- **View `idxfin_gold.financial_summary_provenance`**:
  Mirrors the summary table but provides exact PDF page citations (e.g. `p.8`, `p.16`) for every single metric, creating an instant verification trail.

---

## 4. Extraction Engine Deep Dive

### 4.1 Document Loading & Metadata Sniffing (`src/parser/pdf_loader.py`)
IDX reports contain metadata encoded on cover pages and headers. The `PDFDocument` class extracts:
- **Currency**: Detected from header regex (`[1000000]` block):
  - `"Dolar Amerika / USD"` or `"United States Dollar"` $\longrightarrow$ `USD`
  - `"Rupiah / IDR"` $\longrightarrow$ `IDR`
- **Scale**:
  - `"Satuan penuh"` or `"In full amount"` $\longrightarrow$ `1`
  - `"Dalam ribuan"` or `"In thousand"` $\longrightarrow$ `1,000`
  - `"Dalam jutaan"` or `"In million"` $\longrightarrow$ `1,000,000`
  - `"Dalam miliaran"` or `"In billion"` $\longrightarrow$ `1,000,000,000`
- **Locale**:
  - Automatically configured based on header language keywords: English (`en`) uses `,` for thousands and `.` for decimals; Indonesian (`id`) uses `.` for thousands and `,` for decimals.

### 4.2 Sectionizer & Span Router (`src/parser/sectionizer.py`)
IDX financial statements follow the IDX XBRL taxonomy code format: `[XNNNNNN]`. The `Sectionizer` identifies primary financial statements by inspecting the 6-digit tail:
- **Balance Sheet / Financial Position**: Second digit `2` (e.g. `[1210000]`, `[3210000]`, `[4220000]`).
- **Profit or Loss / Income Statement**: Second digit `3` (e.g. `[1311000]`, `[1312000]`, `[1321000]`, `[3311000]`, `[4312000]`, `[4322000]`).
- **Statement of Changes in Equity**: Second digit `4` (e.g. `[1410000]`, `[4410000]`).
- **Statement of Cash Flows**: Second digit `5` (e.g. `[1510000]`, `[4510000]`).
- **Notes to Financial Statements**: Second digit `6` (e.g. `[1610000]`, `[4610000]`).

For Annual Reports and Sustainability Reports, the sectionizer uses robust regex pattern scanning to locate chapters (Financial Highlights, Operational Reviews, Governance, Risk Management, ESG Performance).

---

## 5. Prompt Engineering & LLM Interaction

### 5.1 System Prompt Design
The prompt sets an uncompromising role definition:
```text
You are a senior financial analyst and expert financial data extraction engine specializing in Indonesian public company (IDX) filings under IFRS and PSAK standards.
Your mission:
1. Extract line items verbatim as printed.
2. Read bilingual text: preserve both Indonesian and English labels.
3. Preserve reported column periods (current year, comparative year).
4. Maintain parent-child line item hierarchies (e.g. current assets vs non-current assets).
5. Extract exact unformatted cell strings into raw_text without rounding or guessing.
```

### 5.2 Pydantic Schema Contracts (`schema_design/extraction_contracts.py`)
Extraction outputs are strictly constrained to Pydantic v2 data models:
- `StatementExtraction`:
  - `context`: Reporting entity, period end date, currency, scale multiplier, presentation currency.
  - `periods`: List of periods reported across table columns.
  - `line_items`: Array of `ExtractedLineItem`:
    - `line_item_id`: Unique section-level identifier.
    - `label_raw`: Primary label (e.g. "Jumlah aset").
    - `label_raw_alt_lang`: Alternate bilingual label (e.g. "Total assets").
    - `note_ref`: Footnote reference number.
    - `parent_line_item_id`: Tree hierarchy pointer.
    - `is_total`: Boolean indicating subtotal or grand total.
    - `cells`: Array of `ExtractedCell` containing `period_id`, `raw_text`, and coordinates.

### 5.3 Token Capacity Management
Mega-cap commercial banks (e.g. **BBCA**) have balance sheets ordered by liquidity containing over 150 individual sub-accounts spanning 9 consecutive pages (conventional vs sharia instruments, placements, derivatives, loans by segment, deposits by product).
- We set `max_tokens=32,768` in `src/llm/client.py` and `src/extraction/statements.py`.
- This ensures full extraction of complex multi-page statements in a single structured invocation without truncation.

---

## 6. Normalization & Verbatim Grounding

### 6.1 Multi-Locale Number Parsing (`src/normalization/number_parser.py`)
Financial numbers arrive in diverse formats:
- Parentheses represent negative values: `"(1,237)"` $\longrightarrow$ `-1237`.
- Accounting dashes represent nil: `"-"` $\longrightarrow$ `0`.
- Locale parsing converts digit strings accurately:
  - English locale (`en`): `"1,874,500"` $\longrightarrow$ `Decimal("1874500")`.
  - Indonesian locale (`id`): `"1.874.500"` $\longrightarrow$ `Decimal("1874500")`.
- Scale multiplication:
  $$\text{Value}_{\text{normalized}} = \text{Value}_{\text{reported}} \times \text{Scale}$$

### 6.2 Strict Verbatim Grounding Rule
To eliminate hallucinations, the `grounded(raw_text, page_text)` function strips currency symbols, thousand separators, and whitespace, verifying that the exact character sequence exists on the cited PDF page. If a table wraps across page boundaries, the validator searches adjacent pages ($\pm 1$ page).

---

## 7. Concept Mapping & Domain Taxonomies

### 7.1 Cross-Industry Canonical Concepts (`src/mapping/concept_mapper.py`)
The pipeline standardizes terminology across 11 distinct industries into unified canonical IDs:
1. **Energy & Coal Mining (ADRO, ABMM)**:
   - Coal extraction, contractor services, overburden removal $\longrightarrow$ `fs.is.revenue`.
   - Stripping costs, mining expenses $\longrightarrow$ `fs.is.cost_of_revenue`.
2. **Renewable Energy (BREN)**:
   - Electricity sales (geothermal & wind) $\longrightarrow$ `fs.is.revenue`.
   - Steam production & sales $\longrightarrow$ `fs.is.revenue`.
3. **Mega-Cap Commercial Banking (BBCA, BBSI)**:
   - Interest income (conventional + sharia) $\longrightarrow$ `fs.is.interest_income`.
   - Cost of funds / Interest expense $\longrightarrow$ `fs.is.interest_expense`.
   - Loans gross (Kredit yang diberikan) $\longrightarrow$ `fs.bs.bank.loans_gross`.
   - Customer deposits: Demand deposits (`giro`), Savings (`tabungan`), Time deposits (`deposito`) $\longrightarrow$ `fs.bs.bank.*`.
   - Placements with Bank Indonesia $\longrightarrow$ `fs.bs.bank.placements`.
4. **Agribusiness & Plantations (LSIP)**:
   - Bearer plants (Tanaman menghasilkan) $\longrightarrow$ `fs.bs.bearer_plants`.
   - Immature plantations $\longrightarrow$ `fs.bs.immature_plantations`.
   - Biological assets $\longrightarrow$ `fs.bs.biological_assets`.
   - CPO and palm kernel sales $\longrightarrow$ `fs.is.revenue`.
5. **Aviation & Airport Logistics (CASS)**:
   - Aviation ground handling & in-flight catering $\longrightarrow$ `fs.is.revenue`.
   - Cargo & warehousing revenues $\longrightarrow$ `fs.is.revenue`.
6. **Technology & Systems Integration (ATIC)**:
   - IT hardware, software licensing, integration services $\longrightarrow$ `fs.is.revenue`.
7. **Marine Towage & Port Infrastructure (IPCM)**:
   - Vessel towage & pilotage services $\longrightarrow$ `fs.is.revenue`.
8. **Retail & Specialty Stores (MAPI)**:
   - Retail sales across fashion, sports, department stores, and F&B $\longrightarrow$ `fs.is.revenue`.
   - Merchandise inventories and store right-of-use leases $\longrightarrow$ `fs.bs.inventories`, `fs.bs.right_of_use_assets`.
9. **Industrial Estates & Property Development (DMAS)**:
   - Industrial land plots (Kota Deltamas, Data Center Zone) $\longrightarrow$ `fs.is.revenue`.
   - Real estate inventories and investment properties $\longrightarrow$ `fs.bs.inventories`, `fs.bs.investment_properties`.
10. **Precious Metals & Pure Gold Mining (ARCI)**:
    - Pure gold and silver bullion doré sales (reported in USD) $\longrightarrow$ `fs.is.revenue`.
    - Mining properties, gold ore reserves, exploration assets $\longrightarrow$ `fs.bs.mining_properties`.
11. **Agrochemicals & Crop Science (BISI)**:
    - Hybrid corn seeds, horticulture seeds, crop protection chemicals, fertilizers $\longrightarrow$ `fs.is.revenue`.

### 7.2 PSAK 101 Sharia Accounting (Temporary Syirkah Funds)
Under Indonesian Islamic Financial Accounting Standards (PSAK 101), institutions with Islamic operations (such as Bank Central Asia's sharia subsidiary, BCA Syariah) must present **Dana Syirkah Temporer** (Temporary Syirkah Funds) as a distinct category between liabilities and equity:
$$\text{Total Assets} = \text{Total Liabilities} + \text{Total Equity} + \text{Temporary Syirkah Funds}$$
- BBCA reported **IDR 10,632,695,000,000** (2025) and **IDR 9,486,817,000,000** (2024) in Syirkah funds.
- Mapping this to `fs.bs.sharia.temporary_syirkah_funds` enabled the balance sheet equation to reconcile to **exact zero difference** across all reported periods.

---

## 8. Multi-Layer Validation Engine (`src/validation/engine.py`)

The validation engine evaluates 33 accounting equations divided into **Hard** rules (must pass with 0 difference within scale tolerance) and **Soft** rules (flagged for review):

| Rule ID | Severity | Formula Evaluated | Description |
|---|---|---|---|
| `bs_identity` | **Hard** | $\text{Assets} = \text{Liabilities} + \text{Equity} + \text{Syirkah}$ | Primary Balance Sheet equation |
| `bs_current_split` | **Hard** | $\text{Assets} = \text{Current Assets} + \text{Non-Current Assets}$ | Classified Balance Sheet split |
| `net_profit` | **Hard** | $\text{Net Profit} = \text{PBT} - \text{Tax Expense}$ | Income statement bottom-line reconciliation |
| `cf_sections` | **Hard** | $\Delta \text{Cash} = \text{CFO} + \text{CFI} + \text{CFF}$ | Statement of Cash Flows reconciliation |
| `gross_profit` | Soft | $\text{Gross Profit} = \text{Revenue} - \text{COGS}$ | Trading margin consistency |
| `capex_negative` | Soft | $\text{Acquisition of PPE} \le 0$ | Direct method cash outflow sign |
| `cash_reconciliation` | Soft | $\text{Cash}_{\text{End}} = \text{Cash}_{\text{Start}} + \Delta \text{Cash} + \text{FX}$ | Ending cash balance rollforward |

---

## 9. Production Benchmark Matrix (All 14 Issuers)

| Issuer | Sector | Currency | Scale | DB Facts | Balance Sheet Identity | Hard Rules Pass |
|---|---|---|---|---|---|---|
| **BBCA** | Commercial Banking | IDR | 1,000,000 | 649 | $1,586,828,536\text{M} = 1,294,508,286\text{M} + 281,687,555\text{M} + 10,632,695\text{M}$ | **100% (Diff: 0)** |
| **NCKL** | Nickel Mining & Smelting | IDR | 1,000,000 | 304 | $61,770,103\text{M} = 15,014,238\text{M} + 46,755,865\text{M}$ | **100% (Diff: 0)** |
| **ADRO** | Coal Mining & Energy | USD | 1,000 | 264 | $6,816,993\text{K} = 1,813,040\text{K} + 5,003,953\text{K}$ | **100% (Diff: 0)** |
| **MAPI** | Retail & Specialty Stores | IDR | 1,000,000 | 235 | $32,824,207\text{M} = 15,612,724\text{M} + 17,211,483\text{M}$ | **100% (Diff: 0)** |
| **ABMM** | Mining Services | USD | 1 | 220 | $\$2,055,209,004 = \$1,174,787,608 + \$880,421,396$ | **100% (Diff: 0)** |
| **ATIC** | IT Systems Integration | IDR | 1 | 219 | $5,228,022,432,145 = 4,423,227,003,842 + 804,795,428,303$ | **100% (Diff: 0)** |
| **LSIP** | Palm Oil Plantations | IDR | 1,000,000 | 215 | $15,540,077\text{M} = 1,543,964\text{M} + 13,996,113\text{M}$ | **100% (Diff: 0)** |
| **BREN** | Geothermal & Wind | USD | 1,000 | 199 | $3,867,410\text{K} = 2,983,891\text{K} + 883,519\text{K}$ | **100% (Diff: 0)** |
| **ARCI** | Pure Gold Mining | USD | 1 | 188 | $\$1,026,763,377 = \$663,835,665 + \$362,927,712$ | **100% (Diff: 0)** |
| **CASS** | Aviation Ground & Catering | IDR | 1,000,000 | 184 | $3,230,133\text{M} = 978,840\text{M} + 2,251,293\text{M}$ | **100% (Diff: 0)** |
| **BBSI** | Commercial / Digital Banking| IDR | 1 | 182 | $12,214,396,484,644 = 8,752,048,122,301 + 3,462,348,362,343$ | **100% (Diff: 0)** |
| **DMAS** | Industrial Estates & Property | IDR | 1 | 171 | $7,121,947,515,022 = 508,539,806,236 + 6,613,407,708,786$ | **100% (Diff: 0)** |
| **BISI** | Agrochemicals & Seeds | IDR | 1,000,000 | 160 | $3,738,296\text{M} = 243,726\text{M} + 3,494,570\text{M}$ | **100% (Diff: 0)** |
| **IPCM** | Marine Port & Towage | IDR | 1,000 | 135 | $1,714,517,440\text{K} = 354,723,262\text{K} + 1,359,794,178\text{K}$ | **100% (Diff: 0)** |

Total Silver facts in DuckDB: **3,325 facts** across all 14 issuers.
Hard Rules Compliance: **100.0%**.
Zero-Difference Balance Sheet Identities: **14 out of 14 issuers**.

---

## 10. Automated Testing Architecture

The codebase includes **49 automated tests** across four test suites:

1. **`tests/test_operational_kpis.py` (13 tests)**:
   - Verifies real operational disclosures from official reports: NCKL ore/smelter output, LSIP nucleus planted hectares, CASS aviation segment profit, ATIC IT revenue, ABMM overburden removal & coal sales, ADRO financial overview, BBCA loans & CASA breakdown, BREN electricity & steam production, MAPI store count & regional network, DMAS industrial hectares & data center zone, ARCI gold volume & reserves, BISI agrochemical margins, verbatim string parser.
2. **`tests/test_all_issuers.py` (21 tests)**:
   - Full integration suite: sectionizing across all 14 companies, minimum 40 facts per company, multi-currency reporting (USD vs IDR), zero-diff balance sheet reconciliation, banking vs non-bank field availability, and page citations.
3. **`tests/test_validation_engine.py` (7 tests)**:
   - Unit tests for accounting reconciliation, scale multiplier tolerance, sign inversion handling, and automated error repair.
4. **`tests/test_end_to_end_nckl.py` (1 test)**:
   - Comprehensive multi-page end-to-end integration run for NCKL.
5. **Parser & Database Tests (7 tests)**:
   - Unit tests covering `test_number_parser.py`, metadata sniffing, and storage persistence.

Execution command:
```bash
python3 -m unittest discover tests -p "test_*.py"
# Result: Ran 49 tests in 93.038s — OK (100% pass)
```

---

## 11. Complete Database Architecture & Data Inspection Playbook

The pipeline persists all data into an embedded, high-performance **DuckDB** database located at:
`data/idxfin.duckdb`

The schema design adheres strictly to the **Medallion Architecture** (Ref $\rightarrow$ Bronze $\rightarrow$ Silver $\rightarrow$ Market $\rightarrow$ Gold) with portable ANSI SQL DDL compatible with DuckDB, Delta Lake, Snowflake, and BigQuery.

<p align="center">
  <img src="img/database_schema.png" alt="DuckDB Medallion Relational Warehouse Schema" width="100%" />
</p>

### 11.1 Layer Catalog & Table Schemas

#### 1. Reference Layer (`idxfin_ref`)
- **`idxfin_ref.concept`**:
  - Contains standard seeded financial taxonomy items (148 concepts).
  - Columns: `concept_id` (PK, e.g. `fs.bs.total_assets`), `domain`, `statement`, `period_type` (`instant` vs `duration`), `balance` (`dr` vs `cr`), `data_type` (`monetary`, `percent`, `shares`), `unit_dimension`, `label_en`, `label_id`.
- **`idxfin_ref.label_mapping`**:
  - Dynamically learned label-to-concept mappings persisted from few-shot resolution and manual curation.
  - Columns: `label_raw`, `concept_id`, `mapping_method`, `confidence`, `statement_type`, `updated_at`.

#### 2. Bronze Layer (`idxfin_bronze`)
- **`idxfin_bronze.document`**:
  - Master registry of all ingested filing PDFs.
  - Columns: `doc_id` (PK, SHA-256 hash of PDF), `issuer_id` (Ticker), `doc_type` (`fs_annual`, `fs_interim`, `annual_report`), `fiscal_year`, `fiscal_period` (`FY`, `Q1`, `H1`, `9M`), `period_end`, `currency` (`IDR`, `USD`), `scale` (e.g. `1`, `1000`, `1000000`), `number_locale` (`id`, `en`), `page_count`, `ingested_at`.
- **`idxfin_bronze.page`**:
  - Text layer and layout coordinates extracted per page for fast text retrieval without reopening PDFs.
  - Columns: `doc_id`, `page_no` (1-based), `text`, `width`, `height`.

#### 3. Silver Layer (`idxfin_silver`)
- **`idxfin_silver.fact`**:
  - The universal fact table containing all normalized numbers from face statements, notes disclosures, and operational KPIs.
  - Key Columns:
    - `fact_id` (PK, deterministic hash: `f-{issuer}-{concept}-{period_end}-{dim_key}`)
    - `issuer_id`, `doc_id`
    - `concept_id` (Canonical concept ID or `ext.<TICKER>.<slug>`)
    - `label_raw` (Verbatim row label in original bilingual language)
    - `value` (Exact `Decimal` scaled to nominal units, e.g. IDR full units or USD full units)
    - `raw_text` (Verbatim number string printed on page, e.g. `"32,824,207"`)
    - `currency`, `scale_as_reported`
    - `period_type` (`instant` for BS, `duration` for IS/CF)
    - `fiscal_year`, `fiscal_period`, `period_end`
    - `is_comparative` (Boolean: `True` if extracted from comparative prior-year column)
    - `lineage_page` (1-based PDF page citation)
    - `lineage_extractor`, `lineage_model`, `lineage_run_id`
    - `knowledge_ts`, `ingested_at`
- **`idxfin_silver.officer_position`**:
  - Board of Directors and Board of Commissioners corporate governance profiles.
  - Columns: `issuer_id`, `doc_id`, `person_name`, `role`, `is_independent`, `source_page`.
- **`idxfin_silver.audit_report`**:
  - Independent Auditor's Report metadata, KAP name, signing CPA, opinion, and Key Audit Matters.
  - Columns: `doc_id`, `issuer_id`, `fiscal_year`, `audit_firm`, `network`, `signing_partner`, `opinion`, `key_audit_matters`, `source_page`.
- **`idxfin_silver.validation_result`**:
  - Complete historical log of all accounting identities evaluated by `ValidationEngine`.
  - Columns: `run_id`, `doc_id`, `rule_id`, `passed`, `lhs`, `rhs`, `diff`, `tolerance`, `severity`, `message`, `checked_at`.
- **`idxfin_silver.review_task`**:
  - Workflow tasks generated whenever a hard validation check fails and repair loop fails to resolve.
  - Columns: `task_id`, `doc_id`, `target_ids`, `reason`, `priority`, `status`, `created_at`.

#### 4. Market Layer (`idxfin_market`)
- **`idxfin_market.stock_daily`**:
  - 4-Year daily OHLCV trading bars and volumes for IDX public issuers (`.JK`).
  - Columns: `ticker` (PK), `trade_date` (PK), `open`, `high`, `low`, `close`, `adj_close`, `volume`, `currency`, `source`, `ingested_at`.
- **`idxfin_market.commodity_daily`**:
  - Benchmark commodity quotes covering energy, precious metals, and physical fertilizer assessments: Physical Ammonia (`NH3=F`), Gold (`GC=F`), WTI Oil (`CL=F`), Brent Oil (`BZ=F`), Natural Gas (`NG=F`), and corporate proxies (`ESSA.JK`, `CF`, `UAN`).
  - Columns: `symbol` (PK), `trade_date` (PK), `commodity_name`, `commodity_category`, `open`, `high`, `low`, `close`, `volume`, `unit`, `currency`, `source`, `ingested_at`.
- **`idxfin_market.fx_daily`**:
  - 4-Year daily spot foreign exchange rates (e.g. `USD/IDR`, ticker `IDR=X`).
  - Columns: `pair` (PK), `trade_date` (PK), `symbol`, `open`, `high`, `low`, `close`, `rate`, `base_currency`, `quote_currency`, `source`, `ingested_at`.

#### 5. Gold Layer (`idxfin_gold`)
- **`idxfin_gold.fact_latest`**:
  - Bitemporal view selecting the single best known fact per `(issuer_id, concept_id, period_end, dim_key, scenario)` based on audit assurance and latest `knowledge_ts`.
- **`idxfin_gold.fact_first_reported`**:
  - Point-in-time view of data as first seen by the market prior to restatements.
- **`idxfin_gold.fact_discrete_quarter`**:
  - Dynamically calculates discrete quarterly figures for duration concepts (`Q2 = H1 - Q1`, `Q3 = 9M - H1`, `Q4 = FY - 9M`).
- **`idxfin_gold.financial_summary`**:
  - Analytical summary pivoting core financial metrics across all issuers: `total_assets`, `current_assets`, `total_liabilities`, `current_liabilities`, `total_equity`, `temporary_syirkah_funds`, `revenue`, `cost_of_revenue`, `gross_profit`, `net_profit`, `operating_cash_flow`, `capex`, `bank_loans`, `bank_deposits`.
- **`idxfin_gold.financial_summary_provenance`**:
  - Provenance audit view returning the source page citation (`p.<page_no>`) for each figure displayed in the summary.
- **`idxfin_gold.market_valuation`**:
  - Cross-asset join linking financial statement fundamentals with period-end closing stock prices, latest market prices, and price performance since period end.
- **`idxfin_gold.commodity_trend_monthly`**:
  - Monthly aggregated commodity prices, min/max spreads, trading volume, and monthly percentage return across energy, precious metals, and chemicals.
- **`idxfin_gold.operational_kpi`**:
  - Operational physical volume metrics (Ammonia tonnes, LPG tonnes, Gold oz, Coal tonnes) filtered from extracted facts.
- **`idxfin_gold.fx_rate_daily`**:
  - Daily foreign exchange closing rates with windowed monthly and annual averages for financial statement currency translation.

---

### 11.2 How to Inspect and Query the Database

You can inspect the database directly using Python, the DuckDB CLI, or DBeaver / TablePlus.

#### Option A: Quick Inspection via Python
```python
import duckdb
import pandas as pd

# Connect to the database in read-only mode
conn = duckdb.connect('data/idxfin.duckdb', read_only=True)

# 1. View all 14 issuers in Gold financial summary
df_summary = conn.execute("""
    SELECT issuer_id, currency, total_assets, total_liabilities, total_equity, revenue, net_profit, operating_cash_flow
    FROM idxfin_gold.financial_summary
    WHERE fiscal_year = 2025
    ORDER BY total_assets DESC
""").df()
print(df_summary)

# 2. Trace provenance citations for any issuer (e.g. MAPI)
df_prov = conn.execute("""
    SELECT * FROM idxfin_gold.financial_summary_provenance
    WHERE issuer_id = 'MAPI' AND fiscal_year = 2025
""").df()
print(df_prov)

conn.close()
```

#### Option B: Interactive DuckDB CLI
Launch the DuckDB CLI directly on the file:
```bash
duckdb data/idxfin.duckdb
```

Useful commands inside the CLI:
```sql
-- List all schemas
SHOW SCHEMAS;

-- List all tables and views in Silver and Gold
SHOW TABLES FROM idxfin_silver;
SHOW TABLES FROM idxfin_gold;

-- Set output format for clean reading
.mode box
.nullvalue 'NULL'

-- Inspect table columns
DESCRIBE idxfin_silver.fact;
```

---

### 11.3 Essential SQL Inspection Recipes

#### Recipe 1: Balance Sheet Reconciliation Audit
Verifies that Total Assets equals Liabilities + Equity (+ Sharia Temporary Syirkah Funds) for all companies:
```sql
SELECT 
    issuer_id,
    currency,
    total_assets,
    total_liabilities,
    total_equity,
    COALESCE(temporary_syirkah_funds, 0) AS syirkah_funds,
    total_assets - (total_liabilities + total_equity + COALESCE(temporary_syirkah_funds, 0)) AS bs_diff
FROM idxfin_gold.financial_summary
WHERE fiscal_year = 2025
ORDER BY bs_diff DESC;
```

#### Recipe 2: Verbatim Grounding & Raw Cell Audit
Inspects how numbers printed on the PDF match normalized facts and cited page numbers:
```sql
SELECT 
    issuer_id,
    concept_id,
    label_raw,
    raw_text AS verbatim_page_string,
    value AS scaled_nominal_value,
    scale_as_reported,
    lineage_page AS source_page
FROM idxfin_silver.fact
WHERE issuer_id = 'MAPI' 
  AND concept_id IN ('fs.bs.total_assets', 'fs.bs.total_equity', 'fs.is.revenue', 'fs.is.gross_profit')
  AND fiscal_year = 2025
ORDER BY lineage_page;
```

#### Recipe 3: Multi-Year Comparative Time Series (2025 vs 2024)
Inspects comparative reporting consistency across periods:
```sql
SELECT 
    f.fiscal_year,
    f.concept_id,
    f.label_raw,
    f.raw_text,
    f.value,
    f.is_comparative,
    f.lineage_page
FROM idxfin_silver.fact f
WHERE f.issuer_id = 'ARCI' 
  AND f.concept_id IN ('fs.bs.total_assets', 'fs.is.revenue', 'fs.is.profit_for_the_period')
ORDER BY f.concept_id, f.fiscal_year DESC;
```

#### Recipe 4: Corporate Governance & Board Profiles
Inspects directors and commissioners extracted from Annual Reports:
```sql
SELECT 
    issuer_id,
    role,
    person_name,
    is_independent,
    source_page
FROM idxfin_silver.officer_position
ORDER BY issuer_id, role;
```

#### Recipe 5: Accounting Validation Audit Log
Inspects the rule evaluation trail and checks for any failing soft or hard rules:
```sql
SELECT 
    v.rule_id,
    v.passed,
    v.severity,
    v.lhs,
    v.rhs,
    v.diff,
    v.tolerance,
    v.message
FROM idxfin_silver.validation_result v
WHERE v.passed = false;
```
*(Returns 0 rows for hard rules, proving 100% compliance across all companies).*

---

## 12. Market Data, Macroeconomic Benchmarks & Cross-Asset Valuation Architecture

### 12.1 Architectural Purpose: Bridging Accounting Fundamentals & Market Realities

While the core extraction engine processes audited accounting facts from financial statements (LK) and annual reports (AR), comprehensive equity research and quantitative risk analysis require synthesizing reported financial performance with **external market pricing and macroeconomic drivers**:
1. **Valuation Multiples**: Price-to-Earnings (P/E), Price-to-Book (P/B), Enterprise Value (EV/EBITDA), and Market Capitalization cannot be derived from financial statements alone without historical stock prices.
2. **Post-Period Market Dynamics**: Quantifying how an issuer's share price reacted following quarterly earnings releases or audited annual results.
3. **Macroeconomic Sensitivity & Input Costs**: Indonesian corporate earnings are heavily influenced by global commodity cycles (e.g. Gold miners `ARCI`, `AMMN`; Energy producers `ADRO`, `BREN`; Agrochemical & Petrochemical producers `ESSA`, `BISI`).

To fulfill these requirements without compromising the integrity of the accounting extraction pipeline, a decoupled market data ingestion layer was integrated into DuckDB via `yfinance`.

<p align="center">
  <img src="img/market_valuation_architecture.png" alt="Market Data Ingestion, Macroeconomic Benchmarks & Cross-Asset Valuation Architecture" width="100%" />
</p>

---

### 12.2 Medallion Schema Isolation: `idxfin_market`

In accordance with Medallion architecture principles, external market data is isolated in a dedicated schema (`idxfin_market`) distinct from corporate filing artifacts:
- **`idxfin_silver`** retains strictly internal corporate disclosures extracted from issuer documents with cryptographic SHA-256 provenance and cited PDF page coordinates.
- **`idxfin_market`** stores external time-series data from public trading exchanges:
  1. **`idxfin_market.stock_daily`**:
     - Composite Primary Key: `(ticker, trade_date)`.
     - Fields: `open`, `high`, `low`, `close`, `adj_close`, `volume`, `currency` (IDR), `source` (`'yfinance'`), `ingested_at`.
  2. **`idxfin_market.commodity_daily`**:
     - Composite Primary Key: `(symbol, trade_date)`.
     - Fields: `commodity_name`, `commodity_category`, `open`, `high`, `low`, `close`, `volume`, `unit`, `currency`, `source`, `ingested_at`.

---

### 10.3 Commodity Catalog & Solving the Ammonia OTC Constraint

Physical anhydrous ammonia ($NH_3$) does not trade via an active, standardized CME or NYMEX futures contract with public daily pricing on Yahoo Finance; instead, industrial ammonia trades through private, over-the-counter (OTC) bilateral spot and term contracts (CFR Tampa, FOB Middle East, CFR NW Europe).

To solve this constraint and provide rigorous, liquid market proxies for ammonia market dynamics, the system implements a **triangulated proxy architecture**:

| Commodity / Proxy | Symbol | Category | Unit | Economic Rationale & Mechanism |
| :--- | :--- | :--- | :--- | :--- |
| **Gold** | `GC=F` | `PRECIOUS_METALS` | USD/troy oz | Benchmark COMEX Gold Futures; primary revenue driver for gold producers (`ARCI`, `AMMN`). |
| **Crude Oil (WTI)** | `CL=F` | `ENERGY` | USD/bbl | Light sweet crude oil benchmark traded on NYMEX; global energy demand proxy. |
| **Crude Oil (Brent)** | `BZ=F` | `ENERGY` | USD/bbl | North Sea Brent crude futures on ICE; international pricing marker for Indonesian crude (ICP). |
| **Ammonia Domestic** | `ESSA.JK` | `CHEMICALS_FERTILIZERS` | IDR/share | **PT ESSA Industries Indonesia Tbk**: Indonesia's leading listed merchant ammonia and LPG producer. Equity valuation directly tracks domestic and regional East Asian ammonia realizations. |
| **Ammonia Global** | `CF` | `CHEMICALS_FERTILIZERS` | USD/share | **CF Industries Holdings Inc.**: World's largest pure-play ammonia manufacturer. Direct proxy for global synthetic nitrogen margins. |
| **Ammonia Feedstock** | `NG=F` | `ENERGY` | USD/MMBtu | **Henry Hub Natural Gas**: Primary hydrogen feedstock for Haber-Bosch ammonia synthesis. Natural gas accounts for **70% to 85% of ammonia cash production costs**. |

---

### 10.4 Gold Layer Cross-Asset Valuation Views

The Gold layer synthesizes fundamental statement metrics with market pricing using windowed temporal joins:

#### 1. `idxfin_gold.market_valuation`
Joins `financial_summary` with `stock_daily` to find:
- The closing stock price on or closest to the filing period end date (`period_close_price`).
- The most recent market closing price (`latest_close_price`).
- The cumulative price performance since the end of the reporting period (`price_change_since_period_pct`).

```sql
WITH price_at_period AS (
    SELECT
        s.ticker,
        f.period_end,
        s.close AS period_close_price,
        s.trade_date AS period_price_date,
        ROW_NUMBER() OVER (
            PARTITION BY s.ticker, f.period_end
            ORDER BY ABS(DATE_DIFF('day', s.trade_date, f.period_end)) ASC, s.trade_date ASC
        ) AS rn
    FROM (SELECT DISTINCT issuer_id, period_end FROM idxfin_gold.financial_summary) f
    JOIN idxfin_market.stock_daily s
      ON s.ticker = f.issuer_id
     AND s.trade_date BETWEEN f.period_end - INTERVAL 5 DAY AND f.period_end + INTERVAL 5 DAY
),
latest_price AS (
    SELECT
        ticker,
        close AS latest_close_price,
        trade_date AS latest_trade_date,
        ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY trade_date DESC) AS rn
    FROM idxfin_market.stock_daily
)
SELECT
    fs.issuer_id,
    fs.period_end,
    fs.fiscal_year,
    fs.fiscal_period,
    fs.currency,
    fs.revenue,
    fs.gross_profit,
    fs.net_profit,
    fs.total_assets,
    fs.total_equity,
    pap.period_close_price,
    lp.latest_close_price,
    ROUND(((lp.latest_close_price - pap.period_close_price) / pap.period_close_price) * 100, 2) AS price_change_since_period_pct
FROM idxfin_gold.financial_summary fs
LEFT JOIN price_at_period pap ON pap.ticker = fs.issuer_id AND pap.period_end = fs.period_end AND pap.rn = 1
LEFT JOIN latest_price lp ON lp.ticker = fs.issuer_id AND lp.rn = 1;
```

#### 2. `idxfin_gold.commodity_trend_monthly`
Aggregates daily commodity prices into discrete monthly windows computing average, min, and max closes, trading volume, and monthly percentage return (`monthly_return_pct`).

---

### 12.5 Ingestion Resiliency & Lock Protection

1. **Deterministic Suffix Normalization**:
   All Indonesian stock tickers are automatically normalized to `{TICKER}.JK` before dispatching requests to yfinance, while global commodity symbols (`GC=F`, `CL=F`, `BZ=F`, `CF`, `NG=F`) are preserved.
2. **Idempotent Upsert Semantics**:
   DataFrames are registered as temporary virtual tables in DuckDB and committed using `INSERT OR REPLACE INTO idxfin_market.stock_daily`, ensuring seamless backfilling and zero duplicate records upon recurring cron executions.
3. **FileLock Mutex Concurrency**:
   Market data ingestion shares the same POSIX `FileLock` (`data/idxfin.duckdb.lock`) as the Celery asynchronous extraction workers, eliminating database locking contention and write collisions.

---

## 13. Distributed Asynchronous Extraction & Concurrent Ingestion Architecture

For high-throughput enterprise batch processing, the pipeline features an asynchronous queue-driven architecture powered by **Redis**, a **6-worker Celery extraction pool**, an isolated **Parquet Staging Lake**, and a **single-writer DuckDB serialized ingestion engine**:

<p align="center">
  <img src="img/distributed_architecture.png" alt="Distributed Asynchronous Extraction & Concurrent Ingestion Architecture" width="100%" />
</p>

### 13.1 Solving DuckDB File Concurrency (Two-Phase Commit)
DuckDB is an in-process, serverless OLAP engine that does not support concurrent write transactions across multiple processes. If 6 Celery worker processes attempt to open `data/idxfin.duckdb` in read-write mode, DuckDB throws `_duckdb.IOException: Could not set lock on file ...`.

**The Solution:**
1. **Stateless Extraction (Phase 1)**: Celery extraction workers process PDFs and invoke LLMs in complete process isolation, writing Bronze & Silver entities to strongly typed Parquet tables in `data/staging/pending/{run_id}/`.
2. **Serialized Single-Writer Ingestion (Phase 2)**: Staged runs are ingested by a dedicated Celery queue (`duckdb_writer`) with **strictly concurrency = 1**, further protected by a file mutex (`FileLock("data/idxfin.duckdb.lock")`).
3. **Vectorized Loading**: DuckDB reads Parquet files using native `read_parquet(...)` in **< 50 milliseconds**, updates Gold views, and archives the staging run.
4. **Zero-Lock Multi-Reader Access**: Analytical queries via Wren Engine, Ibis Server, and LangGraph agents connect concurrently with `PRAGMA access_mode='read_only';` without write lock collisions.
5. **Disaster Recovery**: Staged Parquet files provide an immutable landing zone. If the database file is ever corrupted, all extractions can be restored in seconds with `python3 -m src.cli reindex-staging` without calling LLMs again.

---

## 14. Conversational GenBI & Agentic Analytics Architecture (Wren AI)

### 14.1 The Architectural Paradigm Shift: Governed Semantic Layer vs Raw Text-to-SQL
While the Medallion DuckDB warehouse (`data/idxfin.duckdb`) provides structured relational views, traditional direct Text-to-SQL approaches fail on financial statements due to:
1. **Schema Hallucinations**: Models inventing non-existent columns or misunderstanding domain abbreviations.
2. **PSAK Accounting Complexities**: Omitting *Dana Syirkah Temporer* (PSAK 101) in bank balance sheets, causing false "unbalanced" flags.
3. **Multi-Currency Reporting**: Blindly aggregating USD miners (`ADRO`, `ARCI`, `BREN`, `ESSA`) with IDR companies (`BBCA`, `NCKL`, `MAPI`).
4. **Audit Provenance Gap**: Producing numbers without lineage citations back to source PDF pages.

To resolve these challenges, **Wren AI** is integrated as an open-source **Generative Business Intelligence (GenBI)** engine that acts as a governed intermediary between end-user questions and DuckDB.

### 14.2 Microservice Topology & Component Interaction
Wren AI operates as an isolated containerized stack orchestrated via `wren/docker-compose.wren.yml`:
- **Wren UI** (Port `3000`): React/Next.js interface for financial analysts, supporting conversational threads, visual semantic modeling, and saved dashboards.
- **Wren Engine** (Port `8080`): High-performance Rust engine powered by **Apache DataFusion**, compiling Modeling Definition Language (MDL) contracts into optimized DuckDB dialect SQL.
- **Wren AI Service** (Port `5555`): FastAPI orchestration layer powered by **LiteLLM**, routing prompts to OpenAI (**`gpt-5.6-luna`** for primary reasoning and **`gpt-5.4-mini`** for fallback), and leveraging **Qdrant** for vector retrieval.
- **Qdrant Vector Database** (Port `6333`): Stores dense embeddings of table descriptions, column definitions, and verified few-shot SQL pairs using OpenAI `text-embedding-3-small`.
- **Ibis Server** (Port `8000`): Analytical query execution server supporting multi-engine dataframe transformations.

### 14.3 Solving DuckDB File Concurrency in Multi-Service Deployments
Because DuckDB is an in-process, serverless OLAP engine, write transactions lock the file exclusively. To guarantee that analytical user queries never collide with Celery ingestion workers holding `FileLock("data/idxfin.duckdb.lock")`:
1. **Read-Only Volume Mounts**: The DuckDB database file is mounted into Wren Engine and Ibis Server with `:ro` mode.
2. **Read-Only Session Pragma**: Connections execute `PRAGMA access_mode='read_only';`, allowing concurrent multi-reader access without lock acquisition conflicts.
3. **Optional Serving Replicas**: For continuous high-throughput ingestion, Celery's single-writer worker can checkpoint snapshots to `data/serving/idxfin_serving.duckdb` without locking the live extraction database.

### 14.4 Semantic Contracts in Modeling Definition Language (MDL)
The semantic layer is codified in YAML under `wren/projects/idxfin/`:
- **`financial_summary.yaml`**: Standardized face statements across 14 benchmark issuers + ESSA, defining calculated measures (ROE, ROA, Net Margin, Debt-to-Equity, LDR).
- **`market_valuation.yaml`**: Fundamental-to-market cross-asset joins computing period closing price, latest price, and percentage return since period end.
- **`commodity_trend_monthly.yaml`**: Multi-asset commodity tracking covering Gold (`GC=F`), WTI (`CL=F`), Brent (`BZ=F`), and Ammonia proxies (`ESSA.JK`, `CF`, `NG=F`).
- **`officer_position.yaml`**: Corporate governance profiles (Directors and Commissioners).
- **`audit_report.yaml`**: KAP names, signing CPA partners, opinions, and Key Audit Matters.
- **`financial_summary_provenance.yaml`**: Direct page citation linkage (`p.<page_no>`) for auditability.
- **`relationships.yml`**: Explicit join graph connecting fundamental, market, governance, and audit models.

### 14.5 Domain Knowledge & Guardrails
Wren AI's cognitive service is grounded by explicit domain rules in `knowledge/accounting_rules.yaml`:
- **Currency Isolation**: Strictly segregates IDR and USD financial metrics.
- **PSAK 101 Sharia Balance Sheet**: Enforces $\text{Assets} = \text{Liabilities} + \text{Equity} + \text{Temporary Syirkah Funds}$.
- **Unclassified Bank Balance Sheets**: Guides LLM to evaluate bank liquidity through customer deposits and loan-to-deposit ratios rather than current asset splits.
- **Provenance Citations**: Commands the conversational agent to join `financial_summary_provenance` and cite the exact PDF page for every financial figure presented.

### 14.6 Dual Execution Topology: Containerized Microservices & Native Python GenBI Agent
To maximize developer agility and deployment flexibility, the platform implements a dual execution topology:

#### 1. Native Python GenBI Chat Agent (`wren/chat_agent.py`)
Powered by `wren-engine 0.6.0`, `wren-core-py 0.8.0` (Rust DataFusion), and `wren-langchain 0.2.2`:
- **Direct Lakehouse Attachment**: Runs in-process with native Apple Silicon (ARM64) binaries. Attaches `data/idxfin.duckdb` directly with zero container translation overhead.
- **Dynamic Semantic Metric Calculation**: Automatically computes calculated fields (e.g., `return_on_equity_pct`, `net_profit_margin_pct`) on the fly via DataFusion AST rewrites.
- **OpenAI Function Calling Guardrails**: Configured with `reasoning_effort="none"` to enable seamless tool-calling with `gpt-5.6-luna` and `gpt-5.4-mini` over `wren_query`, `wren_dry_plan`, and `wren_list_models`.
- **Interactive CLI & One-Shot Mode**: Analysts can query interactively (`python wren/chat_agent.py`) or in batch mode (`python wren/chat_agent.py -q "..."`).

#### 2. Microservice Docker Stack (`wren/docker-compose.wren.yml`)
For web-based visual exploration and analyst dashboarding:
- **Qdrant Healthcheck Resolution**: Switched container health test to `/dev/tcp/127.0.0.1/6333` socket probing to avoid `curl: not found` exit code 127 in minimal Alpine/Debian base images.
- **Rosetta 2 Emulation**: On Apple Silicon macOS, ensures Docker Desktop virtualization utilizes Rosetta emulation for amd64 containers.

---

## 15. Phase 8 Architecture: Foreign Exchange Rate (FX) Ingestion & Cross-Currency Semantic Grounding

### 15.1 Business Context & Problem Statement
In the Indonesian capital market (IDX), several major corporate issuers report their audited financial statements and note disclosures in **US Dollars (USD)** because their functional currency is dollar-denominated (e.g., energy, mining, and petrochemical producers such as **ESSA**, **ADRO**, **BREN**, **ARCI**, and **ABMM**). However:
1. Their equity shares trade domestically on the IDX exclusively in **Indonesian Rupiah (IDR)**.
2. Macroeconomic benchmark comparisons and domestic operational unit realizations (e.g. domestic selling price of ammonia or LPG per metric ton, local labor costs, domestic supply contracts) require accurate, period-matched foreign exchange translations.
3. Without a standardized FX series in the lakehouse, autonomous text-to-SQL agents either refuse to perform cross-currency calculations or risk hallucinating nominal exchange rates.

### 15.2 Data Source & Ingestion Pipeline
- **Benchmark Source**: Yahoo Finance ticker `IDR=X` (USD/IDR exchange rate, representing Indonesian Rupiah per 1 US Dollar).
- **Time Horizon**: 4 years of daily trading bars (2022-09-19 to 2026-09-17, 1,039 trading days).
- **Ingestion Engine**: Extended `MarketDataFetcher.fetch_fx()` and `MarketDataLoader.sync_fx()` in `src/market_data/yfinance_loader.py` to persist daily bars into DuckDB via vectorized `INSERT OR REPLACE`.
- **CLI Management**: Added `--fx` flag to `src.cli market` and `scripts/sync_market_data.py`.

### 15.3 Medallion Lakehouse Schema Design

#### 1. Market Base Table (`idxfin_market.fx_daily`)
```sql
CREATE TABLE IF NOT EXISTS idxfin_market.fx_daily (
    pair VARCHAR,                          -- e.g. 'USD/IDR'
    symbol VARCHAR,                        -- 'IDR=X'
    trade_date DATE,                       -- Trading date (YYYY-MM-DD)
    open DOUBLE,                           -- Open exchange rate
    high DOUBLE,                           -- Daily high
    low DOUBLE,                            -- Daily low
    close DOUBLE,                          -- Daily closing rate
    rate DOUBLE,                           -- Benchmark rate (= close)
    base_currency VARCHAR DEFAULT 'USD',
    quote_currency VARCHAR DEFAULT 'IDR',
    source VARCHAR DEFAULT 'yfinance',
    ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (pair, trade_date)
);
```

#### 2. Gold Analytical View (`idxfin_gold.fx_rate_daily`)
Computes windowed averages to provide both point-in-time and period-aggregate conversions:
```sql
CREATE OR REPLACE VIEW idxfin_gold.fx_rate_daily AS
SELECT
    pair,
    symbol,
    trade_date,
    open,
    high,
    low,
    close AS rate_close,
    rate,
    base_currency,
    quote_currency,
    CAST(DATE_TRUNC('month', trade_date) AS DATE) AS month_date,
    YEAR(trade_date) AS fiscal_year,
    ROUND(AVG(close) OVER (PARTITION BY pair, DATE_TRUNC('month', trade_date)), 2) AS monthly_avg_rate,
    ROUND(AVG(close) OVER (PARTITION BY pair, YEAR(trade_date)), 2) AS yearly_avg_rate
FROM idxfin_market.fx_daily
ORDER BY pair, trade_date DESC;
```

### 15.4 Semantic Layer (Wren AI Modeling Definition Language)
- **MDL Model**: Created `wren/projects/idxfin/models/fx_rate.yml` and `fx_rate.yaml`, referencing `idxfin_gold.fx_rate_daily`.
- **Primary Key**: `[pair, trade_date]`.
- **Manifest Registration**: Registered in `wren/projects/idxfin/target/mdl.json` to enable zero-rebuild execution via `WrenToolkit.from_project()`.

### 15.5 Agent Grounding & Verification
- **PSAK Grounding Rule 3**: Instructs the autonomous LLM agent (`wren/chat_agent.py`) to leverage `fx_rate` for cross-currency calculations:
  - For point-in-time balance sheets: use `rate_close` at period-end (e.g., Dec 31, 2024: IDR 16,085.60; Dec 31, 2025: IDR 16,709.00).
  - For P&L period flows or full-year unit price estimation: use `yearly_avg_rate` (e.g. 2025 average: IDR 16,448.09).
- **Test Suite**: Extended `tests/test_wren_semantic_contract.py` with 2 new unit tests (`test_fx_rate_daily_coverage`, `test_wren_toolkit_fx_rate_query`), reaching 11/11 passing tests in 1.59 seconds.

---

## 16. Phase 9 Architecture: Physical Anhydrous Ammonia Benchmark (`NH3=F`) Ingestion & Margin Modeling

### 16.1 Business Context & Problem Statement
While macroeconomic energy benchmarks (WTI, Brent, Natural Gas) and equity proxies (`ESSA.JK`, `CF`, `UAN`) provide indirect market signals, energy and fertilizer analysts require **actual physical spot commodity assessments** to:
1. Benchmark domestic ammonia selling prices realized by Indonesian producers (such as PT ESSA Industries Indonesia Tbk) against international FOB/CFR spot assessments.
2. Evaluate Haber-Bosch production economics: Feedgas crack spread ($\text{Ammonia Spot Price} - \text{Gas Feedstock Cost} \times \text{Conversion Efficiency}$).
3. Track structural fertilizer price shocks across the 2022 global supply crunch ($1,005/ton peak) through the 2023 normalization ($387.5/ton trough) and 2025–2026 recovery ($730–800/ton).

### 16.2 Dataset & Ingestion Pipeline
- **Dataset Source**: Standardized weekly physical spot assessment series (261 observations from 2021-09-20 to 2026-09-14 in `USD/ton`).
- **Storage Location**: Persisted as a version-controlled repository asset in `data/benchmarks/ammonia_daily_benchmark.csv`.
- **Ingestion & Calendar Resampling**:
  - `MarketDataFetcher.load_ammonia_benchmark()` parses raw weekly assessment dates, reindexes across full calendar days (`freq='D'`), and forward-fills (`ffill()`) daily closing, high, low, and open prices.
  - Generates **1,821 daily bars** persisted into `idxfin_market.commodity_daily` under symbol `NH3=F`.
  - Guarantees seamless daily joins with `fx_daily` and corporate financial statement period-end dates (`period_end`).

### 16.3 Medallion Layer Integration
- **Market Layer**: `idxfin_market.commodity_daily` records `symbol = 'NH3=F'`, `commodity_name = 'Anhydrous Ammonia Benchmark Spot (Global CFR/FOB)'`, `unit = 'USD/ton'`, `currency = 'USD'`.
- **Gold Analytical Layer**: `idxfin_gold.commodity_trend_monthly` automatically aggregates `NH3=F` into calendar months:
  - 2024 Average: **USD 562.84 / ton** (IDR 8,918,933 / ton equivalent).
  - 2025 Average: **USD 730.20 / ton** (IDR 12,010,463 / ton equivalent, +29.79% YoY).
  - 2026 Spot (September): **USD 800.00 / ton**.

### 16.4 Semantic Layer & Agent Grounding
- **MDL Contract**: Updated `wren/projects/idxfin/models/commodity_trend_monthly.yml` and `.yaml`, adding `NH3=F` to the model contract and updating `target/mdl.json`.
- **Conversational Grounding**: Enhanced Rule 6 in `wren/chat_agent.py` so the GenBI chatbot recognizes `NH3=F` when asked for ammonia market or spot prices, allowing multi-table joins across `commodity_trend_monthly`, `fx_rate`, and `operational_kpi`.
- **Test Verification**: Updated `test_commodity_trend_monthly_coverage` in `tests/test_wren_semantic_contract.py` to assert presence, row counts (>50 months), and price bounds for `NH3=F`.




