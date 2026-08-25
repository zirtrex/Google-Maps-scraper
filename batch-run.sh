#!/bin/bash
# Batch runner - Ejecuta todas las queries de config/queries.json
# Uso: ./batch-run.sh [daily|weekly|all]

SCRAPER_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG_FILE="$SCRAPER_DIR/config/queries.json"
LOG_DIR="$SCRAPER_DIR/logs"
SCRAPER="$SCRAPER_DIR/google_maps_scraper.py"

mkdir -p "$LOG_DIR"

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BATCH_LOG="$LOG_DIR/batch_${TIMESTAMP}.log"

echo "========================================" > "$BATCH_LOG"
echo "[$(date)] Batch run iniciado" >> "$BATCH_LOG"
echo "  Mod: $1" >> "$BATCH_LOG"
echo "========================================" >> "$BATCH_LOG"

# Funcion para ejecutar una query
run_query() {
    local query="$1"
    local location="$2"
    local max_results="$3"
    local search_email="$4"
    local mod="$5"

    echo "" >> "$BATCH_LOG"
    echo "[$(date)] Ejecutando: $query en $location ($max_results resultados)" >> "$BATCH_LOG"

    python "$SCRAPER" \
        -q "$query" \
        -l "$location" \
        -m "$max_results" \
        -o excel \
        --full-details \
        --search-email="$search_email" \
        2>&1 | tee -a "$BATCH_LOG"

    echo "[$(date)] Completado: $query en $location" >> "$BATCH_LOG"
}

# Leer config segun modo
MODE="${1:-all}"

if [ "$MODE" = "daily" ]; then
    QUERIES=$(python3 -c "
import json
with open('$CONFIG_FILE') as f:
    config = json.load(f)
for q in config.get('daily', []):
    print(f\"{q['query']}|{q['location']}|{q['max_results']}|{q['search_email']}\")
")
elif [ "$MODE" = "weekly" ]; then
    QUERIES=$(python3 -c "
import json
with open('$CONFIG_FILE') as f:
    config = json.load(f)
for q in config.get('weekly', []):
    print(f\"{q['query']}|{q['location']}|{q['max_results']}|{q['search_email']}\")
")
else
    QUERIES=$(python3 -c "
import json
with open('$CONFIG_FILE') as f:
    config = json.load(f)
for q in config.get('daily', []) + config.get('weekly', []):
    print(f\"{q['query']}|{q['location']}|{q['max_results']}|{q['search_email']}\")
")
fi

# Ejecutar cada query
echo "$QUERIES" | while IFS='|' read -r query location max_results search_email; do
    if [ -n "$query" ]; then
        run_query "$query" "$location" "$max_results" "$search_email" "$MODE"
    fi
done

echo "" >> "$BATCH_LOG"
echo "[$(date)] Batch run completado" >> "$BATCH_LOG"
echo "========================================" >> "$BATCH_LOG"

echo "Batch completado. Log: $BATCH_LOG"
