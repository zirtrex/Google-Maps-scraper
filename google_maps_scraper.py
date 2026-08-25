import os
import time
import re
import json
import argparse
from datetime import datetime, timedelta
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.wait import TimeoutException
import pandas as pd
import requests
from bs4 import BeautifulSoup
from collections import deque
from urllib.parse import urljoin, urlparse, urldefrag
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Configuracion desde variables de entorno
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
QUERY = os.environ.get("QUERY", "restaurantes")
LOCATION = os.environ.get("LOCATION", "New York")
MAX_RESULTS = int(os.environ.get("MAX_RESULTS", "20"))
OUTPUT_FORMAT = os.environ.get("OUTPUT_FORMAT", "excel")
FULL_DETAILS = os.environ.get("FULL_DETAILS", "true").lower() == "true"
SEARCH_EMAIL = os.environ.get("SEARCH_EMAIL", "true").lower() == "true"
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
DEFAULT_OUTPUT_DIR = os.path.join(PROJECT_DIR, "output")
OUTPUT_DIR = os.environ.get("OUTPUT_DIR", DEFAULT_OUTPUT_DIR)
# Historial FIJO en la raiz del proyecto (independiente de OUTPUT_DIR) para que el
# dedup diario no se reinicie si cambias OUTPUT_DIR
HISTORY_FILE = os.environ.get("HISTORY_FILE", os.path.join(PROJECT_DIR, "history.json"))
DAYS_TO_KEEP = int(os.environ.get("DAYS_TO_KEEP", "30"))
MAX_DAILY = int(os.environ.get("MAX_DAILY", "0"))  # 0 = sin limite diario de nuevos


def setup_driver(headless=True):
    chrome_options = Options()
    if headless:
        chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--lang=en-US")
    chrome_options.add_argument("--accept-lang=en-US,en")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_argument("--disable-gpu")

    driver = webdriver.Chrome(options=chrome_options)
    return driver


def load_history():
    """Cargar historial de resultados escaneados"""
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r") as f:
            return json.load(f)
    return {"scraped": {}, "config": {}}


def save_history(history):
    """Guardar historial"""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)


def load_existing_results():
    """Cargar resultados ya guardados para evitar duplicados"""
    existing = {}
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r") as f:
            history = json.load(f)
            existing = {k.lower(): v for k, v in history.get("scraped", {}).items()}
    return existing


def is_duplicate(nombre, existing_results):
    """Verificar si ya fue escaneado"""
    return nombre.lower() in existing_results


def today_new_count():
    """Cantidad de negocios capturados hoy (para el cap diario MAX_DAILY)"""
    history = load_history()
    today = datetime.now().strftime("%Y-%m-%d")
    count = 0
    for data in history.get("scraped", {}).values():
        if data.get("last_seen", "").startswith(today):
            count += 1
    return count


def get_last_run_date(query, location):
    """Obtener fecha de ultima ejecucion para este query/location"""
    history = load_history()
    key = f"{query}_{location}"
    if key in history.get("config", {}):
        return history["config"][key]
    return None


def update_last_run(query, location):
    """Actualizar fecha de ultima ejecucion"""
    history = load_history()
    if "config" not in history:
        history["config"] = {}
    key = f"{query}_{location}"
    history["config"][key] = datetime.now().isoformat()
    save_history(history)


def save_to_file(results, fmt, query, location):
    """Guardar resultados en archivo"""
    if not results:
        print("No hay resultados nuevos para guardar.")
        return

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    query_slug = re.sub(r"[^a-zA-Z0-9]+", "_", query).strip("_")
    loc_slug = re.sub(r"[^a-zA-Z0-9]+", "_", location).strip("_")

    # Agregar fecha de escaneo
    for r in results:
        r["scraped_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")

    df = pd.DataFrame(results)

    if fmt == "csv":
        filename = f"results_{query_slug}_{loc_slug}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        filepath = os.path.join(OUTPUT_DIR, filename)
        df.to_csv(filepath, index=False, encoding="utf-8-sig")
        print(f"Guardado en: {filepath}")
    elif fmt == "json":
        filename = f"results_{query_slug}_{loc_slug}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        filepath = os.path.join(OUTPUT_DIR, filename)
        df.to_json(filepath, index=False, orient="records", force_ascii=False)
        print(f"Guardado en: {filepath}")
    elif fmt == "excel":
        filename = f"results_{query_slug}_{loc_slug}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        filepath = os.path.join(OUTPUT_DIR, filename)
        df.to_excel(filepath, index=False, engine="openpyxl")
        print(f"Guardado en: {filepath}")

    # Actualizar historial
    existing = load_existing_results()
    history = load_history()
    if "scraped" not in history:
        history["scraped"] = {}

    for r in results:
        nombre = r.get("nombre", "").lower()
        if nombre:
            history["scraped"][nombre] = {
                "last_seen": datetime.now().isoformat(),
                "rating": r.get("rating", ""),
                "email": r.get("email", ""),
                "web": r.get("web", ""),
            }

    save_history(history)


def extract_from_list(article):
    """Extract basic info from the list view"""
    detail = {}
    spans = article.find_elements(By.TAG_NAME, "span")

    # Name
    name = ""
    for span in spans:
        if span.text.strip():
            name = span.text.strip()
            break
    detail["nombre"] = name if name else "N/A"

    # Rating
    detail["rating"] = "N/A"
    if len(spans) > 4 and spans[4].text.strip():
        detail["rating"] = spans[4].text.strip()

    # Category
    detail["categoria"] = "N/A"
    for span in spans[7:]:
        text = span.text.strip()
        if text and not re.match(r"^[\d.,]+$", text) and not text.startswith("Abierto") and not text.startswith("Cerrado"):
            detail["categoria"] = text
            break

    # Get the link
    links = article.find_elements(By.TAG_NAME, "a")
    detail["href"] = links[0].get_attribute("href") if links else ""

    return detail


def extract_from_detail(driver):
    """Extract full details from the Google Maps detail page"""
    detail = {}
    time.sleep(2)

    spans = driver.find_elements(By.TAG_NAME, "span")
    links = driver.find_elements(By.TAG_NAME, "a")

    # Rating
    detail["rating"] = "N/A"
    for span in spans:
        text = span.text.strip()
        if re.match(r"^\d+\.\d+$", text) and len(text) == 3:
            detail["rating"] = text
            break

    # Reviews
    detail["reviews"] = "N/A"
    for span in spans:
        text = span.text.strip()
        review_match = re.match(r"^\(([\d.,]+)\)$", text)
        if review_match:
            num = review_match.group(1).replace(",", "").replace(".", "")
            if num.isdigit() and int(num) > 100:
                detail["reviews"] = num
                break

    # Category
    detail["categoria"] = "N/A"
    for span in spans:
        text = span.text.strip()
        if text and not re.match(r"^[\d.,]+$", text) and not text.startswith("Cerrado") and not text.startswith("Abierto") and "reviews" not in text.lower() and "opinion" not in text.lower() and len(text) > 5 and len(text) < 150:
            if "foto" not in text.lower() and "sugerir" not in text.lower() and "escribir" not in text.lower():
                detail["categoria"] = text
                break

    # Address
    detail["direccion"] = "N/A"
    for span in spans:
        text = span.text.strip()
        if text and ("St." in text or "Ave." in text or "Av." in text or "C." in text or "Calle" in text or "Blvd" in text or "Broadway" in text or "Wall" in text or "Park" in text or "Floor" in text or "Piso" in text):
            if not text.startswith("Cerrado") and not text.startswith("Abierto") and "reviews" not in text.lower():
                detail["direccion"] = text
                break

    # Phone
    detail["telefono"] = "N/A"
    for link in links:
        href = link.get_attribute("href")
        if href and href.startswith("tel:"):
            detail["telefono"] = href.replace("tel:", "")
            break

    # Website
    detail["web"] = "N/A"
    for link in links:
        href = link.get_attribute("href")
        text = link.text.strip()
        if not href:
            continue
        if href.startswith("tel:") or href.startswith("mailto:") or href.startswith("javascript:"):
            continue
        low = href.lower()
        if any(b in low for b in BLOCKED_WEB_HOSTS):
            continue
        if re.search(r'https?://[^/]+\.(com|org|net|es|co|uk|de|fr|it|ca|au|mx|ar|cl|pe|br)', href):
            detail["web"] = href
            break
        if text and re.match(r'^www\.', text):
            detail["web"] = "https://" + text
            break

    # Hours
    detail["horario"] = "N/A"
    for span in spans:
        text = span.text.strip()
        if text.startswith("Cerrado") or text.startswith("Abierto"):
            detail["horario"] = text
            break

    # Email from Google Maps
    detail["email"] = "N/A"
    for link in links:
        href = link.get_attribute("href")
        text = link.text.strip()
        if href and ("mailto:" in href or re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', text)):
            detail["email"] = href.replace("mailto:", "") if "mailto:" in href else text
            break

    return detail


# Hosts que no son el sitio propio (redes, mensajeria, reservas, mapas)
BLOCKED_WEB_HOSTS = [
    "google.com", "googleusercontent", "gstatic", "maps.", "opentable",
    "reserve", "goo.gle", "searchviewer", "whatsapp", "wa.me", "m.me",
    "messenger", "telegram", "t.me", "instagram", "facebook.com", "fb.com",
    "twitter", "x.com", "linkedin", "youtube", "youtu.be", "tiktok",
    "pinterest", "foursquare", "tripadvisor", "yelp", "justeat", "deliveroo",
    "uber", "spotify", "soundcloud", "gravatar",
]

EMAIL_BLACKLIST = [
    "noreply", "no-reply", "no.reply", "example.com", "domain.com",
    "email.com", "test.com", "placeholder", "yourmail", "your.email",
    "name@", "user@", "info@localhost", "sentry", "wixpress", "@2x",
    "@3x", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", "schema.org",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

EMAIL_RE = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9-]+\.[a-zA-Z]{2,}'


def _valid_email(raw):
    raw = raw.strip()
    if not re.match(EMAIL_RE, raw):
        return None
    low = raw.lower()
    if any(b in low for b in EMAIL_BLACKLIST):
        return None
    return raw


def _emails_from_html(html):
    """Extraer emails de una pagina: mailto, JSON-LD, texto y scripts."""
    found = set()
    soup = BeautifulSoup(html, "html.parser")

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.lower().startswith("mailto:"):
            e = _valid_email(href[7:].split("?")[0])
            if e:
                found.add(e)

    for script in soup.find_all("script", type="application/ld+json"):
        txt = script.string or script.get_text()
        if txt:
            for m in re.findall(r'"email"\s*:\s*"([^"]+@[^"]+)"', txt, re.I):
                e = _valid_email(m)
                if e:
                    found.add(e)

    text = soup.get_text(" ", strip=True)
    for m in re.findall(EMAIL_RE, text):
        e = _valid_email(m)
        if e:
            found.add(e)

    for script in soup.find_all("script"):
        s = script.string or ""
        if "@" in s:
            for m in re.findall(EMAIL_RE, s):
                e = _valid_email(m)
                if e:
                    found.add(e)

    return found


def _headless_email_fallback(url, driver):
    """Renderizar la web con navegador para capturar emails generados por JS."""
    if driver is None:
        return "N/A"
    current_tab = driver.current_window_handle
    try:
        driver.switch_to.new_window("tab")
        driver.get(url)
        time.sleep(4)
        found = _emails_from_html(driver.page_source)
        if not found:
            for path in ("/contact", "/contacto", "/contact-us", "/nosotros"):
                try:
                    driver.get(url.rstrip("/") + path)
                    time.sleep(3)
                    found |= _emails_from_html(driver.page_source)
                    if found:
                        break
                except Exception:
                    continue
        return sorted(found)[0] if found else "N/A"
    except Exception:
        return "N/A"
    finally:
        try:
            driver.close()
        except Exception:
            pass
        try:
            driver.switch_to.window(current_tab)
        except Exception:
            pass


def search_email_on_website(url, name, max_pages=12, driver=None):
    """Buscar email: crawl estatico BFS + fallback headless si no encuentra."""
    if not url or url == "N/A":
        return "N/A"

    url = re.split(r'[?#]', url)[0]
    if not url.startswith("http"):
        url = "https://" + url

    start_netloc = urlparse(url).netloc.lower().lstrip("www.")

    queue = deque([url])
    seen = set()
    found = set()
    pages_checked = 0

    # Intentar sitemap para encontrar paginas de contacto
    try:
        sm = requests.get(url.rstrip("/") + "/sitemap.xml", headers=HEADERS, timeout=6, verify=False)
        if sm.status_code == 200:
            for loc in re.findall(r"<loc>\s*(\S+?)\s*</loc>", sm.text)[:50]:
                if urlparse(loc).netloc.lower().lstrip("www.") == start_netloc:
                    if any(k in loc.lower() for k in ("contact", "contacto", "nosotros", "about")):
                        queue.appendleft(loc)
    except Exception:
        pass

    while queue and pages_checked < max_pages and not found:
        u = queue.popleft()
        if u in seen:
            continue
        seen.add(u)
        pages_checked += 1
        try:
            resp = requests.get(u, headers=HEADERS, timeout=8, verify=False, allow_redirects=True)
            if resp.status_code != 200:
                continue
            ctype = resp.headers.get("Content-Type", "")
            if ctype and "html" not in ctype and "xml" not in ctype and "text" not in ctype:
                continue
            html = resp.text
            found |= _emails_from_html(html)
            if found:
                break
            soup = BeautifulSoup(html, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if href.startswith(("mailto:", "tel:", "javascript:")):
                    continue
                absu = urljoin(u, href)
                p = urlparse(absu)
                if p.scheme not in ("http", "https"):
                    continue
                if p.netloc.lower().lstrip("www.") != start_netloc:
                    continue
                clean = urldefrag(absu)[0]
                if clean in seen or len(clean) > 250:
                    continue
                if any(k in clean.lower() for k in ("contact", "contacto", "nosotros", "about", "get-in-touch", "hola")):
                    queue.appendleft(clean)
                else:
                    queue.append(clean)
        except Exception:
            continue

    if found:
        return sorted(found)[0]
    # Fallback: renderizar con navegador (captura emails generados por JS)
    return _headless_email_fallback(url, driver)


def scrape_google_maps(driver, query, location, max_results=20, full_details=False, search_email=False, dedup=True):
    maps_url = f"https://www.google.com/maps/search/{query.replace(' ', '+')}+near+{location.replace(' ', '+')}"
    driver.get(maps_url)
    time.sleep(5)

    new_results = []
    skipped = 0

    try:
        WebDriverWait(driver, 15).until(
            lambda d: len(d.find_elements(By.CSS_SELECTOR, "div[role='article']")) > 0
        )
    except TimeoutException:
        print("No se encontraron resultados.")
        return [], 0

    time.sleep(2)
    articles = driver.find_elements(By.CSS_SELECTOR, "div[role='article']")
    print(f"Total encontrados: {len(articles)}")

    existing = load_existing_results()

    # Cap diario: no capturar mas de MAX_DAILY negocios nuevos al dia
    cap = max_results
    if MAX_DAILY > 0:
        budget = max(0, MAX_DAILY - today_new_count())
        if budget == 0:
            print("Cap diario alcanzado (MAX_DAILY), sin nuevos hoy.")
            return [], 0
        cap = min(cap, budget)

    for i, article in enumerate(articles):
        if len(new_results) >= cap:
            break

        list_detail = extract_from_list(article)
        nombre = list_detail['nombre']

        # Check if already scraped
        if dedup and is_duplicate(nombre, existing):
            skipped += 1
            continue

        print(f"[{i+1}/{len(articles)}] {nombre} (Rating: {list_detail['rating']})")

        if full_details and list_detail.get("href"):
            try:
                driver.execute_script(f"window.open('{list_detail['href']}');")
                time.sleep(1)
                tabs = driver.window_handles
                driver.switch_to.window(tabs[-1])
                time.sleep(3)

                detail = extract_from_detail(driver)
                list_detail.update(detail)

                if search_email and detail.get("email") == "N/A" and detail.get("web") != "N/A":
                    print(f"  Buscando email en web...")
                    email = search_email_on_website(detail.get("web", ""), nombre, driver=driver)
                    list_detail["email"] = email
                    print(f"  Email: {email}")

                driver.close()
                driver.switch_to.window(driver.window_handles[0])
                time.sleep(1)
            except Exception as e:
                print(f"  Error: {e}")
                try:
                    driver.close()
                    driver.switch_to.window(driver.window_handles[0])
                except:
                    pass

        new_results.append(list_detail)

    print(f"\nNuevos: {len(new_results)} | Saltados (ya escaneados): {skipped}")
    return new_results, skipped


def cleanup_old_history():
    """Limpiar historial de mas de DAYS_TO_KEEP dias (0 = nunca limpiar)"""
    if DAYS_TO_KEEP <= 0:
        return
    history = load_history()
    if "scraped" not in history:
        return

    cutoff = datetime.now() - timedelta(days=DAYS_TO_KEEP)
    to_remove = []

    for nombre, data in history["scraped"].items():
        try:
            last_seen = datetime.fromisoformat(data.get("last_seen", ""))
            if last_seen < cutoff:
                to_remove.append(nombre)
        except:
            pass

    for nombre in to_remove:
        del history["scraped"][nombre]

    if to_remove:
        save_history(history)
        print(f"Limpiados {len(to_remove)} resultados antiguos del historial")


def main():
    parser = argparse.ArgumentParser(description="Google Maps Business Scraper")
    parser.add_argument("-q", "--query", default=QUERY, help="Tipo de empresa")
    parser.add_argument("-l", "--location", default=LOCATION, help="Ubicacion")
    parser.add_argument("-m", "--max-results", type=int, default=MAX_RESULTS, help="Maximo resultados")
    parser.add_argument("-o", "--output", choices=["csv", "json", "excel"], default=OUTPUT_FORMAT, help="Formato")
    parser.add_argument("--headed", action="store_true", help="Mostrar navegador")
    parser.add_argument("--full-details", action="store_true", help="Obtener datos completos")
    parser.add_argument("--search-email", action="store_true", help="Buscar email en web")
    parser.add_argument("--no-dedup", action="store_true", help="No evitar duplicados")

    args = parser.parse_args()

    query = args.query
    location = args.location
    max_results = args.max_results
    output_fmt = args.output
    full_details = args.full_details or FULL_DETAILS
    search_email = args.search_email or SEARCH_EMAIL
    headless = not args.headed and HEADLESS

    print(f"{'='*60}")
    print(f"  Google Maps Scraper")
    print(f"  Query: {query}")
    print(f"  Location: {location}")
    print(f"  Max results: {max_results}")
    print(f"  Output: {output_fmt}")
    print(f"  Full details: {full_details}")
    print(f"  Search email: {search_email}")
    print(f"  Dedup: {not args.no_dedup}")
    print(f"{'='*60}\n")

    # Limpiar historial viejo
    cleanup_old_history()

    driver = setup_driver(headless=headless)

    try:
        results, skipped = scrape_google_maps(driver, query, location, max_results, full_details, search_email, dedup=not args.no_dedup)

        if results:
            save_to_file(results, output_fmt, query, location)
            update_last_run(query, location)

            print(f"\n{'='*60}")
            print(f"  Resumen:")
            print(f"  Nuevos resultados: {len(results)}")
            print(f"  Ya escaneados: {skipped}")
            print(f"  Guardado en: {OUTPUT_DIR}")
            print(f"{'='*60}")
        else:
            print("\nNo hay resultados nuevos.")

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        driver.quit()
        print("\nListo.")


if __name__ == "__main__":
    main()
