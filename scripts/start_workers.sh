#!/usr/bin/env bash
set -e

echo "=========================================================="
echo " Starting Celery Workers for IDX Extraction Pipeline"
echo "=========================================================="
echo " - Extraction Worker Pool: Concurrency = 6 (Queue: extraction)"
echo " - DuckDB Writer Worker:   Concurrency = 1 (Queue: duckdb_writer)"
echo "=========================================================="

# Start DuckDB writer single worker in background
celery -A src.celery_app worker -Q duckdb_writer -c 1 --loglevel=info -n duckdb_writer@%h &
WRITER_PID=$!

# Start Extraction worker pool (concurrency = 6)
celery -A src.celery_app worker -Q extraction -c 6 --loglevel=info -n extraction_pool@%h &
EXTRACT_PID=$!

trap "kill $WRITER_PID $EXTRACT_PID; exit" SIGINT SIGTERM

wait
