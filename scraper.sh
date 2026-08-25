#!/bin/bash
# Google Maps Scraper - Script para cron en VPS
# Uso: ./scraper.sh [query] [location] [max_results] [search_email]

SCRAPER_DIR="/opt/google-maps-scraper"
LOG_DIR="/var/log/google-maps-scraper"
OUTPUT_DIR="$SCRAPER_DIR/output"

mkdir -p "$LOG_DIR" "$OUTPUT_DIR"

# Variables con defaults
QUERY="${1:-restaurantes}"
LOCATION="${2:-New York}"
MAX_RESULTS="${3:-20}"
SEARCH_EMAIL="${4:-true}"

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
LOG_FILE="$LOG_DIR/run_${TIMESTAMP}.log"

echo "========================================" >> "$LOG_FILE"
echo "[$(date)] Iniciando scraper" >> "$LOG_FILE"
echo "  Query: $QUERY" >> "$LOG_FILE"
echo "  Location: $LOCATION" >> "$LOG_FILE"
echo "  Max Results: $MAX_RESULTS" >> "$LOG_FILE"
echo "  Search Email: $SEARCH_EMAIL" >> "$LOG_FILE"
echo "========================================" >> "$LOG_FILE"

# Ejecutar scraper
cd "$SCRAPER_DIR"
python google_maps_scraper.py \
    -q "$QUERY" \
    -l "$LOCATION" \
    -m "$MAX_RESULTS" \
    -o excel \
    --full-details \
    --search-email="$SEARCH_EMAIL" \
    2>&1 | tee -a "$LOG_FILE"

echo "[$(date)] Completado" >> "$LOG_FILE"
echo "" >> "$LOG_FILE"
