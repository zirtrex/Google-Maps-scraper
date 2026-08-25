# Google Maps Scraper

Automatiza la busqueda de empresas en Google Maps con:
- Busqueda por tipo de empresa y ubicacion
- Extraccion de rating, reviews, telefono, web, email
- Busqueda de email: **crawl estatico (BFS) + fallback headless** (captura emails generados por JS)
- Evita duplicados entre ejecuciones (por nombre, `history.json`)
- **Cap diario** de negocios nuevos (`MAX_DAILY`)
- Resultados guardados por fecha y por query
- Integracion con **n8n + Google Sheets** (lee queries de una hoja, escribe resultados en otra)

## Estructura de archivos

```
google-maps-scraper/
├── Dockerfile
├── docker-compose.yml        # Servicio one-shot por query (env QUERY/LOCATION)
├── requirements.txt
├── google_maps_scraper.py    # Scraper principal (Selenium + email crawl)
├── api-server.py             # API HTTP /run /health para n8n (devuelve filas)
├── scraper-api.service       # Servicio systemd para api-server.py
├── n8n-workflow.json         # Workflow n8n: Sheets -> scraper -> Sheets
├── scraper.sh                # Script por query para cron
├── batch-run.sh              # Ejecuta todas las queries de queries.json
├── crontab-example
├── deploy-guide.md
├── config/
│   └── queries.json          # Configuracion de queries (daily/weekly)
├── output/                   # Resultados Excel/CSV/JSON (por query)
│   └── results_*.xlsx
├── output/test/              # Resultados de prueba (ejemplos)
├── logs/                     # Logs de ejecucion
│   └── run_*.log
└── history.json              # Historial de escaneos (FIJO en la raiz)
```

## Deploy en VPS con EasyPanel

### 1. Subir archivos al VPS
```bash
mkdir -p /opt/google-maps-scraper/{config,output,logs}
cd /opt/google-maps-scraper

# Subir todos los archivos
scp -r * user@tu-vps:/opt/google-maps-scraper/
```

### 2. Configurar queries en `config/queries.json`
```json
{
  "daily": [
    {"query": "restaurantes", "location": "New York", "max_results": 20, "search_email": true},
    {"query": "restaurantes", "location": "Madrid", "max_results": 20, "search_email": true},
    {"query": "gimnasios", "location": "Barcelona", "max_results": 15, "search_email": true}
  ],
  "weekly": [
    {"query": "dentistas", "location": "Madrid", "max_results": 15, "search_email": true},
    {"query": "farmacias", "location": "New York", "max_results": 25, "search_email": false}
  ]
}
```

### 3. Deploy en EasyPanel
- Ir a **Services > Compose**
- Crear servicio `google-maps-scraper`
- Pegar contenido de `docker-compose.yml`
- Guardar e implementar

### 4. Configurar cron en el VPS
```bash
# Editar crontab
crontab -e

# Ejemplo: Ejecutar todos los scripts de config/queries.json
# Diarios a las 8 AM
0 8 * * * /opt/google-maps-scraper/scraper.sh restaurantes "New York" 20 true
0 9 * * * /opt/google-maps-scraper/scraper.sh restaurantes "Madrid" 20 true

# Semanales
0 8 * * 1 /opt/google-maps-scraper/scraper.sh gimnasios "Barcelona" 15 true
0 9 * * 3 /opt/google-maps-scraper/scraper.sh dentistas "Madrid" 15 true
```

### 5. Usar script de batch para ejecutar todas las queries
```bash
# Ejecutar todas las queries diarias
./batch-run.sh daily

# Ejecutar todas las queries semanales
./batch-run.sh weekly
```

## Uso local

```bash
# Instalar dependencias
pip install -r requirements.txt

# Ejecutar
python google_maps_scraper.py -q restaurantes -l "New York" -m 20 -o excel --full-details --search-email

# Sin buscar emails (mas rapido)
python google_maps_scraper.py -q restaurantes -l "New York" -m 20 -o excel

# Sin evitar duplicados
python google_maps_scraper.py -q restaurantes -l "New York" -m 20 --no-dedup
```

## API + n8n + Google Sheets (recomendado)

Arquitectura: **n8n** lee las queries de una hoja, llama al scraper por API y escribe los nuevos en otra hoja.

1. **Ejecutar la API** en el VPS (systemd):
   ```bash
   sudo cp scraper-api.service /etc/systemd/system/
   sudo systemctl enable --now scraper-api
   curl http://localhost:8080/health
   # {"status":"ok","today_new":0,"max_daily":100}
   ```
   - `MAX_DAILY=100` ya esta en el servicio (cap diario global, comparte `history.json`).
2. **Importar `n8n-workflow.json`** en n8n y configurar:
   - En ambos nodos de Sheets: tu **credential Google** y el **ID de la hoja**.
   - En el nodo **Ejecutar scraper**: la URL real de la API (ej. `http://google-maps-scraper-api:8080/run`).
3. **Sheet `Queries`** (1 fila por busqueda): `query | location | max_results | search_email`
4. **Sheet `Results`** (autocompleta): `nombre | rating | reviews | categoria | direccion | telefono | web | email | horario | scraped_at`

> El cap diario lo respeta el scraper: al llegar a `MAX_DAILY` devuelve `daily_cap_reached` y no escribe mas ese dia.

## Yield realista de email

- B2B (abogados, inmobiliarias, clínicas, agencias): **~40%**
- Consumo (restaurantes, etc.): **~20%**
- Muchos negocios solo usan formulario de contacto y no publican email (el fallback headless no lo inventa).
- Con `MAX_DAILY=100` y ~40% de yield -> **~40 emails utiles/dia** (~1.200/mes).

## Variables de entorno

| Variable | Default | Descripcion |
|----------|---------|-------------|
| QUERY | restaurantes | Tipo de empresa |
| LOCATION | New York | Ciudad/ubicacion |
| MAX_RESULTS | 20 | Maximo resultados por query |
| MAX_DAILY | 0 | Cap diario de negocios nuevos (0 = sin limite) |
| OUTPUT_FORMAT | excel | csv/json/excel |
| FULL_DETAILS | true | Abrir cada resultado |
| SEARCH_EMAIL | true | Buscar email en web (estatico + headless) |
| HEADLESS | true | Modo headless |
| DAYS_TO_KEEP | 90 | Dias de historial (0 = no limpiar) |
| OUTPUT_DIR | ./output | Directorio de salida |
| HISTORY_FILE | ./history.json | Ruta FIJA del historial (dedup) |

## Comandos utiles

```bash
# Ver resultados
ls -la output/

# Ver historial
cat history.json

# Ver logs
tail -f logs/run_*.log

# Limpiar historial viejo
python google_maps_scraper.py --cleanup-history

# Reiniciar container
docker-compose restart

# Ver logs del container
docker logs -f google-maps-scraper
```
