-- =====================================================================================
-- storage_ddl.sql — IDX filing extraction store (Delta Lake / Databricks SQL)
-- Portable to DuckDB/Postgres: MAP->JSON, STRUCT->JSON/composite, CLUSTER BY->indexes.
--
-- Principles
--   * Industry variance lives in ref.concept / ref.template_concept rows, never in columns.
--   * Every silver row carries `lineage` (doc, page, bbox, extractor, model, run, confidence).
--   * Knowledge time (document.published_at) is separate from the valid period -> PIT views.
--   * dims are MAP for flexibility + dim_key STRING for grouping (Spark cannot GROUP BY a MAP).
-- If on Unity Catalog, prefix schemas with your catalog.
-- fact_latest / fact_first_reported use Databricks `SELECT * EXCEPT`; on OSS Spark list columns explicitly.
-- =====================================================================================

CREATE SCHEMA IF NOT EXISTS idxfin_ref;
CREATE SCHEMA IF NOT EXISTS idxfin_bronze;
CREATE SCHEMA IF NOT EXISTS idxfin_silver;
CREATE SCHEMA IF NOT EXISTS idxfin_gold;

-- -------------------------------------------------------------------------------------
-- REFERENCE (seeded from taxonomy_seed.yaml; versioned, append-mostly)
-- -------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS idxfin_ref.issuer (
  issuer_id              STRING NOT NULL,   -- stable internal id; ticker can change
  ticker                 STRING NOT NULL,
  legal_name             STRING,
  idx_ic_sector          STRING,
  idx_ic_subindustry     STRING,
  listing_date           DATE,
  delisting_date         DATE,
  presentation_currency  STRING,
  functional_currency    STRING,
  fiscal_year_end_mmdd   STRING             -- '12-31' for nearly all IDX issuers
);

CREATE TABLE IF NOT EXISTS idxfin_ref.issuer_template (   -- SCD2: issuers restructure
  issuer_id     STRING NOT NULL,
  template_id   STRING NOT NULL,            -- GENERAL | PROPERTY | INFRASTRUCTURE | BANK | FINANCING | SECURITIES | INSURANCE | INSURANCE_P117
  is_sharia     BOOLEAN NOT NULL,           -- overlay: adds temporary syirkah funds, sharia contract axes
  bs_format     STRING,                     -- current_noncurrent | liquidity
  valid_from    DATE NOT NULL,
  valid_to      DATE
);

CREATE TABLE IF NOT EXISTS idxfin_ref.issuer_kpi_pack (   -- packs attach to SEGMENTS; conglomerates hold several
  issuer_id       STRING NOT NULL,
  pack_id         STRING NOT NULL,          -- core | coal | nickel | pharma | technology_platform | retail | ...
  segment_member  STRING,                   -- axis_member of segment_business; NULL = whole issuer
  valid_from      DATE NOT NULL,
  valid_to        DATE,
  assigned_by     STRING                    -- rule (IDX-IC + segment note) | human
);

CREATE TABLE IF NOT EXISTS idxfin_ref.concept_broader (    -- specialized KPI -> industry-agnostic core KPI
  concept_id         STRING NOT NULL,
  broader_concept_id STRING NOT NULL
);

CREATE TABLE IF NOT EXISTS idxfin_ref.accounting_regime (
  regime_id      STRING NOT NULL,           -- PRE_2020 | PSAK71_72_73 | PSAK_RENUMBERED | PSAK117
  effective_from DATE,
  effective_to   DATE,
  templates      ARRAY<STRING>,             -- NULL = all
  is_series_break BOOLEAN,                  -- measurement change (TRUE) vs reference-only change (FALSE)
  description    STRING
);

CREATE TABLE IF NOT EXISTS idxfin_ref.concept (
  concept_id       STRING NOT NULL,         -- fs.bs.cash_and_equivalents | note.ppe.gross_carrying | op.nickel.ore_grade | ext.XXXX.slug
  domain           STRING NOT NULL,         -- fs | note | op | reg | esg | hr | nongaap | ext
  statement        STRING,                  -- balance_sheet | income_statement | comprehensive_income | changes_in_equity | cash_flow | NULL
  label_en         STRING,
  label_id         STRING,
  data_type        STRING NOT NULL,         -- monetary | shares | per_share | ratio | quantity | count | text | date
  unit_dimension   STRING,                  -- currency | mass | mass_wet | volume | energy | area | pure | price_per_mass ...
  period_type      STRING NOT NULL,         -- instant | duration
  period_aggregation STRING NOT NULL,       -- additive | non_additive (EPS, ratios, prices, utilization, weighted shares)
  balance          STRING,                  -- debit | credit | NULL
  allowed_axes     ARRAY<STRING>,
  required_qualifiers ARRAY<STRING>,        -- e.g. [basis, moisture_basis] for nickel ore
  is_company_defined BOOLEAN DEFAULT FALSE,
  extension_issuer_id STRING,               -- non-null for ext.* concepts
  xbrl_element     STRING,                  -- IDX taxonomy element when one exists
  standard_refs    ARRAY<STRING>,           -- ['PSAK 73','PSAK 116']
  definition       STRING,
  deprecated_at    DATE
) TBLPROPERTIES ('delta.feature.allowColumnDefaults' = 'supported');

CREATE TABLE IF NOT EXISTS idxfin_ref.template_concept (
  template_id   STRING NOT NULL,
  regime_id     STRING,                     -- NULL = all regimes
  concept_id    STRING NOT NULL,
  requirement   STRING NOT NULL,            -- required | expected | optional
  expected_in   ARRAY<STRING>,              -- doc types where `requirement` applies, e.g. ['fs_annual'] for full rollforwards;
                                            -- condensed interim notes must not count as extraction misses
  display_order INT
);

CREATE TABLE IF NOT EXISTS idxfin_ref.axis (
  axis_id           STRING NOT NULL,
  label             STRING,
  member_source     STRING NOT NULL         -- standard | issuer_extensible | fk_counterparty | fk_instrument | fk_facility | iso4217
);

CREATE TABLE IF NOT EXISTS idxfin_ref.axis_member (
  axis_id        STRING NOT NULL,
  member_id      STRING NOT NULL,
  issuer_id      STRING,                    -- NULL = standard member; set for issuer-defined segments/products
  label_en       STRING,
  label_id       STRING,
  std_member_id  STRING                     -- issuer member -> standard member (e.g. 'Segmen Nikel' -> nickel)
);

CREATE TABLE IF NOT EXISTS idxfin_ref.unit (
  unit_id    STRING NOT NULL,
  dimension  STRING NOT NULL,
  to_base    DECIMAL(38,12),                -- NULL = not auto-convertible
  base_unit  STRING,
  note       STRING
);

CREATE TABLE IF NOT EXISTS idxfin_ref.label_mapping (      -- learned mapping memory; cuts LLM calls over time
  label_norm     STRING NOT NULL,           -- lowercased, whitespace/punct-normalized as-reported label
  lang           STRING,
  template_id    STRING,
  issuer_id      STRING,                    -- NULL = global mapping
  context_hint   STRING,                    -- statement / note topic / parent label
  concept_id     STRING NOT NULL,
  method         STRING,                    -- exact | fuzzy | embedding | llm | human
  confidence     DOUBLE,
  approved_by    STRING,
  valid_from     TIMESTAMP
);

CREATE TABLE IF NOT EXISTS idxfin_ref.metric_definition (
  metric_id    STRING NOT NULL,
  version      INT NOT NULL,
  formula      STRING NOT NULL,             -- DSL over concept ids, see taxonomy_seed.yaml
  templates    ARRAY<STRING>,
  unit_dimension STRING,
  description  STRING,
  valid_from   DATE
);

CREATE TABLE IF NOT EXISTS idxfin_ref.validation_rule (
  rule_id      STRING NOT NULL,
  severity     STRING NOT NULL,             -- hard | soft
  templates    ARRAY<STRING>,
  expression   STRING NOT NULL,
  tolerance    STRING,                      -- rounding | pct:0.02 | abs:<n>
  description  STRING
);

-- -------------------------------------------------------------------------------------
-- BRONZE (documents and parse artifacts; immutable)
-- -------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS idxfin_bronze.document (
  doc_id          STRING NOT NULL,          -- sha256 of file bytes
  issuer_id       STRING NOT NULL,
  doc_type        STRING NOT NULL,          -- see DocType enum
  fiscal_year     INT,
  fiscal_period   STRING,                   -- FY | Q1 | H1 | 9M
  period_end      DATE,
  published_at    TIMESTAMP NOT NULL,       -- KNOWLEDGE TIME: when the market could see it
  source_url      STRING,
  storage_path    STRING,
  language        STRING,                   -- id | en | bilingual
  number_locale   STRING,                   -- id | en
  page_count      INT,
  has_text_layer  BOOLEAN,
  xbrl_available  BOOLEAN,
  regime_id       STRING,
  assurance       STRING,                   -- audited | reviewed | unaudited | management_narrative
  supersedes_doc_id STRING,                 -- re-issued / revised filings
  parser_version  STRING,
  ingested_at     TIMESTAMP
) CLUSTER BY (issuer_id, fiscal_year);

CREATE TABLE IF NOT EXISTS idxfin_bronze.page (
  doc_id     STRING NOT NULL,
  page_no    INT NOT NULL,
  text       STRING,
  ocr_used   BOOLEAN,
  layout     STRING,                        -- JSON blocks with bbox from layout model
  section_type STRING                       -- sectionizer output, e.g. lk.statement, ar.mdna.kpis
) CLUSTER BY (doc_id);

CREATE TABLE IF NOT EXISTS idxfin_bronze.parsed_table (
  doc_id       STRING NOT NULL,
  table_id     STRING NOT NULL,
  page_from    INT,
  page_to      INT,                         -- tables spanning pages are stitched here
  section_path STRING,
  header_rows  ARRAY<ARRAY<STRING>>,
  grid         ARRAY<ARRAY<STRING>>,
  cell_bboxes  STRING,                      -- JSON
  parser       STRING
) CLUSTER BY (doc_id);

CREATE TABLE IF NOT EXISTS idxfin_bronze.text_chunk (   -- retrieval + re-extraction without re-parsing
  chunk_id     STRING NOT NULL,
  doc_id       STRING NOT NULL,
  section_path STRING,
  section_type STRING,
  page_from    INT,
  page_to      INT,
  language     STRING,
  text         STRING,
  embedding    ARRAY<FLOAT>
) CLUSTER BY (doc_id);

-- -------------------------------------------------------------------------------------
-- SILVER — FACTS (one table for statements, notes, KPIs, ESG, HR, regulatory ratios)
-- -------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS idxfin_silver.fact (
  fact_id            STRING NOT NULL,       -- sha2(doc_id|label_or_concept|period|dim_key|scenario|col coords)
  issuer_id          STRING NOT NULL,
  reporting_entity   STRING,                -- NULL = issuer group; else subsidiary/segment entity name id
  doc_id             STRING NOT NULL,
  -- what
  concept_id         STRING,                -- NULL until mapped
  label_raw          STRING,
  label_raw_alt      STRING,
  note_ref           STRING,
  mapping            STRUCT<method: STRING, confidence: DOUBLE, mapped_at: TIMESTAMP, approved: BOOLEAN>,
  -- when (valid time)
  period_type        STRING NOT NULL,
  period_start       DATE,
  period_end         DATE NOT NULL,
  fiscal_year        INT,
  fiscal_period      STRING,                -- FY | Q1 | H1 | 9M (cumulative) | Q2 | Q3 | Q4 (discrete) | OTHER
  duration_months    INT,
  is_cumulative_ytd  BOOLEAN,
  -- context
  scenario           STRING NOT NULL,       -- actual | guidance | target | budget | sensitivity
  dims               MAP<STRING, STRING>,
  dim_key            STRING NOT NULL,       -- 'axis=member|axis=member' sorted; '' if none
  basis              STRING,                -- gross_100pct | attributable | not_stated (KPIs)
  consolidation_scope STRING,
  -- value
  value              DECIMAL(38, 6),        -- parsed x scale, natural balance sign
  value_text         STRING,                -- text-valued facts
  raw_text           STRING NOT NULL,
  unit_id            STRING,
  currency           STRING,
  scale_as_reported  BIGINT,
  -- trust
  assurance          STRING NOT NULL,
  is_restated_column BOOLEAN,
  is_comparative     BOOLEAN,               -- period_end < document.period_end
  review_status      STRING NOT NULL,       -- auto | needs_review | verified | rejected
  knowledge_ts       TIMESTAMP NOT NULL,    -- = document.published_at
  ingested_at        TIMESTAMP,
  lineage            STRUCT<page: INT, bbox: ARRAY<DOUBLE>, table_id: STRING, row_idx: INT, col_idx: INT,
                            section_path: STRING, quote: STRING, extractor: STRING, extractor_version: STRING,
                            method: STRING, model: STRING, prompt_version: STRING, run_id: STRING, confidence: DOUBLE>
) CLUSTER BY (issuer_id, concept_id, period_end);

-- -------------------------------------------------------------------------------------
-- SILVER — RECORDS (objects with many attributes; amounts live in fact via dims)
-- -------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS idxfin_silver.counterparty (
  counterparty_id  STRING NOT NULL,
  canonical_name   STRING NOT NULL,
  kind             STRING,                  -- bank | supplier | customer | contractor | government | ...
  country          STRING,
  is_listed_ticker STRING,                  -- links to ref.issuer when the counterparty is itself an emiten
  resolved_by      STRING                   -- rule | embedding | human
);

CREATE TABLE IF NOT EXISTS idxfin_silver.counterparty_alias (
  counterparty_id STRING NOT NULL,
  alias_raw       STRING NOT NULL,          -- 'PT Bank Mandiri (Persero) Tbk', 'Bank Mandiri', 'Mandiri'
  alias_norm      STRING NOT NULL,
  first_seen_doc  STRING
);

CREATE TABLE IF NOT EXISTS idxfin_silver.debt_instrument (
  instrument_id    STRING NOT NULL,         -- stable across years: hash(issuer, borrower, name_norm, signing/issue date)
  issuer_id        STRING NOT NULL,
  doc_id           STRING NOT NULL,         -- snapshot source; attributes can change on amendment
  as_of            DATE NOT NULL,
  instrument_name_raw STRING,
  instrument_type  STRING,
  borrower_entity  STRING,
  lender_ids       ARRAY<STRING>,
  trustee_id       STRING,
  currency         STRING,
  facility_limit   DECIMAL(38, 6),
  rate_type        STRING,
  rate_raw         STRING,
  benchmark        STRING,
  spread_bps       INT,
  coupon_pct       DECIMAL(9, 4),
  signing_or_issue_date DATE,
  maturity_date    DATE,
  secured          BOOLEAN,
  collateral_raw   STRING,
  guarantor_raw    STRING,
  rating_raw       STRING,
  lineage          STRUCT<page: INT, section_path: STRING, extractor: STRING, run_id: STRING, confidence: DOUBLE>
);

CREATE TABLE IF NOT EXISTS idxfin_silver.debt_covenant (
  instrument_id    STRING NOT NULL,
  doc_id           STRING NOT NULL,
  as_of            DATE NOT NULL,
  metric_code      STRING,
  metric_raw       STRING,
  operator         STRING,
  threshold        DECIMAL(18, 6),
  test_frequency   STRING,
  issuer_states_compliance BOOLEAN,
  headroom         DECIMAL(18, 6)           -- computed in gold from metric_value, stored for alerting
);

CREATE TABLE IF NOT EXISTS idxfin_silver.derivative_contract (
  derivative_id STRING NOT NULL, issuer_id STRING NOT NULL, doc_id STRING NOT NULL, as_of DATE NOT NULL,
  hedged_risk STRING, instrument STRING, counterparty_id STRING, underlying_raw STRING,
  notional DECIMAL(38, 6), notional_currency_or_unit STRING, maturity_date DATE, hedge_designation STRING
);

CREATE TABLE IF NOT EXISTS idxfin_silver.commitment_contingency (
  item_id STRING NOT NULL, issuer_id STRING NOT NULL, doc_id STRING NOT NULL, as_of DATE NOT NULL,
  kind STRING, counterparty_id STRING, description STRING, amount DECIMAL(38, 6), currency STRING,
  status_raw STRING, likelihood STRING, provision_recognized BOOLEAN,
  first_seen_doc STRING                     -- enables "new litigation this year" diffs
);

CREATE TABLE IF NOT EXISTS idxfin_silver.facility (
  facility_id STRING NOT NULL, issuer_id STRING NOT NULL, doc_id STRING NOT NULL, as_of DATE NOT NULL,
  name_raw STRING, facility_type STRING, owning_entity STRING, status STRING,
  province STRING, regency STRING, latitude DOUBLE, longitude DOUBLE,
  license_type STRING, license_expiry DATE, area_ha DECIMAL(18, 4),
  primary_products ARRAY<STRING>, nameplate_capacity DECIMAL(38, 6), capacity_unit_id STRING,
  commercial_operation_date DATE
);

CREATE TABLE IF NOT EXISTS idxfin_silver.group_structure (
  issuer_id STRING NOT NULL, doc_id STRING NOT NULL, as_of DATE NOT NULL,
  parent_entity STRING, child_entity STRING NOT NULL, relation STRING,   -- subsidiary | associate | joint_venture | joint_operation
  domicile STRING, principal_activity STRING, effective_ownership_pct DECIMAL(9, 4), commercial_operation_year INT
);

CREATE TABLE IF NOT EXISTS idxfin_silver.shareholder_position (
  issuer_id STRING NOT NULL, doc_id STRING NOT NULL, as_of DATE NOT NULL,
  holder_counterparty_id STRING, holder_name_raw STRING, holder_type STRING, share_class STRING,
  shares DECIMAL(38, 0), ownership_pct DECIMAL(9, 4)
);

CREATE TABLE IF NOT EXISTS idxfin_silver.officer_position (
  issuer_id STRING NOT NULL, doc_id STRING NOT NULL, as_of DATE NOT NULL,
  person_name STRING, role STRING, appointed_date DATE, term_end DATE, is_independent BOOLEAN,
  concurrent_positions_raw STRING
);

CREATE TABLE IF NOT EXISTS idxfin_silver.supply_chain_link (
  issuer_id STRING NOT NULL, doc_id STRING NOT NULL, as_of DATE NOT NULL,
  counterparty_id STRING NOT NULL, relation STRING, item_raw STRING,
  share_pct DECIMAL(9, 4), share_basis STRING, from_major_counterparty_note BOOLEAN,
  contract_term_raw STRING, origin_or_destination_country STRING, assurance STRING
);

CREATE TABLE IF NOT EXISTS idxfin_silver.license_agreement (
  agreement_id STRING NOT NULL,              -- hash(issuer, counterparty, type, brands, start_date)
  issuer_id STRING NOT NULL, doc_id STRING NOT NULL, as_of DATE NOT NULL,
  agreement_type STRING, direction STRING, counterparty_id STRING,
  brands_or_products ARRAY<STRING>, territory_raw STRING, exclusive BOOLEAN,
  start_date DATE, expiry_date DATE, renewal_terms_raw STRING,
  royalty_basis_raw STRING, minimum_commitment_raw STRING
);                                           -- expiry_date drives a license-renewal risk calendar in gold

CREATE TABLE IF NOT EXISTS idxfin_silver.subsequent_event (
  issuer_id STRING NOT NULL, doc_id STRING NOT NULL, event_date DATE,
  category STRING, description STRING, amount DECIMAL(38, 6), currency STRING
);

CREATE TABLE IF NOT EXISTS idxfin_silver.audit_report (
  doc_id STRING NOT NULL, issuer_id STRING NOT NULL, fiscal_year INT,
  audit_firm STRING, network STRING, signing_partner STRING, report_date DATE,
  opinion STRING, emphasis_of_matter BOOLEAN, material_uncertainty_going_concern BOOLEAN,
  key_audit_matters ARRAY<STRING>
);

-- -------------------------------------------------------------------------------------
-- SILVER — ASSERTIONS (claims with evidence)
-- -------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS idxfin_silver.forward_guidance (
  guidance_id STRING NOT NULL, issuer_id STRING NOT NULL, doc_id STRING NOT NULL,
  stated_at TIMESTAMP NOT NULL,             -- = document.published_at
  concept_id STRING, metric_label_raw STRING, scenario STRING,
  value_point DECIMAL(38, 6), value_low DECIMAL(38, 6), value_high DECIMAL(38, 6), unit_id STRING,
  target_fiscal_year INT, dims MAP<STRING, STRING>, dim_key STRING,
  conditions_raw STRING, funding_source_raw STRING,
  lineage STRUCT<page: INT, quote: STRING, extractor: STRING, run_id: STRING, confidence: DOUBLE>
);

CREATE TABLE IF NOT EXISTS idxfin_silver.driver_attribution (
  issuer_id STRING NOT NULL, doc_id STRING NOT NULL, concept_id STRING, metric_label_raw STRING,
  period_start DATE, period_end DATE, direction STRING, change_raw STRING,
  driver STRING, driver_summary STRING, contribution_raw STRING,
  lineage STRUCT<page: INT, quote: STRING, extractor: STRING, run_id: STRING, confidence: DOUBLE>
);

CREATE TABLE IF NOT EXISTS idxfin_silver.risk_factor (
  issuer_id STRING NOT NULL, doc_id STRING NOT NULL, fiscal_year INT,
  category STRING, title_raw STRING, title_norm STRING, summary STRING, mitigation_summary STRING,
  is_quantified BOOLEAN, first_seen_fiscal_year INT,   -- risk diffing across years
  embedding ARRAY<FLOAT>
);

CREATE TABLE IF NOT EXISTS idxfin_silver.accounting_policy (
  issuer_id STRING NOT NULL, doc_id STRING NOT NULL, fiscal_year INT,
  topic STRING, choice_code STRING, choice_raw STRING,
  useful_life_min_years DOUBLE, useful_life_max_years DOUBLE,
  changed_this_period BOOLEAN, change_impact_raw STRING
);

-- -------------------------------------------------------------------------------------
-- SILVER — QUALITY
-- -------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS idxfin_silver.validation_result (
  run_id STRING NOT NULL, doc_id STRING NOT NULL, rule_id STRING NOT NULL,
  period_end DATE, dim_key STRING, passed BOOLEAN,
  lhs DECIMAL(38, 6), rhs DECIMAL(38, 6), diff DECIMAL(38, 6), tolerance DECIMAL(38, 6),
  implicated_fact_ids ARRAY<STRING>, checked_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS idxfin_silver.review_task (
  task_id STRING NOT NULL, doc_id STRING NOT NULL, target_table STRING, target_ids ARRAY<STRING>,
  reason STRING,                            -- rule_failed:<id> | low_confidence | unmapped_label | grounding_failed
  priority DOUBLE,                          -- e.g. materiality x (1 - confidence)
  status STRING, assignee STRING, resolution STRING, created_at TIMESTAMP, resolved_at TIMESTAMP
);

-- -------------------------------------------------------------------------------------
-- GOLD VIEWS
-- -------------------------------------------------------------------------------------

-- Latest best value: highest assurance first, then most recent knowledge (captures restatements).
CREATE OR REPLACE VIEW idxfin_gold.fact_latest AS
SELECT * EXCEPT (rn) FROM (
  SELECT f.*, ROW_NUMBER() OVER (
           PARTITION BY issuer_id, concept_id, period_type, period_start, period_end, dim_key, scenario, consolidation_scope
           ORDER BY CASE assurance WHEN 'audited' THEN 0 WHEN 'reviewed' THEN 1 WHEN 'unaudited' THEN 2 ELSE 3 END,
                    knowledge_ts DESC, lineage.confidence DESC) AS rn
  FROM idxfin_silver.fact f
  WHERE concept_id IS NOT NULL AND review_status <> 'rejected'
) WHERE rn = 1;

-- As first reported: what the market saw on day one (surprise / guidance-vs-actual analysis).
CREATE OR REPLACE VIEW idxfin_gold.fact_first_reported AS
SELECT * EXCEPT (rn) FROM (
  SELECT f.*, ROW_NUMBER() OVER (
           PARTITION BY issuer_id, concept_id, period_type, period_start, period_end, dim_key, scenario, consolidation_scope
           ORDER BY knowledge_ts ASC,
                    CASE assurance WHEN 'audited' THEN 0 WHEN 'reviewed' THEN 1 WHEN 'unaudited' THEN 2 ELSE 3 END) AS rn
  FROM idxfin_silver.fact f
  WHERE concept_id IS NOT NULL AND review_status <> 'rejected'
) WHERE rn = 1;

-- Point-in-time for backtests (run as parameterized query; :as_of = rebalance timestamp).
-- SELECT * EXCEPT (rn) FROM (
--   SELECT f.*, ROW_NUMBER() OVER (PARTITION BY issuer_id, concept_id, period_type, period_start, period_end,
--                                               dim_key, scenario, consolidation_scope
--                                  ORDER BY knowledge_ts DESC) rn
--   FROM idxfin_silver.fact f
--   WHERE knowledge_ts <= :as_of AND concept_id IS NOT NULL AND review_status <> 'rejected'
-- ) WHERE rn = 1;

-- Discrete quarters. Two sources:
--   (a) derived from cumulative YTD filings: Q1 = 3M, Q2 = H1 - Q1, Q3 = 9M - H1, Q4 = FY - 9M
--   (b) reported discrete quarters (quarterly production updates, AR quarterly tables, decks)
-- Only additive concepts: never subtract EPS, ratios, prices, utilization or weighted shares.
-- Preference when both exist: statements/notes take (a) because it ties to filed YTD numbers; KPIs take (b).
-- Never emits Q2 from H1 when Q1 is missing. Assumes 31 December fiscal year end.
CREATE OR REPLACE VIEW idxfin_gold.fact_discrete_quarter AS
WITH base AS (
  SELECT f.issuer_id, f.concept_id, c.domain, f.dim_key, f.scenario, f.consolidation_scope, f.unit_id,
         f.fiscal_year, f.period_end, f.duration_months, f.is_cumulative_ytd, f.value
  FROM idxfin_gold.fact_latest f
  JOIN idxfin_ref.concept c ON c.concept_id = f.concept_id
  WHERE f.period_type = 'duration' AND c.period_aggregation = 'additive'
),
ytd AS (
  SELECT * FROM base WHERE is_cumulative_ytd AND duration_months IN (3, 6, 9, 12)
),
derived AS (
  SELECT cur.issuer_id, cur.concept_id, cur.domain, cur.dim_key, cur.scenario, cur.consolidation_scope, cur.unit_id,
         cur.fiscal_year, cur.period_end, cur.duration_months DIV 3 AS quarter_no,
         cur.value - COALESCE(prev.value, 0) AS value,
         'derived_from_ytd' AS derivation,
         cur.duration_months = 12 AS absorbs_year_end_adjustments
  FROM ytd cur
  LEFT JOIN ytd prev
    ON  prev.issuer_id = cur.issuer_id AND prev.concept_id = cur.concept_id AND prev.dim_key = cur.dim_key
    AND prev.scenario = cur.scenario AND prev.consolidation_scope <=> cur.consolidation_scope
    AND prev.unit_id <=> cur.unit_id
    AND prev.fiscal_year = cur.fiscal_year AND prev.duration_months = cur.duration_months - 3
  WHERE cur.duration_months = 3 OR prev.value IS NOT NULL
),
reported AS (
  SELECT issuer_id, concept_id, domain, dim_key, scenario, consolidation_scope, unit_id,
         fiscal_year, period_end, CAST(CEIL(MONTH(period_end) / 3.0) AS INT) AS quarter_no, value,
         'reported_discrete' AS derivation, FALSE AS absorbs_year_end_adjustments
  FROM base
  WHERE NOT is_cumulative_ytd AND duration_months = 3
),
unioned AS (
  SELECT * FROM derived UNION ALL SELECT * FROM reported
)
SELECT issuer_id, concept_id, domain, dim_key, scenario, consolidation_scope, unit_id,
       fiscal_year, period_end, quarter_no, value, derivation, absorbs_year_end_adjustments,
       n_sources > 1 AS has_alternate_source
FROM (
  SELECT u.*,
         COUNT(*) OVER (PARTITION BY issuer_id, concept_id, dim_key, scenario, consolidation_scope, unit_id,
                                     fiscal_year, quarter_no) AS n_sources,
         ROW_NUMBER() OVER (PARTITION BY issuer_id, concept_id, dim_key, scenario, consolidation_scope, unit_id,
                                         fiscal_year, quarter_no
                            ORDER BY CASE
                              WHEN domain IN ('fs', 'note') AND derivation = 'derived_from_ytd' THEN 0
                              WHEN domain NOT IN ('fs', 'note') AND derivation = 'reported_discrete' THEN 0
                              ELSE 1 END) AS rn
  FROM unioned u
) WHERE rn = 1;

-- Trailing twelve months from discrete quarters; only when the window is 4 CONSECUTIVE quarters.
CREATE OR REPLACE VIEW idxfin_gold.fact_ttm AS
SELECT issuer_id, concept_id, dim_key, scenario, consolidation_scope, unit_id, period_end, value_ttm,
       includes_year_end_adjusted_quarter
FROM (
  SELECT issuer_id, concept_id, dim_key, scenario, consolidation_scope, unit_id, period_end,
         SUM(value)      OVER w AS value_ttm,
         COUNT(*)        OVER w AS n_quarters,
         MIN(period_end) OVER w AS first_quarter_end,
         MAX(CAST(absorbs_year_end_adjustments AS INT)) OVER w = 1 AS includes_year_end_adjusted_quarter
  FROM idxfin_gold.fact_discrete_quarter
  WINDOW w AS (PARTITION BY issuer_id, concept_id, dim_key, scenario, consolidation_scope, unit_id
               ORDER BY period_end ROWS BETWEEN 3 PRECEDING AND CURRENT ROW)
) WHERE n_quarters = 4 AND months_between(period_end, first_quarter_end) = 9;

-- Example hard rule materialized as a view: balance sheet identity (incl. sharia temporary syirkah funds).
CREATE OR REPLACE VIEW idxfin_gold.chk_bs_identity AS
SELECT issuer_id, doc_id, period_end,
       MAX(CASE WHEN concept_id = 'fs.bs.total_assets'           THEN value END) AS total_assets,
       MAX(CASE WHEN concept_id = 'fs.bs.total_liabilities'      THEN value END) AS total_liabilities,
       COALESCE(MAX(CASE WHEN concept_id = 'fs.bs.sharia.temporary_syirkah_funds' THEN value END), 0) AS syirkah_funds,
       MAX(CASE WHEN concept_id = 'fs.bs.total_equity'           THEN value END) AS total_equity,
       MAX(scale_as_reported)                                                    AS scale
FROM idxfin_silver.fact
WHERE dim_key = '' AND scenario = 'actual' AND period_type = 'instant'
GROUP BY issuer_id, doc_id, period_end;
-- passed := ABS(total_assets - total_liabilities - syirkah_funds - total_equity) <= 3 * scale
