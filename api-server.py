#!/usr/bin/env python3
"""API simple para el scraper - Usar con n8n.
Endpoint POST /run devuelve los resultados nuevos para escribirlos en Sheets.
El cap diario (MAX_DAILY) y el dedup los gestiona el scraper via history.json compartido.
"""
import os
import sys
import json
from http.server import HTTPServer, BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from google_maps_scraper import (
    scrape_google_maps,
    setup_driver,
    save_to_file,
    update_last_run,
    today_new_count,
    MAX_DAILY,
)
from datetime import datetime

# Columnas que se devuelven (para escribir en Sheets)
COLUMNS = [
    "nombre", "rating", "reviews", "categoria", "direccion",
    "telefono", "web", "email", "horario", "scraped_at",
]


class ScraperHandler(BaseHTTPRequestHandler):
    def _json(self, code, payload):
        self.send_response(code)
        self.send_header("Content-type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def do_POST(self):
        if self.path != "/run":
            self._json(404, {"status": "not_found"})
            return

        try:
            content_length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(content_length)) if content_length else {}
        except Exception:
            self._json(400, {"status": "bad_request"})
            return

        query = data.get("query", "restaurantes")
        location = data.get("location", "New York")
        max_results = int(data.get("max_results", 10))
        search_email = bool(data.get("search_email", True))
        full_details = bool(data.get("full_details", True))

        print(f"API: {query} en {location} (max {max_results})")

        # Avisar si el cap diario ya se alcanzó
        if MAX_DAILY > 0 and today_new_count() >= MAX_DAILY:
            self._json(200, {
                "status": "daily_cap_reached",
                "new_results": 0,
                "results": [],
                "query": query,
                "location": location,
            })
            return

        driver = setup_driver(headless=True)
        try:
            results, skipped = scrape_google_maps(
                driver, query, location, max_results,
                full_details, search_email, dedup=True,
            )

            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            rows = []
            for r in results:
                r["scraped_at"] = now
                rows.append({c: r.get(c, "") for c in COLUMNS})

            if rows:
                save_to_file(results, "excel", query, location)
                update_last_run(query, location)

            self._json(200, {
                "status": "success",
                "new_results": len(rows),
                "skipped": skipped,
                "query": query,
                "location": location,
                "results": rows,
                "timestamp": datetime.now().isoformat(),
            })
        except Exception as e:
            self._json(500, {
                "status": "error",
                "error": str(e),
                "query": query,
                "location": location,
                "results": [],
            })
        finally:
            try:
                driver.quit()
            except Exception:
                pass

    def do_GET(self):
        if self.path == "/health":
            self._json(200, {"status": "ok", "today_new": today_new_count(), "max_daily": MAX_DAILY})
        else:
            self._json(404, {"status": "not_found"})

    def log_message(self, fmt, *args):
        print(f"[API] {args[0]}")


def run_api(port=8080):
    server = HTTPServer(("0.0.0.0", port), ScraperHandler)
    print(f"API running on port {port}")
    server.serve_forever()


if __name__ == "__main__":
    run_api(int(os.environ.get("API_PORT", 8080)))
