# LLM-First Architecture Implementation Plan: Robust, Industry-Agnostic Extraction & Validation

> **Status:** Pending User Review & Approval  
> **Target Branch / Workspace:** `/Users/Rayhan.Kharisma/Documents/prsnl-prjct/statements-extraction`  
> **Execution Constraint:** **DO NOT AUTO-PROCEED.** Await explicit user sign-off before modifying engine source code.

---

## 1. Executive Summary & Philosophy: The LLM-First Shift

The current extraction pipeline achieves 100% test pass rates across 10 benchmark companies (NCKL, IPCM, BBSI, ATIC, CASS, LSIP, ABMM, ADRO, BBCA, BREN) with 2,571 facts in DuckDB. However, expanding to hundreds of issuers across diverse sectors reveals fundamental bottlenecks in rule-based systems:

### The Limitations of Rules & Regex in Financial Engineering
1. **Regex breaks on phrasing & scale**: Sniffing `"dalam jutaan"` or `"in thousands"` via regex fails when an issuer writes *"Angka disajikan dalam triliun Rupiah"* or *"Expressed in full units unless otherwise noted"*. Falling back to `scale = 1` distorts all extracted figures by $1,000,000\times$.
2. **Static dictionaries cannot scale across 900+ issuers**: Every industry (Insurance, Banking, Healthcare, Tech, Shipping, Mining) introduces hundreds of unique line items. Manually expanding an in-memory dictionary is an unmaintainable game of whack-a-mole.
3. **Hardcoded procedural equations break on real-world accounting**: Procedural rules like $\text{Assets} = \text{Liab} + \text{Equity}$ fail on:
   - **Sharia Banking (PSAK 101)**: Balance sheet includes *Dana Syirkah Temporer* (Temporary Syirkah Funds) situated between liabilities and equity.
   - **Discontinued Operations (PSAK 58)**: Net Profit includes net profit/loss from discontinued operations after tax.
   - **Cash Flow Foreign Exchange (PSAK 2)**: Cash change includes exchange rate fluctuation effects ($\Delta \text{Cash} = \text{CFO} + \text{CFI} + \text{CFF} + \text{FX Impact}$).
   - **Unclassified Balance Sheets**: Banks and insurance companies report in *order of liquidity* without separating current vs. non-current assets.

### The LLM-First Paradigm
In an **LLM-First Architecture**, the LLM performs all semantic, structural, and domain reasoning, while Python code is strictly reserved for deterministic, verifiable guarantees:

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                    DIVISION OF RESPONSIBILITIES                              │
├──────────────────────────────────────┬───────────────────────────────────────┤
│          LLM RESPONSIBILITIES        │          PYTHON RESPONSIBILITIES      │
├──────────────────────────────────────┼───────────────────────────────────────┤
│ • Semantic concept mapping           │ • Pydantic v2 strict type validation  │
│ • Filing metadata & regime detection │ • Verbatim page grounding (no halluc) │
│ • TOC-guided chapter segmentation    │ • Exact arithmetic additions/sums     │
│ • Accounting anomaly reconciliation  │ • Fast O(1) in-memory & DB caching    │
│ • Cross-lingual line item alignment  │ • DuckDB ACID storage transactions   │
└──────────────────────────────────────┴───────────────────────────────────────┘
```

---

## 2. Codebase Navigation & Architectural Context

To navigate the codebase when reviewing and tracing pipeline operations:

```
statements-extraction/
├── schema_design/                      # Core Schemas, Taxonomy, & Database Contracts
│   ├── extraction_contracts.py         # Pydantic v2 models emitted by extractors (StatementExtraction, etc.)
│   ├── taxonomy_seed.yaml              # 148 standard concepts, 33 validation rules, 28 KPI packs, 52 axes
│   └── storage_ddl.sql                 # DuckDB Medallion Architecture (ref, bronze, silver, gold)
│
├── src/                                # Engine Implementation
│   ├── parser/
│   │   ├── pdf_loader.py               # [TARGET] PDF text layer, layout blocks, metadata detection
│   │   └── sectionizer.py              # [TARGET] Section segmentation (XBRL codes & AR chapters)
│   │
│   ├── extraction/
│   │   ├── statements.py               # Primary financial statement extraction (BS, IS, CF, Equity)
│   │   ├── kpis.py                     # Operational KPIs & segment disclosures
│   │   ├── governance.py               # Board of Directors & Commissioners profiles
│   │   └── router.py                   # Section routing logic
│   │
│   ├── llm/
│   │   ├── client.py                   # OpenRouter API wrapper, max_tokens=32,768, retry loop
│   │   └── prompts.py                  # System financial analyst prompts, translation guidelines
│   │
│   ├── normalization/
│   │   ├── number_parser.py            # Number parsing (id/en), parentheses as negatives, scale math
│   │   └── normalizer.py               # Transforms raw LLM output into SilverFact records
│   │
│   ├── mapping/
│   │   ├── concept_mapper.py           # [TARGET] Taxonomy mapping, standard dictionary, extension fallback
│   │   └── period_resolver.py          # Fiscal year and period resolution
│   │
│   ├── validation/
│   │   ├── engine.py                   # [TARGET] Validation engine evaluating accounting identities
│   │   └── repair.py                   # Automated fact repairs (sign inversion, duplicate handling)
│   │
│   └── storage/
│       ├── database.py                 # DuckDB connection manager & table initializers
│       └── gold_views.py               # [TARGET] Analytical Gold views (fact_latest, financial_summary)
│
├── data/idxfin.duckdb                  # Embedded DuckDB database (Medallion layers)
├── tests/                              # Automated test suites (34 tests across 10 companies)
│   ├── test_abmm_extraction.py
│   ├── test_adro_extraction.py
│   ├── test_bbca_extraction.py
│   ├── test_bren_extraction.py
│   └── ... (additional issuer & unit test suites)
└── docs/
    ├── DESIGN_LOG.md                   # System design log & multi-industry benchmarks
    └── LLM_FIRST_IMPLEMENTATION_PLAN.md# [THIS FILE] Detailed transition roadmap
```

### End-to-End Dataflow Pipeline
```
  ┌──────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
  │   PDF File   │ ──► │  pdf_loader  │ ──► │ sectionizer  │ ──► │  statements  │ ──► │  normalizer  │
  └──────────────┘     └──────────────┘     └──────────────┘     └──────────────┘     └──────────────┘
                              │                    │                    │                    │
                      [LLM Metadata]         [LLM TOC Parser]     [LLM Extraction]      [LLM Mapper]
                              ▼                    ▼                    ▼                    ▼
                       FilingMetadata       Chapter Spans        Raw Extracted JSON     Silver Facts
                                                                                             │
                                                                                             ▼
                                                                                      ┌──────────────┐
                                                                                      │  validation  │
                                                                                      └──────────────┘
                                                                                             │
                                                                                    [LLM Reconciler]
                                                                                             ▼
                                                                                      Validated Facts
                                                                                             │
                                                                                             ▼
                                                                                      ┌──────────────┐
                                                                                      │  gold_views  │
                                                                                      └──────────────┘
```

---

## 3. Deep Dive: The 5 LLM-First Pillars

### Pillar 1: LLM-First & Hierarchical Tree Validation (`src/validation/engine.py`)

#### Problem in Current Code
The current `engine.py` runs rigid procedural equations:
```python
# Breaks on banks (no current assets), Sharia banks (syirkah funds), discontinued ops, FX
lhs = ta_fact.value
rhs = tl_fact.value + te_fact.value
diff = abs(lhs - rhs)
```
When an equation fails due to an accounting regime difference, the system flags a hard failure or requires manual code overrides.

#### Proposed Solution: Two-Layer Validation Engine

#### 1. Hierarchical Tree Validation ($\text{Parent} = \sum \text{Children}$)
Financial statements are inherently structured as mathematical trees. Instead of checking whether labels match specific names:
- The validator groups reported line items by their `parent_line_item_id` or indentation level.
- Computes deterministic roll-up verification:
  $$\text{Parent Subtotal} = \sum_{i} \text{Child}_i \pm \text{Rounding Tolerance}$$
- **Universal Guarantee**: This works identically across mining, manufacturing, banking, and sharia entities without needing per-industry rules.

#### 2. Format & Regime-Aware Dispatch
Before running standard cross-concept rules, the engine inspects the filing's `presentation_format` and `accounting_regime`:
- `order_of_liquidity` (e.g. Banks, Insurance): Automatically skip `bs_current_split` ($\text{Current Assets} + \text{Non-Current Assets} = \text{Total Assets}$).
- `sharia_banking`: Automatically use the sharia balance sheet equation:
  $$\text{Total Assets} = \text{Total Liabilities} + \text{Temporary Syirkah Funds} + \text{Total Equity}$$

#### 3. LLM Anomaly Reconciler Loop
When a core identity has a non-zero variance (e.g., $\Delta > \text{tolerance}$):
1. **Trigger**: Do not fail immediately.
2. **Context Dispatch**: Extract the implicated line items and the relevant PDF statement pages.
3. **LLM Query**:
   ```
   The Balance Sheet Identity has a difference:
     Total Assets: 1,586,828,536,000,000
     Total Liabilities: 1,294,508,286,000,000
     Total Equity: 281,687,555,000,000
     Difference: 10,632,695,000,000

   Review the reported line items on pages 4-10 of the statement text.
   Identify the specific reported line item that reconciles this difference,
   and explain the underlying accounting principle (e.g. PSAK 101, PSAK 58, PSAK 2).
   ```
4. **Structured Response**:
   ```json
   {
     "reconciling_concept": "fs.bs.sharia.temporary_syirkah_funds",
     "reconciling_label": "Jumlah dana syirkah temporer",
     "reconciling_value": 10632695000000,
     "accounting_regime": "sharia_banking",
     "standard_reference": "PSAK 101",
     "explanation": "Temporary Syirkah Funds are presented as a separate category between liabilities and equity.",
     "reconciliation_valid": true
   }
   ```
5. **Deterministic Verification**: Python verifies that adding `reconciling_value` yields an exact diff of $0.00$. If verified, the rule passes with an audit trail note recorded in the validation result.

---

### Pillar 2: Hybrid Few-Shot Concept Mapper (`src/mapping/concept_mapper.py`)

#### Problem in Current Code
`standard_map` is a hardcoded Python dictionary containing ~250 entries. When a new industry arrives, unmapped labels fall back to `ext.<ticker>.<slug>`, which preserves the fact but prevents cross-company comparability.

#### Proposed Solution: Two-Tier Cache & Few-Shot Learning

```
                       ┌─────────────────────────┐
                       │   Raw Reported Label    │
                       └────────────┬────────────┘
                                    │
                                    ▼
                         Is label in standard_map
                         or DuckDB ref table?
                                   / \
                            YES   /   \   NO
                                 /     \
                                ▼       ▼
                         ┌─────────┐  ┌───────────────────────────────┐
                         │ Return  │  │ LLM Concept Resolver          │
                         │ mapped  │  │ (Prompt with few-shot         │
                         │ concept │  │  examples from standard_map)  │
                         │  O(1)   │  └───────────────┬───────────────┘
                         └─────────┘                  │
                                                      ▼
                                              Classifies into:
                                              - Canonical concept OR
                                              - Standardized extension
                                                      │
                                                      ▼
                                              Auto-persist to DuckDB:
                                              idxfin_ref.label_mapping
                                                      │
                                                      ▼
                                              Future lookups are O(1)
```

1. **Fast-Path $O(1)$ Cache**:
   - Retain the verified `standard_map` as a compiled cache.
   - Any known label resolves instantaneously with zero token cost.
2. **Few-Shot In-Context Prompting**:
   - For an unseen label, prompt the LLM by supplying representative few-shot examples from the existing verified dictionary across industries:
     - Mining: `"beban pengupasan tanah"` $\to$ `fs.is.cogs`
     - Banking: `"beban bunga"` $\to$ `fs.is.interest_expense`
     - Aviation: `"pendapatan jasa kebandarudaraan"` $\to$ `fs.is.revenue`
     - Agribusiness: `"penjualan kelapa sawit"` $\to$ `fs.is.revenue`
   - The LLM selects the canonical taxonomy concept or creates a clean extension tag (`ext.<sector>.<slug>`).
3. **Persistent DuckDB Expansion**:
   - Newly resolved mappings are written to `idxfin_ref.label_mapping`.
   - On the next run, the label is read from DuckDB, growing the taxonomy organically without human intervention.

---

### Pillar 3: LLM Cover-Page Metadata Inspector (`src/parser/pdf_loader.py`)

#### Problem in Current Code
`pdf_loader.py` uses heuristic regex scanning across pages 1–3:
```python
if any(term in first_pages.lower() for term in ["dalam jutaan", "in million"]):
    self.scale = 1_000_000
```
This is vulnerable to slight wording differences, foreign currencies, or custom scaling declarations.

#### Proposed Solution: Targeted Single-Pass LLM Extraction
- Send the first 2–3 pages (Cover and General Disclosure `[1000000]`) to the LLM.
- Enforce strict Pydantic parsing:
  ```python
  class FilingMetadata(BaseModel):
      ticker: str
      legal_name: str
      doc_type: Literal["fs_annual", "fs_interim", "annual_report", "sustainability_report"]
      fiscal_year: int
      period_end: str                         # ISO-8601: YYYY-MM-DD
      currency: str                           # IDR, USD, EUR, SGD, etc.
      scale: int                              # 1, 1000, 1000000, 1000000000, 1000000000000
      number_locale: Literal["id", "en"]      # Decimal separator convention (. vs ,)
      presentation_format: Literal["classified", "order_of_liquidity"]
      accounting_regime: Literal["general_corporate", "sharia_banking", "conventional_banking", "insurance_psak117"]
  ```
- **Benefits**:
  - Cost: ~600 tokens ($0.0003), executed once per document.
  - Resilience: Correctly interprets complex headers like *"Dinyatakan dalam ribuan Dolar AS, kecuali dinyatakan lain"*.
  - Output is saved directly in `idxfin_bronze.document`.

---

### Pillar 4: Table-of-Contents (TOC) Guided Sectionizer (`src/parser/sectionizer.py`)

#### Problem in Current Code
For 800+ page Annual Reports, `_sectionize_ar` scans every page searching for keyword regexes (`ikhtisar keuangan`, `analisis dan pembahasan manajemen`). It fails when companies use creative or branded titles (*"Kilas Balik Kinerja"*, *"Profil Entitas"*).

#### Proposed Solution: Two-Phase TOC Parsing
1. **Locate TOC Pages**: Scan pages 1–10 for Table of Contents / *Daftar Isi* (consistent across 99% of Indonesian reports).
2. **LLM TOC Extraction**:
   - Send the TOC text to the LLM with prompt:
     ```
     You are an expert indexer of Indonesian corporate Annual Reports.
     Parse the Table of Contents and identify the exact starting and ending page numbers for:
     1. Financial & Operational Highlights (ar.highlights)
     2. Management Discussion & Analysis / Operational Review (ar.mdna.kpis)
     3. Board of Directors & Commissioners Profiles (ar.governance.officers)
     4. Audited Financial Statements (fs.primary)
     ```
   - Returns structured chapter ranges directly from the document's own index.
3. **Zero Full-Text Scanning**: The sectionizer instantly routes extractors to the exact page ranges without inspecting 800 intermediate pages.

---

### Pillar 5: Generalized Storage & Gold Views (`src/storage/gold_views.py`)

#### Problem in Current Code
`financial_summary` uses a rigid `COALESCE(revenue, interest_income)`. Companies with non-standard revenue classifications (e.g. Gross Premiums for Insurance, Brokerage Fees for Securities) show `NULL` for revenue.

#### Proposed Solution: Concept Family Hierarchies
Update `gold_views.py` to evaluate broader concept groups:
- **Revenue Family**:
  ```sql
  COALESCE(
      fact_revenue.value,
      fact_interest_inc.value,
      fact_premium_inc.value,
      fact_brokerage_inc.value,
      fact_operating_rev.value
  ) AS revenue
  ```
- **Capex Family**:
  Expand beyond PPE additions to include sector-specific capital investments:
  ```sql
  COALESCE(
      fact_capex_ppe.value,
      fact_capex_bearer_plants.value,      -- Agribusiness (LSIP)
      fact_capex_exploration.value,        -- Mining (ADRO, NCKL)
      fact_capex_intangibles.value         -- Tech (ATIC)
  ) AS capex
  ```

---

## 4. Implementation Phasing & Work Breakdown

```
  Phase 1: Validation Engine ──► Phase 2: Hybrid Mapper ──► Phase 3: Ingestion/TOC ──► Phase 4: Full Suite
  • Hierarchical roll-up        • Few-shot LLM resolver    • LLM FilingMetadata        • Verify all 10
  • Format-aware dispatch       • DuckDB persistence table • LLM TOC segmenter           companies pass 100%
  • LLM anomaly reconciler      • O(1) cache lookup        • Fallback safeguards       • Zero regression
```

### Detailed Task List

#### Phase 1: Validation Engine Modernization (`src/validation/engine.py`)
- [ ] Implement `_detect_presentation_format(facts)` to identify classified vs. order-of-liquidity statements.
- [ ] Add `validate_hierarchical_rollup(statement_type, facts)` to deterministically test reported parent-child summations ($\text{Parent} = \sum \text{Children}$).
- [ ] Add accounting regime equations for Sharia Banking (Temporary Syirkah Funds) and Discontinued Operations (PSAK 58).
- [ ] Implement `reconcile_variance_with_llm(implicated_facts, statement_text)`:
  - Invokes LLM with implicated row facts when $|\text{diff}| > \text{tolerance}$.
  - Verifies LLM-suggested reconciling items algebraically before accepting.
  - Logs reconciliation rationales into the validation result.

#### Phase 2: Hybrid Few-Shot Concept Mapper (`src/mapping/concept_mapper.py`)
- [ ] Ensure DuckDB schema includes `idxfin_ref.label_mapping` table.
- [ ] Retain `standard_map` as primary $O(1)$ in-memory cache.
- [ ] Add `resolve_unmapped_label_with_llm(label, statement_type, context)`:
  - Injects few-shot examples from `standard_map` into prompt.
  - Resolves novel labels to canonical concepts or standardized extensions.
  - Auto-inserts new mappings into `idxfin_ref.label_mapping`.

#### Phase 3: LLM Ingestion & Sectionizer (`src/parser/`)
- [ ] `src/parser/pdf_loader.py`: Add `extract_filing_metadata_with_llm(cover_pages_text)` using `FilingMetadata` Pydantic model.
- [ ] `src/parser/sectionizer.py`: Add `extract_toc_sections_with_llm(toc_text)` for Annual Report chapter boundary detection.
- [ ] Add safety fallback: if LLM call fails or times out, smoothly fall back to deterministic pattern rules.

#### Phase 4: Storage Views & Full Verification
- [ ] Update `src/storage/gold_views.py` with expanded concept families for revenue and capex.
- [ ] Run full automated test suite:
  ```bash
  python3 -m unittest discover tests -p "test_*.py"
  ```
- [ ] Verify that all 10 issuers (NCKL, IPCM, BBSI, ATIC, CASS, LSIP, ABMM, ADRO, BBCA, BREN) retain 100% test pass rates and zero balance sheet difference.

---

## 5. Verification Plan

### Automated Regression Testing
We will execute the existing test suite of 34 test cases to guarantee that generalizing the architecture causes zero regressions:
```bash
python3 -m unittest discover tests -p "test_*.py"
```

### New Targeted Unit Tests
We will add unit test cases to verify the new LLM-first capabilities:
1. `tests/test_hierarchical_validation.py`:
   - Verifies parent-child roll-up equations on arbitrarily named line items.
   - Tests order-of-liquidity balance sheet handling (BBCA, BBSI).
2. `tests/test_llm_anomaly_reconciliation.py`:
   - Simulates balance sheets with Sharia Syirkah funds and Discontinued Operations.
   - Verifies that the LLM reconciler identifies the reconciling entry and returns exact 0.00 arithmetic diff.
3. `tests/test_hybrid_concept_mapper.py`:
   - Tests that known labels resolve in $O(1)$ without LLM invocation.
   - Tests that novel labels resolve via few-shot LLM and persist to DuckDB.

---

## 6. User Review & Decision Checkpoint

> [!IMPORTANT]
> **AWAITING USER FEEDBACK**:  
> No source code modifications will begin until you have reviewed this plan and confirmed that you want to proceed.
>
> Please confirm:
> 1. Does the division of responsibilities (LLM for semantics & reconciliation, Python for arithmetic & schema enforcement) align with your expectations?
> 2. Are you ready for us to begin implementing **Phase 1 (Validation Engine Modernization)**?
