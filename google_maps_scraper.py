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


UI_TEXT_STOPWORDS = {
    "directions", "save", "saved", "save to lists", "share", "share place",
    "nearby", "search nearby", "send to phone", "suggest an edit", "update",
    "updated", "edit", "overview", "reviews", "menu", "services", "photos",
    "photo", "popular times", "write a review", "order online", "book online",
    "see more", "show more", "see all", "learn more", "all", "less",
    "get directions", "plan route", "claim this business", "own this business",
    "check history", "call", "website", "address", "hours", "email", "profile",
    "sponsored", "ad", "new",
}

ADDR_STREET_RE = re.compile(
    r"\b\d{1,6}\s+[A-Za-z0-9 .'-]*\b"
    r"(St|St\.|Ave|Ave\.|Avenue|Blvd|Blvd\.|Rd|Rd\.|Way|Dr|Dr\.|Ln|Ln\.|Pl|Pl\.|"
    r"Sq|Sq\.|Pkwy|Terrace|Ter|Ct|Cir|Hwy|Broadway|Boulevard|Lane|Drive|Road|"
    r"Street|Parkway|Circle|Court|Square|Place)\b",
    re.I,
)


def _is_category_text(text):
    """Heuristica: el texto parece una categoria de negocio y no UI de Google."""
    t = text.strip()
    if not (3 <= len(t) <= 60):
        return False
    if not re.match(r"^[A-Za-z0-9&()/' -]+$", t):
        return False
    if sum(c.isalpha() for c in t) < 2:
        return False
    if t.lower() in UI_TEXT_STOPWORDS:
        return False
    if t.startswith(("Open", "Closed", "Opens", "Closes", "Abierto", "Cerrado", "Serves")):
        return False
    return True


def _find_by_aria(scope, label):
    for el in scope.find_elements(By.CSS_SELECTOR, "button, a, div[role='button']"):
        al = (el.get_attribute("aria-label") or "").strip().lower()
        if al == label:
            return el
    return None


def extract_from_list(article):
    """Extract basic info from the list view"""
    detail = {}
    spans = article.find_elements(By.TAG_NAME, "span")
    texts = [s.text.strip() for s in spans]

    # Name
    name = next((t for t in texts if t), "")
    detail["nombre"] = name if name else "N/A"

    # Rating
    detail["rating"] = "N/A"
    detail["reviews"] = "N/A"
    rating_idx = -1
    for idx, text in enumerate(texts):
        m = re.match(r"^(\d\.\d)\s*\(([\d,.]+)\)$", text)
        if m:
            detail["rating"] = m.group(1)
            detail["reviews"] = m.group(2).replace(",", "")
            rating_idx = idx
            break
        if re.match(r"^\d\.\d$", text):
            detail["rating"] = text
            rating_idx = idx
            break

    # Reviews, ej. (204)
    if detail["reviews"] == "N/A":
        for text in texts:
            m = re.match(r"^\(([\d,.]+)\)$", text)
            if m:
                detail["reviews"] = m.group(1).replace(",", "")
                break
    # Reviews: "247 reviews" / "247 opiniones"
    if detail["reviews"] == "N/A":
        for text in texts:
            m = re.match(r"^([\d][\d,.]*)\s+(?:reviews?|opiniones?)$", text, re.I)
            if m:
                detail["reviews"] = m.group(1).replace(",", "")
                break

    # Category: primer texto plausible despues del rating
    detail["categoria"] = "N/A"
    start = rating_idx + 1 if rating_idx >= 0 else 1
    for text in texts[start:]:
        if text and text != name and _is_category_text(text):
            detail["categoria"] = text
            break

    # Address (fila con calle en la lista)
    detail["direccion"] = "N/A"
    for text in texts:
        if len(text) < 150 and "," in text and ADDR_STREET_RE.search(text):
            detail["direccion"] = text
            break

    # Get the link
    links = article.find_elements(By.TAG_NAME, "a")
    detail["href"] = links[0].get_attribute("href") if links else ""

    return detail


def extract_from_detail(driver):
    """Extract full details del panel de detalle (div[role='main']), no de toda la pagina."""
    try:
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "div[role='main'] h1"))
        )
    except TimeoutException:
        return {}

    main = driver.find_element(By.CSS_SELECTOR, "div[role='main']")
    detail = {}

    # Nombre oficial del panel
    try:
        name = main.find_element(By.TAG_NAME, "h1").text.strip()
        if name:
            detail["nombre"] = name
    except Exception:
        pass

    spans = main.find_elements(By.TAG_NAME, "span")
    links = main.find_elements(By.TAG_NAME, "a")
    texts = [s.text.strip() for s in spans]

    # Rating y reviews
    detail["rating"] = "N/A"
    detail["reviews"] = "N/A"
    for text in texts:
        m = re.match(r"^(\d\.\d)\s*\(([\d,.]+)\)$", text)
        if m:
            detail["rating"] = m.group(1)
            detail["reviews"] = m.group(2).replace(",", "")
            break
    if detail["rating"] == "N/A":
        for text in texts:
            if re.match(r"^\d\.\d$", text):
                detail["rating"] = text
                break
    if detail["reviews"] == "N/A":
        for text in texts:
            m = re.match(r"^\(([\d,.]+)\)$", text)
            if m:
                detail["reviews"] = m.group(1).replace(",", "")
                break
    if detail["reviews"] == "N/A":
        for text in texts:
            m = re.match(r"^([\d][\d,.]*)\s+reviews?$", text, re.I)
            if m:
                detail["reviews"] = m.group(1).replace(",", "")
                break

    # Category: chip bajo el nombre
    detail["categoria"] = "N/A"
    for el in main.find_elements(By.CSS_SELECTOR, "button, a, div[role='button']"):
        t = el.text.strip()
        if _is_category_text(t):
            detail["categoria"] = t
            break

    # Address
    detail["direccion"] = "N/A"
    addr_el = _find_by_aria(main, "address")
    if addr_el and addr_el.text.strip():
        detail["direccion"] = addr_el.text.strip()
    if detail["direccion"] == "N/A":
        for text in texts:
            if text.startswith("Serves ") and len(text) < 100:
                detail["direccion"] = text
                break
    if detail["direccion"] == "N/A":
        for text in texts:
            if len(text) < 150 and "," in text and ADDR_STREET_RE.search(text):
                detail["direccion"] = text
                break

    # Phone
    detail["telefono"] = "N/A"
    for link in links:
        href = link.get_attribute("href")
        if href and href.startswith("tel:"):
            detail["telefono"] = href.replace("tel:", "").replace(" ", "")
            break

    # Website
    detail["web"] = "N/A"
    web_el = _find_by_aria(main, "website")
    if web_el:
        href = web_el.get_attribute("href") or ""
        if not href:
            for sub in web_el.find_elements(By.TAG_NAME, "a"):
                href = sub.get_attribute("href") or ""
                if href:
                    break
        if href.startswith("http"):
            detail["web"] = href
    if detail["web"] == "N/A":
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

    # Hours (UI en ingles: Open/Closed, con fallback espanol)
    detail["horario"] = "N/A"
    hours_el = _find_by_aria(main, "hours")
    if hours_el and hours_el.text.strip():
        line = hours_el.text.strip().split("\n")[0].replace("\u22c5", " ").strip(" -")
        if line:
            detail["horario"] = line
    if detail["horario"] == "N/A":
        for text in texts:
            if len(text) < 60 and text.startswith(("Open", "Closed", "Abierto", "Cerrado")):
                detail["horario"] = text
                break

    # Email from Google Maps
    detail["email"] = "N/A"
    for link in links:
        href = link.get_attribute("href")
        if href and "mailto:" in href:
            e = _valid_email(href.replace("mailto:", "").split("?")[0])
            if e:
                detail["email"] = e
                break
    if detail["email"] == "N/A":
        for link in links:
            m = re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', link.text.strip())
            if m:
                detail["email"] = link.text.strip()
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
        WebDriverWait(driver, 30).until(
            lambda d: len(d.find_elements(By.CSS_SELECTOR, "div[role='article']")) > 0
        )
    except TimeoutException:
        # Reintento: en el VPS a veces Google tarda mas o sirve una pagina intermedia
        print("No cargaron resultados a la primera, reintentando...")
        driver.get(maps_url)
        time.sleep(5)
        try:
            WebDriverWait(driver, 30).until(
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
                driver.execute_script("window.open(arguments[0]);", list_detail["href"])
                time.sleep(1)
                tabs = driver.window_handles
                driver.switch_to.window(tabs[-1])

                detail = extract_from_detail(driver)
                # Solo sobrescribir con valores reales del panel (no con "N/A")
                for k, v in detail.items():
                    if v not in (None, "", "N/A"):
                        list_detail[k] = v

                if (search_email
                        and list_detail.get("email") in (None, "N/A")
                        and list_detail.get("web") not in (None, "N/A")):
                    print(f"  Buscando email en web...")
                    email = search_email_on_website(list_detail.get("web", ""), nombre, driver=driver)
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
