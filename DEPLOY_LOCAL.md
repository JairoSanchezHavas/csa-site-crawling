# Guía de Despliegue Local · CSA Tech Auditor

Esta guía describe cómo ejecutar y probar localmente el backend y la SPA de **CSA Tech Auditor · Havas**, tanto en entorno virtual Python como mediante Docker Compose.

---

## Opción 1: Ejecución en Entorno Virtual Python (venv)

### 1.1 Prerrequisitos
- Python **3.11** instalado (`python --version` debe mostrar 3.11.x).
- Acceso a internet para descargar paquetes y binarios de Chromium.

### 1.2 Crear y Activar el Entorno Virtual

En Linux / macOS:
```bash
python3.11 -m venv .venv
source .venv/bin/activate
```

En Windows (PowerShell):
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

### 1.3 Instalar Dependencias
```bash
pip install --upgrade pip
pip install -r requirements.txt
pip install -r requirements-dev.txt
```

### 1.4 Instalar Binarios de Chromium para Playwright
```bash
playwright install chromium --with-deps
```

### 1.5 Ejecutar la Suite de Pruebas Unitarias
```bash
pytest -v
```

### 1.6 Iniciar el Servidor de Desarrollo con Uvicorn
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
```

Una vez iniciado:
- Accede a la SPA en tu navegador: [http://localhost:8080](http://localhost:8080)
- Accede a la documentación interactiva Swagger: [http://localhost:8080/docs](http://localhost:8080/docs)

---

## Opción 2: Ejecución con Docker Compose

Docker Compose compila la imagen con Ubuntu Jammy, Playwright v1.44 y Chromium listo para producción con usuario no-root.

### 2.1 Construir y Levantar el Contenedor
```bash
docker compose up --build
```

Para ejecutarlo en segundo plano (daemon):
```bash
docker compose up -d --build
```

### 2.2 Ver Logs en Vivo
```bash
docker compose logs -f csa-auditor
```

### 2.3 Detener el Servicio
```bash
docker compose down
```

---

## Ejemplos Prácticos con `curl`

### 1. Healthcheck
Verifica que el servicio y el motor Playwright están listos:
```bash
curl -X GET http://localhost:8080/health
```
**Respuesta esperada (HTTP 200):**
```json
{
  "status": "ok",
  "service": "CSA Tech Auditor · Havas",
  "version": "1.0.0",
  "playwright_ready": true
}
```

---

### 2. Validación de Seguridad Anti-SSRF (Bloqueo de IPs Privadas y Metadatos)
Prueba que una petición dirigida a `localhost` o a la IP de metadatos de GCP es bloqueada inmediatamente:

```bash
# Intento de ataque contra localhost
curl -X POST http://localhost:8080/api/audit-page \
  -H "Content-Type: application/json" \
  -d '{"url": "http://127.0.0.1:8080/admin", "render_mode": "fast"}'
```
**Respuesta esperada (HTTP 400 Bad Request):**
```json
{
  "detail": "Security/SSRF validation rejected target URL: Access to IP '127.0.0.1' is blocked by anti-SSRF policy (private/reserved IP)"
}
```

```bash
# Intento de ataque contra el endpoint de metadatos de Google Cloud / AWS
curl -X POST http://localhost:8080/api/audit-page \
  -H "Content-Type: application/json" \
  -d '{"url": "http://169.254.169.254/computeMetadata/v1/", "render_mode": "fast"}'
```
**Respuesta esperada (HTTP 400 Bad Request):**
```json
{
  "detail": "Security/SSRF validation rejected target URL: Access to IP '169.254.169.254' is blocked by anti-SSRF policy (private/reserved IP)"
}
```

---

### 3. Auditar una Página con Modo Render (Playwright)

#### Opción A: Vía GET (Acceso Rápido por URL con pre-cargado de render y HTML completo)
```bash
curl -X GET "http://localhost:8080/api/audit-page?url=https://example.com"
```
*(También puedes abrir `http://localhost:8080/api/audit-page?url=https://example.com` directamente en tu navegador).*

#### Opción B: Vía POST (Payload JSON personalizable)
Audita una página pública con ejecución completa de JavaScript y captura de `dataLayer`:

```bash
curl -X POST http://localhost:8080/api/audit-page \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://example.com",
    "render_mode": "render",
    "timeout_ms": 20000,
    "allow_subdomains": false,
    "wait_until": "networkidle",
    "include_html": false
  }'
```

---

### 4. Auditar una Página en Modo Rápido (HTTP Fallback)
```bash
curl -X POST http://localhost:8080/api/audit-page \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://example.com",
    "render_mode": "fast",
    "timeout_ms": 10000
  }'
```

---

### 5. Rastrear hasta 15 Páginas de un Sitio (Crawl Batch)
```bash
curl -X POST http://localhost:8080/api/crawl-site \
  -H "Content-Type: application/json" \
  -d '{
    "start_url": "https://example.com",
    "max_pages": 5,
    "render_mode": "render",
    "allow_subdomains": true,
    "timeout_per_page_ms": 15000
  }'
```
