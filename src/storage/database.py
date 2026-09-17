"""
DuckDB Database Store implementing Medallion Architecture (Reference, Bronze, Silver, Gold).
Matches storage_ddl.sql portable semantics.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional
import duckdb

from filelock import FileLock, Timeout

import schema_design.extraction_contracts as contracts
from src.config import DB_LOCK_PATH, DB_PATH, TAXONOMY_PATH
from src.mapping.concept_mapper import ConceptMapper
from src.normalization.normalizer import SilverFact
from src.parser.pdf_loader import PDFDocument
from src.storage.staging import ParquetStagingStore
from src.validation.engine import ValidationRuleResult
from src.validation.repair import ReviewTask

logger = logging.getLogger(__name__)


class DatabaseStore:
    def __init__(self, db_path: str | Path = DB_PATH, read_only: bool = False, max_retries: int = 5, retry_delay: float = 1.0):
        self.db_path = Path(db_path)
        self.read_only = read_only
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = FileLock(str(DB_LOCK_PATH), timeout=60)

        # Retry connection with exponential backoff if lock contention occurs
        import time
        last_err = None
        for attempt in range(max_retries):
            try:
                if not read_only:
                    with self.lock:
                        self.conn = duckdb.connect(str(self.db_path), read_only=False)
                        self._init_schemas()
                else:
                    self.conn = duckdb.connect(str(self.db_path), read_only=True)
                break
            except Exception as e:
                last_err = e
                logger.warning(f"DuckDB connect attempt {attempt+1}/{max_retries} failed: {e}. Retrying in {retry_delay}s...")
                time.sleep(retry_delay)
                retry_delay *= 1.5
        else:
            raise last_err

    def _init_schemas(self) -> None:
        """Create reference, bronze, silver, and gold schemas and tables."""
        self.conn.execute("CREATE SCHEMA IF NOT EXISTS idxfin_ref;")
        self.conn.execute("CREATE SCHEMA IF NOT EXISTS idxfin_bronze;")
        self.conn.execute("CREATE SCHEMA IF NOT EXISTS idxfin_silver;")
        self.conn.execute("CREATE SCHEMA IF NOT EXISTS idxfin_gold;")
        self.conn.execute("CREATE SCHEMA IF NOT EXISTS idxfin_market;")

        # Ref tables
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS idxfin_ref.concept (
                concept_id VARCHAR PRIMARY KEY,
                domain VARCHAR,
                statement VARCHAR,
                label_en VARCHAR,
                label_id VARCHAR,
                data_type VARCHAR,
                unit_dimension VARCHAR,
                period_type VARCHAR,
                balance VARCHAR,
                period_aggregation VARCHAR DEFAULT 'additive'
            );
        """)

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS idxfin_ref.label_mapping (
                label_raw VARCHAR PRIMARY KEY,
                concept_id VARCHAR NOT NULL,
                mapping_method VARCHAR DEFAULT 'llm_few_shot',
                confidence DOUBLE DEFAULT 1.0,
                statement_type VARCHAR,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # Bronze tables
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS idxfin_bronze.document (
                doc_id VARCHAR PRIMARY KEY,
                issuer_id VARCHAR,
                doc_type VARCHAR,
                fiscal_year INT,
                fiscal_period VARCHAR,
                period_end DATE,
                storage_path VARCHAR,
                currency VARCHAR,
                number_locale VARCHAR,
                scale BIGINT,
                page_count INT,
                has_text_layer BOOLEAN,
                ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS idxfin_bronze.page (
                doc_id VARCHAR,
                page_no INT,
                text VARCHAR,
                PRIMARY KEY (doc_id, page_no)
            );
        """)

        # Silver tables
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS idxfin_silver.fact (
                fact_id VARCHAR PRIMARY KEY,
                issuer_id VARCHAR,
                reporting_entity VARCHAR,
                doc_id VARCHAR,
                concept_id VARCHAR,
                label_raw VARCHAR,
                label_raw_alt VARCHAR,
                note_ref VARCHAR,
                mapping_method VARCHAR,
                mapping_confidence DOUBLE,
                period_type VARCHAR,
                period_start DATE,
                period_end DATE,
                fiscal_year INT,
                fiscal_period VARCHAR,
                duration_months INT,
                is_cumulative_ytd BOOLEAN,
                scenario VARCHAR,
                dim_key VARCHAR,
                basis VARCHAR,
                consolidation_scope VARCHAR,
                value DECIMAL(38, 6),
                raw_text VARCHAR,
                unit_id VARCHAR,
                currency VARCHAR,
                scale_as_reported BIGINT,
                assurance VARCHAR,
                is_restated_column BOOLEAN,
                is_comparative BOOLEAN,
                review_status VARCHAR,
                knowledge_ts TIMESTAMP,
                ingested_at TIMESTAMP,
                lineage_page INT,
                lineage_extractor VARCHAR,
                lineage_method VARCHAR,
                lineage_model VARCHAR,
                lineage_run_id VARCHAR,
                lineage_confidence DOUBLE
            );
        """)

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS idxfin_silver.officer_position (
                issuer_id VARCHAR,
                doc_id VARCHAR,
                person_name VARCHAR,
                role VARCHAR,
                is_independent BOOLEAN,
                source_page INT
            );
        """)

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS idxfin_silver.audit_report (
                doc_id VARCHAR PRIMARY KEY,
                issuer_id VARCHAR,
                fiscal_year INT,
                audit_firm VARCHAR,
                network VARCHAR,
                signing_partner VARCHAR,
                report_date VARCHAR,
                opinion VARCHAR,
                emphasis_of_matter BOOLEAN,
                material_uncertainty_going_concern BOOLEAN,
                key_audit_matters VARCHAR,
                source_page INT
            );
        """)

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS idxfin_silver.forward_guidance (
                guidance_id VARCHAR PRIMARY KEY,
                issuer_id VARCHAR,
                doc_id VARCHAR,
                metric_label_raw VARCHAR,
                scenario VARCHAR,
                point_raw VARCHAR,
                low_raw VARCHAR,
                high_raw VARCHAR,
                unit_raw VARCHAR,
                target_period_raw VARCHAR,
                target_fiscal_year INT,
                source_page INT
            );
        """)

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS idxfin_silver.validation_result (
                run_id VARCHAR,
                doc_id VARCHAR,
                rule_id VARCHAR,
                passed BOOLEAN,
                lhs DECIMAL(38, 6),
                rhs DECIMAL(38, 6),
                diff DECIMAL(38, 6),
                tolerance DECIMAL(38, 6),
                implicated_fact_ids VARCHAR,
                message VARCHAR,
                checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS idxfin_silver.review_task (
                task_id VARCHAR PRIMARY KEY,
                doc_id VARCHAR,
                target_ids VARCHAR,
                reason VARCHAR,
                priority DOUBLE,
                status VARCHAR,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # Market tables
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS idxfin_market.stock_daily (
                ticker VARCHAR,
                trade_date DATE,
                open DOUBLE,
                high DOUBLE,
                low DOUBLE,
                close DOUBLE,
                adj_close DOUBLE,
                volume BIGINT,
                currency VARCHAR DEFAULT 'IDR',
                source VARCHAR DEFAULT 'yfinance',
                ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (ticker, trade_date)
            );
        """)

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS idxfin_market.commodity_daily (
                symbol VARCHAR,
                commodity_name VARCHAR,
                commodity_category VARCHAR,
                trade_date DATE,
                open DOUBLE,
                high DOUBLE,
                low DOUBLE,
                close DOUBLE,
                volume BIGINT,
                unit VARCHAR,
                currency VARCHAR DEFAULT 'USD',
                source VARCHAR DEFAULT 'yfinance',
                ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (symbol, trade_date)
            );
        """)

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS idxfin_market.fx_daily (
                pair VARCHAR,
                symbol VARCHAR,
                trade_date DATE,
                open DOUBLE,
                high DOUBLE,
                low DOUBLE,
                close DOUBLE,
                rate DOUBLE,
                base_currency VARCHAR DEFAULT 'USD',
                quote_currency VARCHAR DEFAULT 'IDR',
                source VARCHAR DEFAULT 'yfinance',
                ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (pair, trade_date)
            );
        """)

    def seed_taxonomy(self, mapper: Optional[ConceptMapper] = None) -> None:
        """Seed idxfin_ref.concept from taxonomy_seed.yaml."""
        m = mapper or ConceptMapper()
        rows = []
        for cid, c in m.concepts.items():
            rows.append((
                cid,
                c.domain,
                c.statement,
                c.label_en,
                c.label_id,
                c.data_type,
                c.unit_dimension,
                c.period_type,
                c.balance,
                "non_additive" if c.data_type in ("ratio", "per_share", "shares") else "additive",
            ))

        self.conn.executemany("""
            INSERT OR REPLACE INTO idxfin_ref.concept
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, rows)
        logger.info(f"Seeded {len(rows)} concepts into idxfin_ref.concept.")

    def save_document(self, doc: PDFDocument) -> None:
        """Insert document record into Bronze."""
        self.conn.execute("""
            INSERT OR REPLACE INTO idxfin_bronze.document
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP);
        """, (
            doc.doc_id,
            doc.ticker or "EMTN",
            doc.doc_type,
            doc.fiscal_year,
            doc.fiscal_period,
            doc.period_end,
            str(doc.file_path),
            doc.currency,
            doc.number_locale,
            doc.scale,
            doc.page_count,
            doc.has_text_layer,
        ))

    def save_facts(self, facts: list[SilverFact]) -> None:
        """Insert facts into Silver, upserting by fact_id."""
        if not facts:
            return
        rows = []
        for f in facts:
            rows.append((
                f.fact_id,
                f.issuer_id,
                f.reporting_entity,
                f.doc_id,
                f.concept_id,
                f.label_raw,
                f.label_raw_alt,
                f.note_ref,
                f.mapping_method,
                f.mapping_confidence,
                f.period_type,
                f.period_start,
                f.period_end,
                f.fiscal_year,
                f.fiscal_period,
                f.duration_months,
                f.is_cumulative_ytd,
                f.scenario,
                f.dim_key,
                f.basis,
                f.consolidation_scope,
                f.value,
                f.raw_text,
                f.unit_id,
                f.currency,
                f.scale_as_reported,
                f.assurance,
                f.is_restated_column,
                f.is_comparative,
                f.review_status,
                f.knowledge_ts,
                f.ingested_at,
                f.lineage_page,
                f.lineage_extractor,
                f.lineage_method,
                f.lineage_model,
                f.lineage_run_id,
                f.lineage_confidence,
            ))

        self.conn.executemany("""
            INSERT OR REPLACE INTO idxfin_silver.fact
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, rows)
        logger.info(f"Saved {len(rows)} facts into idxfin_silver.fact.")

    def save_officers(self, officers: list[contracts.OfficerPosition], doc: PDFDocument) -> None:
        rows = []
        for o in officers:
            rows.append((
                doc.ticker or "EMTN",
                doc.doc_id,
                o.person_name_raw,
                str(o.role),
                o.is_independent,
                o.source.page,
            ))
        if rows:
            self.conn.executemany("INSERT INTO idxfin_silver.officer_position VALUES (?, ?, ?, ?, ?, ?);", rows)

    def save_audit_report(self, audit: contracts.AuditReport, doc: PDFDocument) -> None:
        kams = "; ".join(audit.key_audit_matters) if audit.key_audit_matters else ""
        self.conn.execute("""
            INSERT OR REPLACE INTO idxfin_silver.audit_report VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, (
            doc.doc_id,
            doc.ticker or "EMTN",
            doc.fiscal_year,
            audit.audit_firm_raw,
            audit.network_raw,
            audit.signing_partner_raw,
            str(audit.report_date) if audit.report_date else None,
            str(audit.opinion),
            audit.emphasis_of_matter,
            audit.material_uncertainty_going_concern,
            kams,
            audit.source.page,
        ))

    def save_guidance(self, guidance: list[contracts.ForwardGuidance], doc: PDFDocument) -> None:
        rows = []
        for g in guidance:
            gid = f"guid.{doc.doc_id[:8]}.{abs(hash(g.metric_label_raw))}"
            rows.append((
                gid,
                doc.ticker or "EMTN",
                doc.doc_id,
                g.metric_label_raw,
                str(g.scenario),
                g.point_raw,
                g.low_raw,
                g.high_raw,
                g.unit_raw,
                g.target_period_raw,
                g.target_fiscal_year,
                g.source.page,
            ))
        if rows:
            self.conn.executemany("INSERT OR REPLACE INTO idxfin_silver.forward_guidance VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);", rows)

    def save_validation_results(self, results: list[ValidationRuleResult], doc_id: str, run_id: str) -> None:
        rows = []
        for r in results:
            rows.append((
                run_id,
                doc_id,
                r.rule_id,
                r.passed,
                r.lhs,
                r.rhs,
                r.diff,
                r.tolerance,
                ",".join(r.implicated_fact_ids),
                r.message,
            ))
        if rows:
            self.conn.executemany("INSERT INTO idxfin_silver.validation_result VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP);", rows)

    def save_review_tasks(self, tasks: list[ReviewTask]) -> None:
        rows = []
        for t in tasks:
            rows.append((
                t.task_id,
                t.doc_id,
                ",".join(t.target_ids),
                t.reason,
                t.priority,
                t.status,
            ))
        if rows:
            self.conn.executemany("INSERT OR REPLACE INTO idxfin_silver.review_task VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP);", rows)

    def ingest_from_staging(self, run_id: str, staging_store: Optional[ParquetStagingStore] = None) -> dict[str, Any]:
        """Bulk ingest staged Parquet files into DuckDB in a single transaction protected by FileLock."""
        staging = staging_store or ParquetStagingStore()
        run_dir = staging.get_run_dir(run_id, status="pending")
        if not run_dir.exists():
            run_dir = staging.get_run_dir(run_id, status="completed")
            if not run_dir.exists():
                raise FileNotFoundError(f"Staging directory for run {run_id} not found in pending or completed.")

        stats: dict[str, Any] = {"run_id": run_id, "tables_ingested": [], "records_ingested": {}}
        with self.lock:
            # 1. Ingest Bronze Document
            doc_file = run_dir / "document.parquet"
            if doc_file.exists():
                self.conn.execute(f"""
                    INSERT OR REPLACE INTO idxfin_bronze.document
                    SELECT 
                        doc_id, issuer_id, doc_type, fiscal_year, fiscal_period,
                        TRY_CAST(period_end AS DATE), storage_path, currency,
                        number_locale, scale, page_count, has_text_layer,
                        TRY_CAST(ingested_at AS TIMESTAMP)
                    FROM read_parquet('{doc_file}');
                """)
                stats["tables_ingested"].append("idxfin_bronze.document")

            # 2. Ingest Silver Facts
            fact_file = run_dir / "facts.parquet"
            if fact_file.exists():
                self.conn.execute(f"""
                    INSERT OR REPLACE INTO idxfin_silver.fact
                    SELECT 
                        fact_id, issuer_id, reporting_entity, doc_id, concept_id, label_raw, label_raw_alt, note_ref,
                        mapping_method, mapping_confidence, period_type, 
                        TRY_CAST(period_start AS DATE), TRY_CAST(period_end AS DATE),
                        fiscal_year, fiscal_period, duration_months, is_cumulative_ytd, scenario, dim_key,
                        basis, consolidation_scope, CAST(value AS DECIMAL(38, 6)), raw_text, unit_id, currency,
                        scale_as_reported, assurance, is_restated_column, is_comparative, review_status,
                        TRY_CAST(knowledge_ts AS TIMESTAMP), TRY_CAST(ingested_at AS TIMESTAMP),
                        lineage_page, lineage_extractor, lineage_method, lineage_model, lineage_run_id, lineage_confidence
                    FROM read_parquet('{fact_file}');
                """)
                fact_count = self.conn.execute(f"SELECT COUNT(*) FROM read_parquet('{fact_file}')").fetchone()[0]
                stats["tables_ingested"].append("idxfin_silver.fact")
                stats["records_ingested"]["facts"] = fact_count

            # 3. Ingest Officers
            officer_file = run_dir / "officers.parquet"
            if officer_file.exists():
                self.conn.execute(f"""
                    INSERT INTO idxfin_silver.officer_position
                    SELECT issuer_id, doc_id, person_name, role, is_independent, source_page
                    FROM read_parquet('{officer_file}');
                """)
                stats["tables_ingested"].append("idxfin_silver.officer_position")

            # 4. Ingest Guidance
            guidance_file = run_dir / "guidance.parquet"
            if guidance_file.exists():
                self.conn.execute(f"""
                    INSERT OR REPLACE INTO idxfin_silver.forward_guidance
                    SELECT 
                        guidance_id, issuer_id, doc_id, metric_label_raw, scenario, point_raw,
                        low_raw, high_raw, unit_raw, target_period_raw, target_fiscal_year, source_page
                    FROM read_parquet('{guidance_file}');
                """)
                stats["tables_ingested"].append("idxfin_silver.forward_guidance")

            # 5. Ingest Audit Reports
            audit_file = run_dir / "audit_reports.parquet"
            if audit_file.exists():
                self.conn.execute(f"""
                    INSERT OR REPLACE INTO idxfin_silver.audit_report
                    SELECT 
                        doc_id, issuer_id, fiscal_year, audit_firm, network, signing_partner,
                        report_date, opinion, emphasis_of_matter, material_uncertainty_going_concern,
                        key_audit_matters, source_page
                    FROM read_parquet('{audit_file}');
                """)
                stats["tables_ingested"].append("idxfin_silver.audit_report")

            # 6. Ingest Validation Results
            val_file = run_dir / "validation_results.parquet"
            if val_file.exists():
                self.conn.execute(f"""
                    INSERT INTO idxfin_silver.validation_result
                    SELECT 
                        run_id, doc_id, rule_id, passed,
                        CAST(lhs AS DECIMAL(38, 6)), CAST(rhs AS DECIMAL(38, 6)),
                        CAST(diff AS DECIMAL(38, 6)), CAST(tolerance AS DECIMAL(38, 6)),
                        implicated_fact_ids, message, TRY_CAST(checked_at AS TIMESTAMP)
                    FROM read_parquet('{val_file}');
                """)
                stats["tables_ingested"].append("idxfin_silver.validation_result")

            # 7. Ingest Review Tasks
            task_file = run_dir / "review_tasks.parquet"
            if task_file.exists():
                self.conn.execute(f"""
                    INSERT OR REPLACE INTO idxfin_silver.review_task
                    SELECT task_id, doc_id, target_ids, reason, priority, status, TRY_CAST(created_at AS TIMESTAMP)
                    FROM read_parquet('{task_file}');
                """)
                stats["tables_ingested"].append("idxfin_silver.review_task")

            # 8. Materialize Gold Views
            from src.storage.gold_views import create_gold_views
            create_gold_views(self)

            # Move from pending to completed
            staging.mark_completed(run_id)

        logger.info(f"Successfully ingested staging run {run_id} into DuckDB: {stats}")
        return stats

    def save_stock_prices(self, df) -> int:
        """Upsert daily stock prices into idxfin_market.stock_daily."""
        if df.empty:
            return 0
        with self.lock:
            self.conn.register("df_stocks_temp", df)
            self.conn.execute("""
                INSERT OR REPLACE INTO idxfin_market.stock_daily
                SELECT 
                    ticker,
                    CAST(trade_date AS DATE) AS trade_date,
                    CAST(open AS DOUBLE) AS open,
                    CAST(high AS DOUBLE) AS high,
                    CAST(low AS DOUBLE) AS low,
                    CAST(close AS DOUBLE) AS close,
                    CAST(adj_close AS DOUBLE) AS adj_close,
                    CAST(volume AS BIGINT) AS volume,
                    COALESCE(currency, 'IDR') AS currency,
                    COALESCE(source, 'yfinance') AS source,
                    CURRENT_TIMESTAMP AS ingested_at
                FROM df_stocks_temp;
            """)
            self.conn.unregister("df_stocks_temp")
            logger.info(f"Saved {len(df)} stock prices into idxfin_market.stock_daily.")
            return len(df)

    def save_commodity_prices(self, df) -> int:
        """Upsert daily commodity prices into idxfin_market.commodity_daily."""
        if df.empty:
            return 0
        with self.lock:
            self.conn.register("df_commodities_temp", df)
            self.conn.execute("""
                INSERT OR REPLACE INTO idxfin_market.commodity_daily
                SELECT 
                    symbol,
                    commodity_name,
                    commodity_category,
                    CAST(trade_date AS DATE) AS trade_date,
                    CAST(open AS DOUBLE) AS open,
                    CAST(high AS DOUBLE) AS high,
                    CAST(low AS DOUBLE) AS low,
                    CAST(close AS DOUBLE) AS close,
                    CAST(volume AS BIGINT) AS volume,
                    unit,
                    COALESCE(currency, 'USD') AS currency,
                    COALESCE(source, 'yfinance') AS source,
                    CURRENT_TIMESTAMP AS ingested_at
                FROM df_commodities_temp;
            """)
            self.conn.unregister("df_commodities_temp")
            logger.info(f"Saved {len(df)} commodity prices into idxfin_market.commodity_daily.")
            return len(df)

    def save_fx_rates(self, df) -> int:
        """Upsert daily foreign exchange (FX) rates into idxfin_market.fx_daily."""
        if df.empty:
            return 0
        with self.lock:
            self.conn.register("df_fx_temp", df)
            self.conn.execute("""
                INSERT OR REPLACE INTO idxfin_market.fx_daily
                SELECT 
                    pair,
                    symbol,
                    CAST(trade_date AS DATE) AS trade_date,
                    CAST(open AS DOUBLE) AS open,
                    CAST(high AS DOUBLE) AS high,
                    CAST(low AS DOUBLE) AS low,
                    CAST(close AS DOUBLE) AS close,
                    CAST(COALESCE(rate, close) AS DOUBLE) AS rate,
                    COALESCE(base_currency, 'USD') AS base_currency,
                    COALESCE(quote_currency, 'IDR') AS quote_currency,
                    COALESCE(source, 'yfinance') AS source,
                    CURRENT_TIMESTAMP AS ingested_at
                FROM df_fx_temp;
            """)
            self.conn.unregister("df_fx_temp")
            logger.info(f"Saved {len(df)} FX rates into idxfin_market.fx_daily.")
            return len(df)

    def get_market_tickers(self) -> list[str]:
        """Return list of distinct tickers present in bronze documents and silver facts."""
        query = """
            SELECT DISTINCT issuer_id FROM idxfin_bronze.document WHERE issuer_id IS NOT NULL AND issuer_id <> ''
            UNION
            SELECT DISTINCT issuer_id FROM idxfin_silver.fact WHERE issuer_id IS NOT NULL AND issuer_id <> ''
            ORDER BY issuer_id;
        """
        rows = self.conn.execute(query).fetchall()
        return [r[0] for r in rows if r[0]]

    def close(self) -> None:
        self.conn.close()

