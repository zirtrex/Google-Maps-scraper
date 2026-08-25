# Google Maps Scraper - Guia de Deploy en VPS con EasyPanel

## Opcion 1: Deploy con Docker (Recomendado en EasyPanel)

### 1. Preparar archivos en el VPS
```bash
# Crear directorio
mkdir -p /opt/google-maps-scraper
cd /opt/google-maps-scraper

# Subir archivos (Dockerfile, docker-compose.yml, google_maps_scraper.py, requirements.txt)
scp Dockerfile docker-compose.yml google_maps_scraper.py requirements.txt user@tu-vps:/opt/google-maps-scraper/
```

### 2. Deploy en EasyPanel
1. Ir a EasyPanel > Services > Add Service
2. Seleccionar Docker Compose
3. Subir el `docker-compose.yml`
4. Configurar variables de entorno:
   - QUERY: restaurantes
   - LOCATION: Madrid
   - MAX_RESULTS: 20
   - OUTPUT_FORMAT: excel
   - FULL_DETAILS: true
   - SEARCH_EMAIL: true
5. Mapear volumen: `./output:/app/output`
6. Iniciar servicio

### 3. Programar ejecuciones automaticas
EasyPanel no tiene cron integrado, usar metodos alternativos:

**Opcion A: Usar cron del sistema operativo**
```bash
# Editar crontab
crontab -e

# Ejecutar todos los dias a las 9:00 AM
0 9 * * * cd /opt/google-maps-scraper && python google_maps_scraper.py -q restaurantes -l Madrid -m 50 -o excel --full-details --search-email >> /var/log/google-maps-scraper/cron.log 2>&1

# Ejecutar lunes y jueves a las 8:00 AM
0 8 * * 1,4 cd /opt/google-maps-scraper && python google_maps_scraper.py -q gimnasios -l Barcelona -m 30 -o excel --full-details --search-email >> /var/log/google-maps-scraper/cron.log 2>&1
```

**Opcion B: Usar EasyPanel Webhooks + GitHub Actions**
- Crear workflow en GitHub que ejecute el scraper via SSH

**Opcion C: Usar un servicio de monitoring**
- UptimeRobot o Cron-Job.org para hacer ping a un endpoint que dispare el scraper

---

## Opcion 2: Sin Docker (Directo en el VPS)

### 1. Instalar dependencias
```bash
# Ubuntu/Debian
apt-get update
apt-get install -y python3 python3-pip google-chrome-stable

# Instalar dependencias Python
pip3 install -r requirements.txt
```

### 2. Configurar cron
```bash
# Copiar script de ejemplo
cp scraper.sh /opt/google-maps-scraper/scraper.sh
chmod +x /opt/google-maps-scraper/scraper.sh

# Crear directorio de logs
mkdir -p /var/log/google-maps-scraper

# Editar crontab
crontab -e
```

### 3. Ejemplos de cron
```bash
# Diariamente a las 6 AM
0 6 * * * /opt/google-maps-scraper/scraper.sh restaurantes "Madrid" 50 true

# Semanal los lunes a las 8 AM
0 8 * * 1 /opt/google-maps-scraper/scraper.sh gimnasios "Barcelona" 30 true

# Cada 6 horas
0 */6 * * * /opt/google-maps-scraper/scraper.sh farmacias "Barcelona" 20 false
```

---

## Mejoras para Produccion

### 1. Rotacion de logs
```bash
# /etc/logrotate.d/google-maps-scraper
/var/log/google-maps-scraper/*.log {
    daily
    rotate 30
    compress
    missingok
    notifempty
}
```

### 2. Monitoreo con alertas
```bash
# Verificar si el scraper se ejecuto hoy
if [ ! -f /var/log/google-maps-scraper/cron.log ]; then
    echo "Alerta: Scraper no se ejecuto" | mail -s "Alerta Scraper" admin@tuempresa.com
fi
```

### 3. Proxy support (evitar bloqueos)
Agregar al docker-compose.yml:
```yaml
environment:
  - HTTP_PROXY=http://proxy-tuempresa.com:8080
  - HTTPS_PROXY=http://proxy-tuempresa.com:8080
```

O agregar al script:
```python
chrome_options.add_argument('--proxy-server=http://user:pass@proxy:8080')
```

### 4. Base de datos en vez de Excel
Para almacenar historico, cambiar a SQLite/PostgreSQL:
```python
import sqlite3

def save_to_db(results, db_path="scraper.db"):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS results
                      (nombre TEXT, rating REAL, email TEXT, web TEXT, 
                       scraped_at DATETIME DEFAULT CURRENT_TIMESTAMP)''')
    for r in results:
        cursor.execute('INSERT INTO results (nombre, rating, email, web) VALUES (?, ?, ?, ?)',
                      (r['nombre'], r['rating'], r['email'], r['web']))
    conn.commit()
    conn.close()
```

### 5. Docker Compose con reinicio automatico
```yaml
services:
  google-maps-scraper:
    restart: unless-stopped
    deploy:
      restart_policy:
        condition: on-failure
        delay: 30s
        max_attempts: 3
    resources:
      limits:
        memory: 2G
```

---

## Estructura de archivos final
```
/opt/google-maps-scraper/
├── Dockerfile
├── docker-compose.yml
├── google_maps_scraper.py
├── requirements.txt
├── scraper.sh
├── output/
│   └── google_maps_results.xlsx
└── logs/
    └── cron.log
```

---

## Comandos utiles

```bash
# Ver logs en tiempo real
docker logs -f google-maps-scraper

# Reiniciar servicio
docker-compose restart

# Verificar estado
docker ps | grep scraper

# Ver logs de cron
tail -f /var/log/google-maps-scraper/cron.log

# Listar resultados guardados
ls -la /opt/google-maps-scraper/output/

# Limpiar logs antiguos
find /var/log/google-maps-scraper/ -name "*.log" -mtime +7 -delete
```
