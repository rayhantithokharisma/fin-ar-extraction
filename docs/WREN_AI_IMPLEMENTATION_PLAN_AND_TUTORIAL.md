# Enterprise Implementation Plan & Tutorial: Deploying Wren AI on the IDX Financial Statement & Market Intelligence Platform

> **Document Type:** System Architecture, Semantic Engineering & Deployment Tutorial  
> **Authors / Roles:** Staff Machine Learning Engineer & Lead Data Engineer  
> **Target System:** IDX Financial Statement & Annual Report Extraction Pipeline (`statements-extraction`)  
> **Status:** Final Architectural Plan & Implementation Tutorial (Pre-Implementation Phase)  
> **Location:** `docs/WREN_AI_IMPLEMENTATION_PLAN_AND_TUTORIAL.md`  

---

## 1. Executive Summary & Strategic Value Proposition

The **IDX Financial Statement & Annual Report Extraction Pipeline** ingests, normalizes, validates, and stores fundamental financial statements, corporate governance profiles, operational KPIs, and 4-year daily market and commodity benchmarks across 14 benchmark issuers spanning 11 industry sectors. All data is structured in an embedded, high-performance **DuckDB** warehouse (`data/idxfin.duckdb`) adhering to the Medallion Architecture (`idxfin_ref`, `idxfin_bronze`, `idxfin_silver`, `idxfin_market`, `idxfin_gold`).

While the pipeline achieves **100% accounting compliance** and zero balance sheet discrepancy across all issuers, consumption currently requires analysts to write complex DuckDB SQL queries, execute Python scripts, or navigate Jupyter notebooks. 

To bridge the gap between technical data engineering and business decision-makers (equity research analysts, portfolio managers, credit officers, and corporate controllers), we introduce **Wren AI**—an open-source **Generative Business Intelligence (GenBI)** and **Agentic Data Analytics Chatbot Platform**.

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                                 THE GEN-BI SHIFT                                       │
├────────────────────────────────────────┬───────────────────────────────────────────────┤
│         TRADITIONAL TEXT-TO-SQL        │            WREN AI SEMANTIC AGENT             │
├────────────────────────────────────────┼───────────────────────────────────────────────┤
│ • Hallucinates arbitrary column names  │ • Governed by Modeling Definition Lang (MDL)  │
│ • Ignores PSAK 101 Sharia accounting   │ • Codifies domain rules (Syirkah, Direct CF)  │
│ • Misinterprets multi-currency mix     │ • Currency-aware joins (IDR vs USD)           │
│ • Fails on complex temporal joins      │ • Encapsulates pre-built Gold analytical views│
│ • Zero citation or audit trail         │ • Grounded to exact PDF pages via Provenance  │
└────────────────────────────────────────┴───────────────────────────────────────────────┘
```

By deploying Wren AI directly over the DuckDB warehouse, we transform our extraction lakehouse into an **interactive, conversational financial analyst** capable of answering complex inquiries in natural language (Indonesian or English):
- *"Bandingkan pertumbuhan laba bersih NCKL dan ADRO untuk tahun buku 2024 dan 2025."*
- *"Which commercial bank had the lowest Cost of Funds and highest CASA ratio in 2025?"*
- *"Show the correlation between COMEX Gold price movements and ARCI's revenue over the last 3 years."*
- *"Verify BBCA's balance sheet reconciliation and show the source PDF page citations."*

---

## 2. System Architecture & Topology

### 2.1 Unified Architecture Overview

The system bridges the asynchronous batch extraction pipeline (FastAPI, Redis, Celery, DuckDB) with Wren AI's semantic microservices (`wren-ui`, `wren-engine`, `wren-ai-service`, `ibis-server`, `qdrant`).

```
                              ┌─────────────────────────────────────────────────────────┐
                              │                    END-USER CLIENTS                     │
                              │  (Financial Analysts, Fund Managers, Executive Suite)   │
                              └────────────────────────────┬────────────────────────────┘
                                                           │ HTTP / WebSocket
                                                           ▼
┌───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                                 WREN AI GENBI STACK                                                   │
│                                                                                                                       │
│   ┌───────────────────────────────────────────────────────────────────────────────────────────────────────────────┐   │
│   │                                       Wren UI (Next.js / React, Port 3000)                                    │   │
│   │               • Natural Language Chatbot    • Semantic Modeler    • Data Exploration & Dashboards             │   │
│   └──────────────────────────────────────┬────────────────────────────────┬───────────────────────────────────────┘   │
│                                          │                                │                                           │
│                 Semantic Queries (MDL)   ▼                                ▼  RAG & Text-to-SQL Requests               │
│   ┌───────────────────────────────────────────────┐     ┌─────────────────────────────────────────────────────────┐   │
│   │       Wren Engine (Rust / DataFusion)         │     │         Wren AI Service (FastAPI / LiteLLM)             │   │
│   │  • Translates semantic MDL to DuckDB SQL      │     │  • Intent decomposition & conversational agent          │   │
│   │  • Metric aggregation & relationship planning │     │  • Few-shot prompt assembly & schema filtering          │   │
│   │  • Dialect-aware SQL execution                │     │  • SQL syntax validation & auto-correction              │   │
│   └──────────────────────┬────────────────────────┘     └────────────┬────────────────────────────┬───────────────┘   │
│                          │                                           │                            │                   │
│                          │                                           ▼ Vector Context             ▼ LLM Invocations   │
│                          │                               ┌──────────────────────┐     ┌───────────────────────┐       │
│                          │                               │    Qdrant Vector DB  │     │   OpenAI LLM Provider │       │
│                          │                               │  • Semantic metadata │     │  • gpt-5.6-luna (Pri) │       │
│                          │                               │  • Bilingual terms   │     │  • gpt-5.4-mini (Sec) │       │
│                          │                               │  • Verified SQL pairs│     │  • text-embedding-3   │       │
│                          │                               └──────────────────────┘     └───────────────────────┘       │
└──────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────┘
                           │
                           │ Native Read-Only Connection (Direct or Replicated Lakehouse)
                           ▼
┌───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                         IDX FINANCIAL WAREHOUSE (DuckDB)                                              │
│                                                                                                                       │
│   ┌───────────────────────┐  ┌───────────────────────┐  ┌───────────────────────┐  ┌──────────────────────────────┐   │
│   │      idxfin_ref       │  │     idxfin_silver     │  │     idxfin_market     │  │         idxfin_gold        │   │
│   │  • 148 seeded concepts│  │  • fact (3,325 rows)  │  │  • stock_daily (4y)   │  │  • financial_summary        │   │
│   │  • label_mapping      │  │  • officer_position   │  │  • commodity_daily    │  │  • financial_summary_prov    │   │
│   │  • validation_rules   │  │  • audit_report       │  │    (GC=F, BZ=F, CL=F, │  │  • market_valuation         │   │
│   │  • issuers & axes     │  │  • forward_guidance   │  │     ESSA, CF, NG=F)   │  │  • commodity_trend_monthly   │   │
│   └───────────────────────┘  └───────────────────────┘  └───────────────────────┘  └──────────────────────────────┘   │
└───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
                                   ▲
                                   │ Serialized Writes (FileLock & Concurrency = 1)
┌──────────────────────────────────┴────────────────────────────────────────────────────────────────────────────────────┐
│                                       INGESTION & STAGING PIPELINE (Existing)                                         │
│   • Redis 7 Message Broker                                                                                            │
│   • 6 Celery Extraction Workers (PyMuPDF, Sectionizer, OpenAI / OpenRouter LLM, Normalization)                        │
│   • Parquet Staging Lake (`data/staging/pending/{run_id}/`)                                                           │
│   • 1 Single-Writer Celery DuckDB Worker (`duckdb_writer`)                                                            │
└───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

### 2.2 The Data Engineering Challenge: Solving DuckDB In-Process Concurrency

#### The Root Cause
DuckDB is an embedded, in-process OLAP database engine. While DuckDB supports multiple concurrent **readers**, it only supports **one writer at a time**, and locks the database file (`data/idxfin.duckdb`). If an active writer holds a write lock, any process attempting to open the database in read-write mode crashes immediately with:
```text
duckdb.IOException: Could not set lock on file "data/idxfin.duckdb": Resource temporarily unavailable
```

Our extraction pipeline solves this internally via **Parquet Staging** and a single-writer worker (`duckdb_writer`) using `FileLock("data/idxfin.duckdb.lock")`. However, when **Wren Engine** or **Ibis Server** connects to `data/idxfin.duckdb`, it must never attempt to acquire an exclusive write lock or block the ingestion pipeline.

#### The Architectural Solutions

##### Strategy A: Read-Only Volume Mount & DuckDB Read-Only Flag (Recommended for Development & Small Teams)
1. Mount the DuckDB database file into the Wren AI container as a **read-only volume**:
   ```yaml
   volumes:
     - ./data/idxfin.duckdb:/app/data/idxfin.duckdb:ro
   ```
2. Configure Wren Engine's connection string / initial SQL with `PRAGMA access_mode='read_only';`.
3. DuckDB allows concurrent readers alongside a reader-writer process as long as the OS permissions and DuckDB connection flags are explicitly set to read-only.

##### Strategy B: Decoupled Analytics Replica / Shadow DB (Recommended for High-Throughput Production)
To achieve complete isolation between the extraction pipeline and interactive analytics:
1. The Celery `duckdb_writer` worker executes an atomic copy or checkpoint to `data/serving/idxfin_serving.duckdb` upon completing a batch ingestion:
   ```bash
   # In Celery duckdb_writer post-ingest hook:
   duckdb data/idxfin.duckdb "CHECKPOINT; COPY FROM DATABASE idxfin TO idxfin_serving;"
   # Or a zero-downtime symlink swap / rsync:
   rsync -a --inplace data/idxfin.duckdb data/serving/idxfin_serving.duckdb
   ```
2. Wren AI connects exclusively to `data/serving/idxfin_serving.duckdb`. Ingestion jobs can run at 100% saturation without risking lock collisions or UI query latency spikes.

---

### 2.3 The ML Engineering Challenge: Domain Grounding & Semantic Modeling

Raw Text-to-SQL approaches fail miserably on specialized Indonesian corporate filings because of four core domain complexities:

| Domain Complexity | What Naked LLMs Do (Failure Mode) | How Wren AI Governs It (Success Mode) |
|---|---|---|
| **PSAK 101 Sharia Banking** | Assumes $\text{Assets} = \text{Liab} + \text{Equity}$. Balance sheet fails to balance for BBCA/BCA Syariah. | MDL defines `temporary_syirkah_funds` and incorporates the formula: $\text{Assets} = \text{Liab} + \text{Equity} + \text{Syirkah}$. |
| **Bilingual Taxonomy (ID/EN)** | Confuses *"Laba Usaha"* with *"Laba Bersih"* or fails when an emiten writes *"Beban Pokok Pendapatan"*. | Qdrant vector memory maps synonyms and alternate labels to canonical concepts (`fs.is.cost_of_revenue`, `fs.is.profit_for_the_period`). |
| **Multi-Currency Reporting** | Sums ARCI (USD) and NCKL (IDR) together directly, generating absurd totals ($10\text{T} + \$1\text{B} = 10,000,000,000,001$). | Semantic Model defines currency dimensions and rejects multi-currency aggregation without explicit FX normalization. |
| **Direct Cash Flow Signs** | Capex is reported as an outflow (negative) on face statements, but positive in derived Capex summaries. | Semantic Measures use `ABS(capex)` or pre-aggregated Gold measures to prevent double negatives. |
| **Cumulative YTD vs Discrete Quarters** | Generates queries against H1 or 9M cumulative figures when answering "Q2 2025 revenue". | Semantic Model exposes `fact_discrete_quarter` view with unbundled discrete quarterly metrics. |
| **Verbatim Provenance Audit** | Cannot cite where numbers came from, reducing investor trust. | Semantic Model links `financial_summary` to `financial_summary_provenance`, returning page citations (`p.18`, `p.24`). |

---

## 3. Step-by-Step Implementation Plan

### Phase 1: Environment & Directory Preparation

Create a dedicated `wren/` configuration hierarchy in the project root:

```bash
mkdir -p wren/projects/idxfin/models
mkdir -p wren/projects/idxfin/views
mkdir -p wren/projects/idxfin/knowledge
mkdir -p wren/data_replica
```

Target folder layout:
```text
statements-extraction/
├── data/
│   └── idxfin.duckdb                 # Source Medallion warehouse
├── wren/
│   ├── docker-compose.wren.yml       # Standalone or composable Wren AI stack
│   ├── config.yaml                   # LiteLLM & Qdrant configuration
│   ├── .env.wren                     # Wren AI environment variables & API keys
│   └── projects/
│       └── idxfin/
│           ├── wren_project.yml      # Project metadata & data source connection
│           ├── relationships.yml     # Relational join paths across models
│           ├── models/               # MDL semantic models
│           │   ├── financial_summary.yaml
│           │   ├── market_valuation.yaml
│           │   ├── commodity_trend_monthly.yaml
│           │   ├── officer_position.yaml
│           │   ├── audit_report.yaml
│           │   └── fact_discrete_quarter.yaml
│           └── knowledge/            # Financial domain rules & golden Q&A pairs
│               ├── accounting_rules.yaml
│               └── golden_queries.yaml
```

---

### Phase 2: Docker Compose Orchestration & Service Networking

To integrate seamlessly with the existing `docker-compose.yml` (which manages `redis`, `api`, `worker-extraction`, and `worker-duckdb-writer`), we define `wren/docker-compose.wren.yml`. It runs on the shared Docker bridge network `idxfin-network`.

#### `wren/docker-compose.wren.yml`

```yaml
networks:
  idxfin-network:
    name: idxfin-network
    driver: bridge

volumes:
  qdrant_data:
  wren_engine_data:

services:
  # -----------------------------------------------------------------------------
  # 1. Qdrant Vector Database (Context memory, MDL semantic search, few-shot Q&A)
  # -----------------------------------------------------------------------------
  qdrant:
    image: qdrant/qdrant:v1.9.0
    container_name: wren-qdrant
    networks:
      - idxfin-network
    ports:
      - "6333:6333"
    volumes:
      - qdrant_data:/qdrant/storage
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "/bin/bash", "-c", "exec 3<>/dev/tcp/127.0.0.1/6333 && exec 3<&-"]
      interval: 5s
      timeout: 3s
      retries: 5

  # -----------------------------------------------------------------------------
  # 2. Wren Engine (Rust / Apache DataFusion semantic query planner)
  # Uses linux/amd64 platform emulation for Apple Silicon compatibility
  # -----------------------------------------------------------------------------
  wren-engine:
    image: ghcr.io/canner/wren-engine:latest
    platform: linux/amd64
    container_name: wren-engine
    networks:
      - idxfin-network
    ports:
      - "8080:8080"
    environment:
      - WREN_ENGINE_PORT=8080
      - LOG_LEVEL=info
    volumes:
      - wren_engine_data:/app/data
      # Mount DuckDB in READ-ONLY mode to eliminate lock collisions
      - ../data/idxfin.duckdb:/app/data/idxfin.duckdb:ro
      - ./projects/idxfin:/app/projects/idxfin
    restart: unless-stopped

  # -----------------------------------------------------------------------------
  # 3. Ibis Server (Data execution & dialect transformation)
  # Uses linux/amd64 platform emulation for Apple Silicon compatibility
  # -----------------------------------------------------------------------------
  ibis-server:
    image: ghcr.io/canner/wren-engine-ibis:latest
    platform: linux/amd64
    container_name: wren-ibis-server
    networks:
      - idxfin-network
    ports:
      - "8000:8000"
    volumes:
      - ../data/idxfin.duckdb:/data/idxfin.duckdb:ro
    restart: unless-stopped

  # -----------------------------------------------------------------------------
  # 4. Wren AI Service (FastAPI, LiteLLM orchestration, prompt engine)
  # -----------------------------------------------------------------------------
  wren-ai-service:
    image: ghcr.io/canner/wren-ai-service:latest
    container_name: wren-ai-service
    networks:
      - idxfin-network
    ports:
      - "5555:5555"
    environment:
      - QDRANT_HOST=qdrant
      - QDRANT_PORT=6333
      - WREN_ENGINE_ENDPOINT=http://wren-engine:8080
      - IBIS_SERVER_ENDPOINT=http://ibis-server:8000
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      - LLM_PROVIDER=${LLM_PROVIDER:-openai}
      - DEFAULT_MODEL=${DEFAULT_MODEL:-gpt-5.6-luna}
      - FALLBACK_MODEL=${FALLBACK_MODEL:-gpt-5.4-mini}
      - CONFIG_PATH=/app/config.yaml
    volumes:
      - ./config.yaml:/app/config.yaml:ro
      - ./projects/idxfin:/app/projects/idxfin
    depends_on:
      qdrant:
        condition: service_healthy
      wren-engine:
        condition: service_started
    restart: unless-stopped

  # -----------------------------------------------------------------------------
  # 5. Wren UI (Next.js Frontend: GenBI Chatbot, Visual Modeler, Dashboards)
  # -----------------------------------------------------------------------------
  wren-ui:
    image: ghcr.io/canner/wren-ui:latest
    container_name: wren-ui
    networks:
      - idxfin-network
    ports:
      - "3000:3000"
    environment:
      - WREN_AI_SERVICE_ENDPOINT=http://wren-ai-service:5555
      - WREN_ENGINE_ENDPOINT=http://wren-engine:8080
    depends_on:
      - wren-ai-service
    restart: unless-stopped
```

---

### Phase 3: AI Service & LiteLLM Configuration

Wren AI uses **LiteLLM** under the hood. In strict alignment with our `.env` configuration (`LLM_PROVIDER=openai`), we configure the **OpenAI LLM provider** utilizing:
- **`gpt-5.6-luna`** as the primary default reasoning model for complex financial SQL planning, multi-table semantic joins, and PSAK domain reasoning.
- **`gpt-5.4-mini`** as the fast / fallback model for rapid query routing, simple single-table aggregations, and high-speed execution.

#### `wren/config.yaml`

```yaml
# =============================================================================
# Wren AI Service Configuration (OpenAI Provider, Embedder & Vector Store)
# Aligned with .env: LLM_PROVIDER=openai, DEFAULT_MODEL=gpt-5.6-luna, FALLBACK_MODEL=gpt-5.4-mini
# =============================================================================

type: llm
provider: litellm_llm
models:
  # Primary reasoning model: Deep SQL planning, multi-table joins, PSAK accounting reasoning
  - alias: default
    model: gpt-5.6-luna
    api_key: os.environ/OPENAI_API_KEY
    kwargs:
      temperature: 0.0
      max_tokens: 4096
      top_p: 0.95

  # Fast / Fallback reasoning model: High-speed queries, simple filters, and operational tasks
  - alias: fallback
    model: gpt-5.4-mini
    api_key: os.environ/OPENAI_API_KEY
    kwargs:
      temperature: 0.0
      max_tokens: 4096

---
type: embedder
provider: litellm_embedder
models:
  # OpenAI embedding model for indexing schema definitions, descriptions, and Q&A
  - alias: default
    model: text-embedding-3-small
    api_key: os.environ/OPENAI_API_KEY
    kwargs:
      dimensions: 1536

---
type: vector_store
provider: qdrant
properties:
  host: qdrant
  port: 6333
```

---

### Phase 4: Semantic Modeling in MDL (Modeling Definition Language)

The Modeling Definition Language (MDL) defines the semantic contracts that govern how the LLM interprets tables, columns, relations, and calculated business metrics.

#### 1. Project Configuration: `wren/projects/idxfin/wren_project.yml`

```yaml
version: "0.1.0"
project_name: "idxfin_analytics"

datasource:
  type: duckdb
  properties:
    # Path inside container
    path: "/app/data/idxfin.duckdb"
    read_only: true
    init_sql:
      - "PRAGMA access_mode='read_only';"
      - "PRAGMA threads=4;"
```

---

#### 2. Primary Financial Summary Model: `wren/projects/idxfin/models/financial_summary.yaml`

Maps the pivoted Gold fundamental view containing 14 companies across 11 sectors:

```yaml
model:
  name: financial_summary
  table_reference: "idxfin_gold.financial_summary"
  description: "Standardized cross-industry financial statement summary for Indonesian public companies (IDX) under IFRS and PSAK. Contains face balance sheet, income statement, and cash flow items."
  
  primary_key:
    - issuer_id
    - period_end

  columns:
    - name: issuer_id
      type: varchar
      description: "4-character IDX stock ticker symbol (e.g. BBCA, NCKL, ADRO, MAPI, ARCI, BREN)."
      
    - name: period_end
      type: date
      description: "Financial reporting period end date (e.g. 2025-12-31)."
      
    - name: fiscal_year
      type: integer
      description: "Fiscal year (e.g. 2024, 2025)."
      
    - name: fiscal_period
      type: varchar
      description: "Reporting period code: FY (Full Year annual audited), Q1 (First Quarter), H1 (First Half), 9M (Nine Months)."
      
    - name: currency
      type: varchar
      description: "Reporting currency: IDR (Indonesian Rupiah) or USD (US Dollar). Always check currency before aggregating."
      
    - name: total_assets
      type: decimal
      description: "Total assets as reported on the statement of financial position. In full nominal currency units."
      
    - name: current_assets
      type: decimal
      description: "Total current assets. For commercial banks (e.g. BBCA, BBSI) presenting in order of liquidity, this field is NULL."
      
    - name: total_liabilities
      type: decimal
      description: "Total liabilities in full nominal currency units."
      
    - name: current_liabilities
      type: decimal
      description: "Total current liabilities. NULL for commercial banks presenting in order of liquidity."
      
    - name: total_equity
      type: decimal
      description: "Total equity attributable to owners and non-controlling interests."
      
    - name: temporary_syirkah_funds
      type: decimal
      description: "Dana Syirkah Temporer under PSAK 101 for Islamic institutions and commercial banks with sharia subsidiaries (e.g. BBCA / BCA Syariah). Situated between liabilities and equity. Assets = Liabilities + Equity + Syirkah Funds."
      
    - name: revenue
      type: decimal
      description: "Top-line gross operating revenue. For corporate entities, this is gross sales/contracts; for commercial banks, this coalesces interest income and operating income."
      
    - name: cost_of_revenue
      type: decimal
      description: "Cost of goods sold (COGS) or banking interest expense (cost of funds)."
      
    - name: gross_profit
      type: decimal
      description: "Gross profit for corporate entities, or Net Interest Income (Interest Income - Interest Expense) for commercial banks."
      
    - name: net_profit
      type: decimal
      description: "Bottom-line net profit for the year/period (Laba tahun berjalan) attributable to the entity."
      
    - name: operating_cash_flow
      type: decimal
      description: "Net cash flows generated from operating activities (Direct method)."
      
    - name: capex
      type: decimal
      description: "Capital expenditures (Acquisition of Property, Plant & Equipment, Bearer Plants, or Intangible Assets from cash flows). Expressed as a positive nominal magnitude."
      
    - name: bank_loans
      type: decimal
      description: "Gross loan portfolio (Kredit yang diberikan) for commercial banks (BBCA, BBSI)."
      
    - name: bank_deposits
      type: decimal
      description: "Customer deposits (Dana Pihak Ketiga: Giro, Tabungan, Deposito) for commercial banks."

  calculated_fields:
    # Profitability Ratios
    - name: return_on_equity_pct
      type: decimal
      expression: "ROUND((net_profit / NULLIF(total_equity, 0)) * 100, 2)"
      description: "Return on Equity (ROE) percentage."

    - name: return_on_assets_pct
      type: decimal
      expression: "ROUND((net_profit / NULLIF(total_assets, 0)) * 100, 2)"
      description: "Return on Assets (ROA) percentage."

    - name: net_profit_margin_pct
      type: decimal
      expression: "ROUND((net_profit / NULLIF(revenue, 0)) * 100, 2)"
      description: "Net Profit Margin percentage."

    - name: gross_profit_margin_pct
      type: decimal
      expression: "ROUND((gross_profit / NULLIF(revenue, 0)) * 100, 2)"
      description: "Gross Profit Margin percentage (or Net Interest Margin proxy for banks)."

    # Solvency & Capital Structure Ratios
    - name: debt_to_equity_ratio
      type: decimal
      expression: "ROUND(total_liabilities / NULLIF(total_equity, 0), 2)"
      description: "Debt to Equity Ratio (Total Liabilities / Total Equity)."

    - name: current_ratio
      type: decimal
      expression: "ROUND(current_assets / NULLIF(current_liabilities, 0), 2)"
      description: "Current Ratio (Current Assets / Current Liabilities). Applicable only to non-bank entities."

    # Banking Specific Metrics
    - name: loan_to_deposit_ratio_pct
      type: decimal
      expression: "ROUND((bank_loans / NULLIF(bank_deposits, 0)) * 100, 2)"
      description: "Loan to Deposit Ratio (LDR) for commercial banks."
```

---

#### 3. Cross-Asset Market Valuation Model: `wren/projects/idxfin/models/market_valuation.yaml`

Connects fundamental accounting facts with 4 years of daily OHLCV trading bars from Yahoo Finance:

```yaml
model:
  name: market_valuation
  table_reference: "idxfin_gold.market_valuation"
  description: "Cross-asset valuation view linking fundamental financial statements with historical stock prices, latest closing market prices, and price returns since filing period end."

  primary_key:
    - issuer_id
    - period_end

  columns:
    - name: issuer_id
      type: varchar
      description: "IDX stock ticker (e.g. ADRO, BBCA, NCKL, ARCI)."
      
    - name: fiscal_year
      type: integer
      description: "Fiscal year of the filing."
      
    - name: fiscal_period
      type: varchar
      description: "Fiscal period (FY, Q1, H1, 9M)."
      
    - name: period_end
      type: date
      description: "Filing period cut-off date."
      
    - name: currency
      type: varchar
      description: "Financial statement currency."
      
    - name: revenue
      type: decimal
      description: "Reported revenue in nominal units."
      
    - name: net_profit
      type: decimal
      description: "Reported net profit in nominal units."
      
    - name: total_equity
      type: decimal
      description: "Reported book value of equity."
      
    - name: period_close_price
      type: decimal
      description: "Stock closing price on or closest to the filing period end date (in IDR/share)."
      
    - name: latest_close_price
      type: decimal
      description: "Most recent trading day closing price (in IDR/share)."
      
    - name: price_change_since_period_pct
      type: decimal
      description: "Percentage return of the stock price from the period close date to the latest market date."
```

---

#### 4. Macroeconomic Commodity Model: `wren/projects/idxfin/models/commodity_trend_monthly.yaml`

Exposes monthly trends across Gold, Crude Oil, and Ammonia proxies:

```yaml
model:
  name: commodity_trend_monthly
  table_reference: "idxfin_gold.commodity_trend_monthly"
  description: "Monthly aggregated commodity benchmark prices, physical spot assessments, and proxies. Includes Gold (GC=F), WTI Crude (CL=F), Brent Crude (BZ=F), Anhydrous Ammonia Spot Benchmark (NH3=F in USD/ton), and Ammonia proxies (ESSA.JK, CF, UAN, NG=F)."

  primary_key:
    - symbol
    - month_date

  columns:
    - name: symbol
      type: varchar
      description: "Commodity symbol: GC=F (Gold), CL=F (WTI Oil), BZ=F (Brent Oil), NH3=F (Anhydrous Ammonia Spot Benchmark in USD/ton), ESSA.JK (Domestic Ammonia producer), CF (Global Ammonia), UAN (Ammonia Fertilizer proxy), NG=F (Natural Gas feedgas)."
      
    - name: commodity_name
      type: varchar
      description: "Full descriptive name of the commodity benchmark or proxy."
      
    - name: commodity_category
      type: varchar
      description: "Category: PRECIOUS_METALS, ENERGY, CHEMICALS_FERTILIZERS."
      
    - name: month_date
      type: date
      description: "First day of the calendar month (e.g. 2025-01-01)."
      
    - name: avg_close
      type: decimal
      description: "Average closing price across all trading days in the month."
      
    - name: min_close
      type: decimal
      description: "Minimum daily close during the month."
      
    - name: max_close
      type: decimal
      description: "Maximum daily close during the month."
      
    - name: end_month_close
      type: decimal
      description: "Final trading day closing price of the month."
      
    - name: monthly_return_pct
      type: decimal
      description: "Monthly percentage price return ((last_close - first_close) / first_close * 100)."
      
    - name: unit
      type: varchar
      description: "Trading unit: USD/troy oz, USD/bbl, USD/MMBtu, IDR/share, USD/share."
```

---

#### 5. Corporate Governance Model: `wren/projects/idxfin/models/officer_position.yaml`

```yaml
model:
  name: officer_position
  table_reference: "idxfin_silver.officer_position"
  description: "Board of Directors (Direksi) and Board of Commissioners (Dewan Komisaris) profiles extracted from Annual Report corporate governance chapters."

  columns:
    - name: issuer_id
      type: varchar
      description: "IDX stock ticker symbol."
      
    - name: person_name
      type: varchar
      description: "Full legal name and titles of the officer or board member."
      
    - name: role
      type: varchar
      description: "Official title (e.g. Direktur Utama, Komisaris Utama, Komisaris Independen, Direktur Keuangan)."
      
    - name: is_independent
      type: boolean
      description: "True if the commissioner is officially designated as Independent Commissioner (Komisaris Independen)."
      
    - name: source_page
      type: integer
      description: "PDF page number where the governance profile was published."
```

---

#### 6. Independent Auditor Model: `wren/projects/idxfin/models/audit_report.yaml`

```yaml
model:
  name: audit_report
  table_reference: "idxfin_silver.audit_report"
  description: "Independent Auditor's Report disclosures: Accounting Firm (KAP), Signing Partner, Audit Opinion, and Key Audit Matters (KAM)."

  columns:
    - name: issuer_id
      type: varchar
      description: "IDX ticker symbol."
      
    - name: fiscal_year
      type: integer
      description: "Audited fiscal year."
      
    - name: audit_firm
      type: varchar
      description: "Public Accounting Firm name (Kantor Akuntan Publik / KAP)."
      
    - name: network
      type: varchar
      description: "Global accounting network affiliation (Big 4: PwC, EY, Deloitte, KPMG, or BDO, etc.)."
      
    - name: signing_partner
      type: varchar
      description: "Name of the signing Certified Public Accountant (CPA)."
      
    - name: opinion
      type: varchar
      description: "Auditor opinion: Unqualified (Wajar Tanpa Pengecualian / WTP), Qualified, Adverse, Disclaimer."
      
    - name: key_audit_matters
      type: varchar
      description: "Titles or descriptions of Key Audit Matters (Hal Audit Utama) identified by the auditor."
      
    - name: source_page
      type: integer
      description: "PDF page number of the Independent Auditor's Report."
```

---

#### 7. Provenance & Page Citations Model: `wren/projects/idxfin/models/financial_summary_provenance.yaml`

```yaml
model:
  name: financial_summary_provenance
  table_reference: "idxfin_gold.financial_summary_provenance"
  description: "Audit trail mapping every metric in financial_summary to its exact source PDF page citation (e.g. p.4, p.18)."

  primary_key:
    - issuer_id
    - period_end

  columns:
    - name: issuer_id
      type: varchar
    - name: period_end
      type: date
    - name: fiscal_year
      type: integer
    - name: total_assets_page
      type: varchar
      description: "PDF page citation for Total Assets (e.g. 'p.4')."
    - name: revenue_page
      type: varchar
      description: "PDF page citation for Revenue (e.g. 'p.18')."
    - name: net_profit_page
      type: varchar
      description: "PDF page citation for Net Profit (e.g. 'p.18')."
    - name: operating_cash_flow_page
      type: varchar
      description: "PDF page citation for Operating Cash Flow (e.g. 'p.24')."
    - name: capex_page
      type: varchar
      description: "PDF page citation for Capex (e.g. 'p.25')."
```

---

#### 8. Operational KPI Model: `wren/projects/idxfin/models/operational_kpi.yaml`

```yaml
model:
  name: operational_kpi
  table_reference: "idxfin_gold.operational_kpi"
  description: "Operational Key Performance Indicators (KPIs) extracted from Annual Reports and Financial Statement disclosures: Production Volumes, Sales Volumes, Installed Capacities, and Utilization across Petrochemicals (Ammonia, LPG, Condensate), Mining (Nickel, Coal, Gold), Agriculture (CPO, TBS), and Infrastructure."

  primary_key:
    - fact_id

  columns:
    - name: fact_id
      type: varchar
      description: "Unique SHA-256 identifier for the fact."
    - name: issuer_id
      type: varchar
      description: "IDX stock ticker symbol (e.g. ESSA, NCKL, ADRO, LSIP, ARCI)."
    - name: fiscal_year
      type: integer
      description: "Fiscal reporting year (e.g. 2023, 2024, 2025)."
    - name: fiscal_period
      type: varchar
      description: "Reporting period code (FY, Q1, H1, 9M)."
    - name: concept_id
      type: varchar
      description: "Standardized operational concept ID (e.g. op.core.production_volume, op.core.sales_volume, op.core.installed_capacity)."
    - name: label_raw
      type: varchar
      description: "Original disclosure line-item label from filing (e.g. Produksi Amoniak, Penjualan Amoniak, Produksi Elpiji, Produksi CPO)."
    - name: value
      type: decimal
      description: "Reported numeric physical volume or metric value."
    - name: unit_id
      type: varchar
      description: "Measurement unit (e.g. metrik ton, ton, bbl, MT, barrels per day)."
    - name: lineage_page
      type: integer
      description: "Source PDF page number in the original disclosure filing."
```

---

#### 9. Foreign Exchange (FX) Benchmark Model: `wren/projects/idxfin/models/fx_rate.yaml`

```yaml
model:
  name: fx_rate
  table_reference: "idxfin_gold.fx_rate_daily"
  description: "Daily foreign exchange (FX) rates and currency conversion benchmarks from Yahoo Finance (e.g. USD/IDR). Provides daily close, monthly average, and annual average rates to convert financial statement USD disclosures to IDR and evaluate cross-currency benchmarks."

  primary_key:
    - pair
    - trade_date

  columns:
    - name: pair
      type: varchar
      description: "Currency pair symbol (e.g. 'USD/IDR')."
    - name: symbol
      type: varchar
      description: "Yahoo Finance market ticker (e.g. 'IDR=X')."
    - name: trade_date
      type: date
      description: "Trading and valuation date (YYYY-MM-DD)."
    - name: open
      type: decimal
      description: "Opening daily exchange rate."
    - name: high
      type: decimal
      description: "Highest intra-day exchange rate."
    - name: low
      type: decimal
      description: "Lowest intra-day exchange rate."
    - name: rate_close
      type: decimal
      description: "Closing exchange rate for the day (e.g. Rupiah per 1 US Dollar, ~16,085 for Dec 2024, ~16,709 for Dec 2025)."
    - name: rate
      type: decimal
      description: "Benchmark closing spot exchange rate."
    - name: base_currency
      type: varchar
      description: "Base currency code (e.g. 'USD')."
    - name: quote_currency
      type: varchar
      description: "Quote currency code (e.g. 'IDR')."
    - name: month_date
      type: date
      description: "First day of the calendar month (e.g. 2025-12-01)."
    - name: fiscal_year
      type: integer
      description: "Calendar and fiscal year of the rate (e.g. 2024, 2025)."
    - name: monthly_avg_rate
      type: decimal
      description: "Monthly average exchange rate across all trading days in the month."
    - name: yearly_avg_rate
      type: decimal
      description: "Annual average exchange rate across all trading days in the fiscal year (ideal for Income Statement and Cash Flow translations)."
```

---

#### 10. Relational Joins: `wren/projects/idxfin/relationships.yml`

Defines how the models link together so Wren AI can execute multi-table reasoning automatically:

```yaml
relationships:
  - name: financial_summary_to_market_valuation
    from_model: financial_summary
    to_model: market_valuation
    join_type: one_to_one
    condition: "financial_summary.issuer_id = market_valuation.issuer_id AND financial_summary.period_end = market_valuation.period_end"

  - name: financial_summary_to_provenance
    from_model: financial_summary
    to_model: financial_summary_provenance
    join_type: one_to_one
    condition: "financial_summary.issuer_id = financial_summary_provenance.issuer_id AND financial_summary.period_end = financial_summary_provenance.period_end"

  - name: financial_summary_to_officers
    from_model: financial_summary
    to_model: officer_position
    join_type: one_to_many
    condition: "financial_summary.issuer_id = officer_position.issuer_id"

  - name: financial_summary_to_audit_report
    from_model: financial_summary
    to_model: audit_report
    join_type: one_to_one
    condition: "financial_summary.issuer_id = audit_report.issuer_id AND financial_summary.fiscal_year = audit_report.fiscal_year"
```

---

### Phase 5: Knowledge Context & Domain Rules Injection

To prevent hallucinations and guarantee accurate query generation, we populate Wren AI's knowledge repository with accounting guidelines and validated few-shot SQL examples.

#### 1. Accounting Domain Guidelines: `wren/projects/idxfin/knowledge/accounting_rules.yaml`

```yaml
rules:
  - name: currency_isolation_rule
    description: "Indonesian public issuers report in either IDR or USD. ADRO, ABMM, BREN, and ARCI report in USD. BBCA, NCKL, MAPI, ATIC, LSIP, CASS, BBSI, DMAS, BISI, and IPCM report in IDR. When ranking or summing companies across sectors, NEVER add USD and IDR directly without grouping by currency or converting via exchange rate."

  - name: sharia_banking_reconciliation
    description: "For commercial banks with Islamic operations (specifically BBCA / BCA Syariah), the balance sheet includes Dana Syirkah Temporer (temporary_syirkah_funds). The balance sheet equation is: total_assets = total_liabilities + total_equity + temporary_syirkah_funds. For conventional entities, temporary_syirkah_funds is NULL or 0."

  - name: banking_presentation_distinction
    description: "Commercial banks (BBCA, BBSI) do not classify current assets vs non-current assets. Do not query current_assets or current_liabilities for banks. Instead, evaluate bank liquidity using bank_deposits, bank_loans, and loan_to_deposit_ratio_pct."

  - name: capex_nominal_sign
    description: "In the financial_summary model, capex is already normalized to a positive nominal value representing cash outflows for capital expenditures. Do not negate capex when calculating free cash flow: FCF = operating_cash_flow - capex."

  - name: page_provenance_citation
    description: "Whenever a user asks for verification, audit trail, or proof of an extracted financial number, perform a JOIN with financial_summary_provenance and explicitly state the cited PDF page number in the final narrative."
```

---

#### 2. Golden Few-Shot Q&A Pairs: `wren/projects/idxfin/knowledge/golden_queries.yaml`

```yaml
queries:
  # Example 1: Multi-Year Profitability Comparison
  - question: "Berapa perbandingan laba bersih dan pendapatan NCKL tahun 2024 dan 2025?"
    sql: |
      SELECT 
          issuer_id, 
          fiscal_year, 
          currency,
          revenue, 
          net_profit, 
          net_profit_margin_pct
      FROM idxfin_gold.financial_summary
      WHERE issuer_id = 'NCKL' AND fiscal_year IN (2024, 2025)
      ORDER BY fiscal_year ASC;

  # Example 2: Banking Loan & Deposit Analysis
  - question: "Show total loans, customer deposits, and LDR for BBCA in 2025."
    sql: |
      SELECT 
          issuer_id, 
          fiscal_year, 
          currency,
          bank_loans, 
          bank_deposits, 
          ROUND((bank_loans / NULLIF(bank_deposits, 0)) * 100, 2) AS ldr_pct
      FROM idxfin_gold.financial_summary
      WHERE issuer_id = 'BBCA' AND fiscal_year = 2025;

  # Example 3: Sharia Balance Sheet Identity Check
  - question: "Verify if BBCA's balance sheet balances with Temporary Syirkah Funds in 2025."
    sql: |
      SELECT 
          issuer_id,
          fiscal_year,
          total_assets,
          total_liabilities,
          total_equity,
          COALESCE(temporary_syirkah_funds, 0) AS syirkah_funds,
          total_assets - (total_liabilities + total_equity + COALESCE(temporary_syirkah_funds, 0)) AS bs_diff
      FROM idxfin_gold.financial_summary
      WHERE issuer_id = 'BBCA' AND fiscal_year = 2025;

  # Example 4: Stock Price Reaction Since Financial Period End
  - question: "Which companies had the highest stock price gain since their 2025 financial period end?"
    sql: |
      SELECT 
          issuer_id, 
          fiscal_year, 
          period_close_price, 
          latest_close_price, 
          price_change_since_period_pct
      FROM idxfin_gold.market_valuation
      WHERE fiscal_year = 2025 AND price_change_since_period_pct IS NOT NULL
      ORDER BY price_change_since_period_pct DESC
      LIMIT 10;

  # Example 5: Cross-Asset Gold Commodity vs Miner Revenue
  - question: "Compare monthly COMEX Gold average prices in 2025 with gold miner revenue."
    sql: |
      SELECT 
          month_date, 
          avg_close AS gold_avg_usd_oz, 
          monthly_return_pct
      FROM idxfin_gold.commodity_trend_monthly
      WHERE symbol = 'GC=F' AND EXTRACT(year FROM month_date) = 2025
      ORDER BY month_date ASC;

  # Example 6: Corporate Governance & Independent Commissioners
  - question: "Siapa saja Komisaris Independen di PT Vale atau MAPI?"
    sql: |
      SELECT 
          issuer_id, 
          person_name, 
          role, 
          source_page
      FROM idxfin_silver.officer_position
      WHERE is_independent = true AND issuer_id = 'MAPI'
      ORDER BY person_name;

  # Example 7: Audit Opinion and Audit Firm
  - question: "Which audit firm audited NCKL in 2025 and what was the opinion?"
    sql: |
      SELECT 
          issuer_id, 
          fiscal_year, 
          audit_firm, 
          network, 
          signing_partner, 
          opinion, 
          source_page
      FROM idxfin_silver.audit_report
      WHERE issuer_id = 'NCKL' AND fiscal_year = 2025;

  # Example 8: Provenance Citation Verification
  - question: "What was MAPI's total assets in 2025 and what PDF page proves it?"
    sql: |
      SELECT 
          s.issuer_id, 
          s.fiscal_year, 
          s.total_assets, 
          s.currency, 
          p.total_assets_page
      FROM idxfin_gold.financial_summary s
      JOIN idxfin_gold.financial_summary_provenance p
        ON s.issuer_id = p.issuer_id AND s.period_end = p.period_end
      WHERE s.issuer_id = 'MAPI' AND s.fiscal_year = 2025;
```

---

## 4. Execution & Verification Tutorial

### Step 1: Initialize the Wren AI Project Structure

In your terminal, create the directory structure and copy the configuration files:

```bash
cd /Users/Rayhan.Kharisma/Documents/prsnl-prjct/statements-extraction

# Create wren folders
mkdir -p wren/projects/idxfin/models
mkdir -p wren/projects/idxfin/views
mkdir -p wren/projects/idxfin/knowledge

# Verify existing DuckDB database exists and is populated
python3 -c "import duckdb; c = duckdb.connect('data/idxfin.duckdb', read_only=True); print('Facts count:', c.execute('SELECT COUNT(*) FROM idxfin_silver.fact').fetchone()[0])"
# Expected output: Facts count: 3325
```

---

### Step 2: Configure Environment Variables

Create `wren/.env.wren` (or ensure root `.env` includes necessary keys):

```bash
cat << 'EOF' > wren/.env.wren
# OpenAI API configuration (matching root .env)
OPENAI_API_KEY=your-openai-api-key-here
LLM_PROVIDER=openai
DEFAULT_MODEL=gpt-5.6-luna
FALLBACK_MODEL=gpt-5.4-mini

# Internal Networking Ports
WREN_UI_PORT=3000
WREN_AI_SERVICE_PORT=5555
WREN_ENGINE_PORT=8080
QDRANT_PORT=6333
EOF
```

---

### Step 3: Launch the Wren AI Microservice Stack

Launch the Wren AI containers using Docker Compose:

```bash
docker compose -f wren/docker-compose.wren.yml --env-file wren/.env.wren up -d
```

Check the health of all running services:

```bash
docker compose -f wren/docker-compose.wren.yml ps
```

Verify service logs:
```bash
# Verify Wren Engine started and opened DuckDB
docker logs wren-engine --tail 20

# Verify Wren AI Service connected to Qdrant
docker logs wren-ai-service --tail 20
```

---

### Step 4: Open the Wren AI UI & Validate Data Connection

1. Open your browser and navigate to **`http://localhost:3000`**.
2. If prompted to connect a data source:
   - Select **DuckDB**.
   - Set Database Path: `/app/data/idxfin.duckdb` (the path inside the container).
   - Set Initial SQL: `PRAGMA access_mode='read_only';`
3. Click **Test Connection**. Wren AI will connect in read-only mode and discover the schemas:
   - `idxfin_gold`
   - `idxfin_silver`
   - `idxfin_market`
   - `idxfin_ref`

---

### Step 5: Index Semantic Metadata into Qdrant

In the Wren AI UI or via the CLI:
1. Navigate to **Modeling**.
2. Confirm the imported models: `financial_summary`, `market_valuation`, `commodity_trend_monthly`, `officer_position`, and `audit_report`.
3. Click **Deploy / Sync to Vector Store**.
4. The `wren-ai-service` will generate vector embeddings for all table descriptions, column definitions, and business formulas, storing them in Qdrant.

---

### Step 6: Test 10 Benchmark Natural Language Queries

Open the **Chat** interface in Wren UI (`http://localhost:3000/chat`) and test the agent across 10 multi-industry scenarios:

```
┌────┬────────────────────────────────────────────────────────┬────────────────────────────────────────┐
│ #  │ Natural Language User Prompt                           │ Verification Expectation               │
├────┼────────────────────────────────────────────────────────┼────────────────────────────────────────┤
│ 1  │ "Berapa pendapatan dan laba bersih NCKL tahun 2025?"   │ Revenue: IDR 29.633T, Net: IDR 10.970T │
├────┼────────────────────────────────────────────────────────┼────────────────────────────────────────┤
│ 2  │ "Which bank has higher deposits, BBCA or BBSI?"        │ BBCA (~IDR 1,180T) vs BBSI (~IDR 8.7T) │
├────┼────────────────────────────────────────────────────────┼────────────────────────────────────────┤
│ 3  │ "Does BBCA's balance sheet identity balance in 2025?"  │ Correctly adds IDR 10.63T Syirkah funds│
├────┼────────────────────────────────────────────────────────┼────────────────────────────────────────┤
│ 4  │ "Compare revenue growth of ADRO vs BREN (USD issuers)" │ Isolates USD companies; avoids IDR mix │
├────┼────────────────────────────────────────────────────────┼────────────────────────────────────────┤
│ 5  │ "Show the stock price change of ADRO since 2025-12-31" │ Returns +48.07% (from 1810 to 2680 IDR)│
├────┼────────────────────────────────────────────────────────┼────────────────────────────────────────┤
│ 6  │ "What was the average COMEX Gold price in Dec 2025?"   │ Queries commodity_trend_monthly (GC=F) │
├────┼────────────────────────────────────────────────────────┼────────────────────────────────────────┤
│ 7  │ "Who are the independent commissioners of MAPI?"       │ Queries officer_position (MAPI)        │
├────┼────────────────────────────────────────────────────────┼────────────────────────────────────────┤
│ 8  │ "Who audited NCKL in 2025 and did they give a clean    │ Returns KAP and Unqualified (WTP)      │
│    │  opinion?"                                             │                                        │
├────┼────────────────────────────────────────────────────────┼────────────────────────────────────────┤
│ 9  │ "Show MAPI total assets with source PDF page citation" │ Returns IDR 32.824T with page citation │
├────┼────────────────────────────────────────────────────────┼────────────────────────────────────────┤
│ 10 │ "Calculate Debt to Equity ratio for LSIP and CASS"     │ Uses calculated_field debt_to_equity   │
└────┴────────────────────────────────────────────────────────┴────────────────────────────────────────┘
```

---

## 5. Automated Golden Test Suite for CI/CD

To ensure that changes to the semantic model or extraction pipeline never break natural language analytics, we specify an automated integration test script.

#### `tests/test_wren_semantic_contract.py`

```python
"""
Automated Semantic Contract Verification for Wren AI & DuckDB Gold Layer.
Verifies that all views and columns mapped in MDL exist, have proper types,
and answer fundamental queries with zero null pointer or lock exceptions.
"""
import unittest
from pathlib import Path
import duckdb


class TestWrenSemanticContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db_path = Path("data/idxfin.duckdb")
        cls.conn = duckdb.connect(str(db_path), read_only=True)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def test_financial_summary_schema_and_issuers(self):
        """Verify all 14 benchmark issuers are present in financial_summary."""
        issuers = self.conn.execute("""
            SELECT DISTINCT issuer_id 
            FROM idxfin_gold.financial_summary 
            WHERE fiscal_year = 2025
        """).fetchall()
        issuer_set = {r[0] for r in issuers}
        expected = {"BBCA", "NCKL", "ADRO", "MAPI", "ABMM", "ATIC", "LSIP", 
                    "BREN", "ARCI", "CASS", "BBSI", "DMAS", "BISI", "IPCM"}
        self.assertTrue(expected.issubset(issuer_set), f"Missing issuers: {expected - issuer_set}")

    def test_sharia_balance_sheet_identity(self):
        """Verify BBCA sharia temporary syirkah funds balance sheet identity."""
        diff = self.conn.execute("""
            SELECT total_assets - (total_liabilities + total_equity + COALESCE(temporary_syirkah_funds, 0)) AS diff
            FROM idxfin_gold.financial_summary
            WHERE issuer_id = 'BBCA' AND fiscal_year = 2025
        """).fetchone()[0]
        self.assertEqual(diff, 0, "BBCA balance sheet discrepancy detected!")

    def test_market_valuation_linkage(self):
        """Verify market_valuation correctly links stock prices with fundamentals."""
        row = self.conn.execute("""
            SELECT period_close_price, latest_close_price, price_change_since_period_pct
            FROM idxfin_gold.market_valuation
            WHERE issuer_id = 'ADRO' AND fiscal_year = 2025
        """).fetchone()
        self.assertIsNotNone(row)
        self.assertGreater(row[0], 0)
        self.assertGreater(row[1], 0)
        self.assertIsNotNone(row[2])

    def test_commodity_trend_monthly_coverage(self):
        """Verify 6 commodity benchmarks & proxies exist in monthly trend view."""
        symbols = self.conn.execute("""
            SELECT DISTINCT symbol 
            FROM idxfin_gold.commodity_trend_monthly
        """).fetchall()
        symbol_set = {r[0] for r in symbols}
        expected_commodities = {"GC=F", "CL=F", "BZ=F", "ESSA.JK", "CF", "NG=F"}
        self.assertTrue(expected_commodities.issubset(symbol_set))

    def test_governance_and_audit_linkage(self):
        """Verify governance officers and audit reports exist for NCKL."""
        officer_count = self.conn.execute("""
            SELECT COUNT(*) FROM idxfin_silver.officer_position WHERE issuer_id = 'NCKL'
        """).fetchone()[0]
        self.assertGreaterEqual(officer_count, 1)

        audit_row = self.conn.execute("""
            SELECT audit_firm, opinion FROM idxfin_silver.audit_report WHERE issuer_id = 'NCKL' AND fiscal_year = 2025
        """).fetchone()
        self.assertIsNotNone(audit_row)


if __name__ == "__main__":
    unittest.main()
```

---

## 6. Production Operations & Best Practices

### 6.1 Lock-Free Refresh Workflow
When new filings are processed by Celery workers into `data/idxfin.duckdb`:
1. Celery `worker-duckdb-writer` acquires the write lock, ingests Parquet files, updates Gold views, and releases the lock in **< 50 milliseconds**.
2. Wren AI reads DuckDB with `PRAGMA access_mode='read_only'`. Because DuckDB reads are non-blocking snapshots, users querying the chatbot never experience lock timeouts or transaction conflicts.
3. If new columns or custom concepts are added, trigger Wren AI's schema re-indexing endpoint:
   ```bash
   curl -X POST http://localhost:5555/api/v1/mdl/sync
   ```

### 6.2 Security & Data Governance
1. **Network Isolation**: The Wren AI stack runs within `idxfin-network`. Only `wren-ui` (port 3000) should be exposed externally behind an enterprise reverse proxy (Nginx / Cloudflare Access with SSO/OAuth2).
2. **Read-Only Enforcement**: The DuckDB database file is mounted with Linux `:ro` mode. Even if an injection attack were attempted through generated SQL, the underlying filesystem will reject write operations at the OS kernel level.
3. **Auditability**: Every generated query and execution plan is logged by `wren-engine`.

---

## 7. Implementation Roadmap & Milestones

```
┌──────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                 WREN AI IMPLEMENTATION ROADMAP                                   │
├─────────┬──────────────────────────────────┬─────────────────────────────────────────────────────┤
│ Stage   │ Milestone                        │ Deliverables                                        │
├─────────┼──────────────────────────────────┼─────────────────────────────────────────────────────┤
│ Phase 1 │ Semantic Modeling (MDL)          │ • Create wren_project.yml, relationships.yml        │
│         │                                  │ • Write YAML models for Gold & Silver tables        │
│         │                                  │ • Codify 33 PSAK accounting rules in knowledge/     │
├─────────┼──────────────────────────────────┼─────────────────────────────────────────────────────┤
│ Phase 2 │ Docker & Service Orchestration   │ • Stand up docker-compose.wren.yml                  │
│         │                                  │ • Configure LiteLLM for OpenAI (gpt-5.6-luna / mini)│
│         │                                  │ • Verify Qdrant vector database health              │
├─────────┼──────────────────────────────────┼─────────────────────────────────────────────────────┤
│ Phase 3 │ Read-Only DuckDB Integration     │ • Mount data/idxfin.duckdb in read-only mode        │
│         │                                  │ • Verify zero-concurrency collisions with Celery    │
│         │                                  │ • Test query planning latency (< 100ms)             │
├─────────┼──────────────────────────────────┼─────────────────────────────────────────────────────┤
│ Phase 4 │ Benchmark Q&A & Evaluation       │ • Deploy semantic models to Qdrant vector store     │
│         │                                  │ • Execute 10 benchmark queries in Wren UI           │
│         │                                  │ • Validate automated test suite (test_wren_semantic)│
├─────────┼──────────────────────────────────┼─────────────────────────────────────────────────────┤
│ Phase 5 │ Enterprise Rollout & Dashboards  │ • Configure executive KPI dashboards in Wren UI     │
│         │                                  │ • Connect Slack / Teams webhook notifications       │
│         │                                  │ • Document user guide for financial analysts        │
└─────────┴──────────────────────────────────┴─────────────────────────────────────────────────────┘
```

---

## 8. Conclusion

By implementing **Wren AI** on top of our Medallion DuckDB Lakehouse, we establish an enterprise-grade, conversational intelligence layer over Indonesian capital market data. 

Through **Modeling Definition Language (MDL)**, we codify complex Indonesian accounting rules (PSAK 101 Sharia banking, direct cash flow conventions, and multi-currency reporting) into a governed semantic contract. This eliminates LLM SQL hallucinations, guarantees verbatim provenance back to source PDF pages, and unlocks instantaneous, conversational financial analytics for analysts and decision-makers alike.
