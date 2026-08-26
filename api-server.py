#!/usr/bin/env python3
"""API simple para el scraper - Usar con n8n.
Endpoint POST /run devuelve los resultados nuevos para escribirlos en Sheets.
El cap diario (MAX_DAILY) y el dedup los gestiona el scraper via history.json compartido.
"""
import os
import sys
import json
import traceback
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

# Opcional: si se define API_KEY, /run exige header X-API-Key
API_KEY = os.environ.get("API_KEY", "")


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

        if API_KEY and self.headers.get("X-API-Key", "") != API_KEY:
            self._json(401, {"status": "unauthorized"})
            return

        content_length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(content_length) if content_length > 0 else b""
        ct = self.headers.get("Content-Type", "")
        ua = self.headers.get("User-Agent", "")
        print(f"[API] /run len={len(raw)} ct={ct!r} ua={ua!r}", flush=True)
        print(f"[API] raw={raw[:200]!r}", flush=True)

        try:
            data = json.loads(raw) if raw else {}
        except Exception:
            self._json(400, {
                "status": "bad_request",
                "hint": "El body debe ser JSON: {\"query\", \"location\", \"max_results\", \"search_email\"}",
                "content_type": ct,
            })
            return
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except Exception:
                data = {}
        if not isinstance(data, dict):
            self._json(400, {"status": "bad_request", "hint": "El body debe ser un objeto JSON"})
            return

        query = str(data.get("query", "restaurantes"))
        location = str(data.get("location", "New York"))
        try:
            max_results = int(str(data.get("max_results", 10) or 10))
        except (TypeError, ValueError):
            max_results = 10
        se = data.get("search_email", True)
        search_email = se if isinstance(se, bool) else str(se).strip().lower() in ("1", "true", "yes", "t", "y", "si")
        fd = data.get("full_details", True)
        full_details = fd if isinstance(fd, bool) else str(fd).strip().lower() in ("1", "true", "yes", "t", "y", "si")

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

        # 2 intentos con driver fresco (los crashes de Chrome suelen ser transitorios;
        # el dedup por history.json impide duplicados en el reintento)
        results = None
        skipped = 0
        last_err = ""
        for attempt in (1, 2):
            try:
                driver = setup_driver(headless=True)
                try:
                    results, skipped = scrape_google_maps(
                        driver, query, location, max_results,
                        full_details, search_email, dedup=True,
                    )
                    last_err = ""
                    break
                finally:
                    try:
                        driver.quit()
                    except Exception:
                        pass
            except Exception:
                last_err = traceback.format_exc()
                print(f"[API] intento {attempt}/2 fallo:\n{last_err}", flush=True)

        if results is None:
            self._json(500, {
                "status": "error",
                "error": last_err.strip().splitlines()[-1] if last_err else "unknown",
                "query": query,
                "location": location,
                "results": [],
            })
            return

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

    def do_GET(self):
        print(f"[API] GET {self.path} ua={self.headers.get('User-Agent', '')!r}", flush=True)
        if self.path == "/health":
            self._json(200, {"status": "ok", "today_new": today_new_count(), "max_daily": MAX_DAILY})
        else:
            self._json(404, {"status": "not_found"})

    def log_message(self, fmt, *args):
        try:
            msg = fmt % args if args else fmt
        except Exception:
            msg = f"{fmt} args={args!r}"
        print(f"[API] {msg}", flush=True)


class QuietHTTPServer(HTTPServer):
    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, TimeoutError, BrokenPipeError)):
            return
        super().handle_error(request, client_address)


def run_api(port=8080):
    server = QuietHTTPServer(("0.0.0.0", port), ScraperHandler)
    print(f"API running on port {port}")
    server.serve_forever()


if __name__ == "__main__":
    run_api(int(os.environ.get("API_PORT", 8080)))
