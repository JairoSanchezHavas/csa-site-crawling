# CSA Tech Auditor · Havas
> **Digital Analytics, Tag Governance & SPA Crawler Platform**  
> Modernized Cloud-Native architecture built with FastAPI, Playwright (Headless Chromium) and Google Cloud Run.

---

## 1. Visión General del Proyecto
**CSA Tech Auditor · Havas** es una plataforma de auditoría de analítica digital y gobernanza de etiquetas diseñada para auditar hasta 15 páginas internas de un sitio web. Inspecciona en profundidad:
- Contenedores de **Google Tag Manager (GTM)** y etiquetas activas.
- Píxeles y hits de **Google Analytics 4 (GA4)** y Universal Analytics.
- Píxeles publicitarios de **Meta (Facebook)**, **TikTok** y **LinkedIn**.
- Plataformas de Gestión de Consentimiento (**CMPs**) como OneTrust, Cookiebot, Didomi, TrustArc y Usercentrics.
- **`window.dataLayer`** real evaluado en la memoria del navegador tras renderizar JavaScript.
- Cabeceras de seguridad web (`HSTS`, `CSP`, `X-Frame-Options`, `Referrer-Policy`).

---

## 2. Resolución de las 3 Limitaciones Críticas

| Limitación Original | Problema Anterior | Solución Implementada en esta Versión |
| :--- | :--- | :--- |
| **1. Proxy público de terceros** | Dependencia frágil de un servicio proxy externo no corporativo, riesgo de interrupción de servicio, falta de SLAs y exposición de tráfico a intermediarios no confiables. | **Backend propio contenerizado en Google Cloud Run**: FastAPI asíncrono con `CORSMiddleware` configurable, protección estricta Anti-SSRF y pre-resolución DNS en hilo seguro. Cero proxies externos. |
| **2. Análisis solo estático** | Incapacidad de auditar Single Page Applications modernas (React, Vue, Angular, Next.js). `window.dataLayer` y eventos de red disparados asíncronamente eran invisibles en el HTML crudo. | **Playwright + Headless Chromium**: Motor de renderizado dinámico con intercepción nativa de requests/responses a nivel de red, espera de estabilización (`networkidle`/`domcontentloaded`) y evaluación de `window.dataLayer` en memoria. Cuenta además con un *Modo Rápido* (`httpx`) como fallback de alta velocidad. |
| **3. Redirecciones y subdominios** | Pérdida de contexto durante redirecciones (ej. HTTP ➔ HTTPS o `dominio.com` ➔ `www.dominio.com`), enlaces descartados erróneamente por fallos en `sameOrigin`. | **Normalización canónica y de origen base**: Detección del dominio final tras la primera redirección. Tratamiento automático de `dominio.com` y `www.dominio.com` como el mismo origen base. Flag configurable (`allow_subdomains`) para incluir subdominios hermanos del apex domain. |

---

## 3. Arquitectura del Sistema

```text
┌────────────────────────────────────────────────────────────────────────┐
│                          CLIENTE (SPA / Browser)                        │
│             static/index.html · "CSA Tech Auditor · Havas"             │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ HTTP Requests (/api/*, /health)
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                 BACKEND FASTAPI (Google Cloud Run)                     │
│                                                                        │
│  ┌─────────────────────────┐          ┌─────────────────────────────┐  │
│  │   Anti-SSRF Firewall    │          │     Lifespan Controller     │  │
│  │ (DNS Pre-flight check,  │          │  (Playwright Singleton Pool,│  │
│  │ private IP & meta CIDR) │          │   Clean Resource Recycle)   │  │
│  └────────────┬────────────┘          └──────────────┬──────────────┘  │
│               │                                      │                 │
│               ▼                                      ▼                 │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │                       ScraperEngine                              │  │
│  │                                                                  │  │
│  │   ┌─────────────────────────────┐  ┌──────────────────────────┐  │  │
│  │   │   Modo Render (Playwright)  │  │   Modo Rápido (httpx)    │  │  │
│  │   │  - Headless Chromium        │  │  - Peticiones HTTP async │  │  │
│  │   │  - Network Interception     │  │  - Parsing de scripts    │  │  │
│  │   │  - window.dataLayer in-mem  │  │  - Detección estática    │  │  │
│  │   │  - Cookie extraction        │  │  - Fallback resiliente   │  │  │
│  │   └─────────────────────────────┘  └──────────────────────────┘  │  │
│  └──────────────────────────────────────────────────────────────────┘  │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ Navegación Segura Saliente
                                    ▼
                     Sitio Web Auditado (Target Webpage)
```

---

## 4. Decisión de Diseño: `/api/audit-page` vs `/api/crawl-site`

El backend implementa **dos estrategias complementarias**:

1. **`POST /api/audit-page` (Operación Atómica - Preferida por el Frontend SPA):**
   - **Por qué:** Permite a la SPA realizar un crawl progresivo mostrando el avance **en tiempo real** (página 1 de 15, página 2 de 15, etc.) con barra de progreso interactiva y feedback visual inmediato si una página tarda o falla.
   - **Resiliencia:** Si la página #8 tarda 15 segundos o arroja un error 404, la SPA registra el fallo y continúa de inmediato con la página #9 sin abortar el crawl global.

2. **`POST /api/crawl-site` (Operación Batch de Servidor):**
   - **Por qué:** Ideal para automatizaciones headless, pipelines de CI/CD o llamadas de API externas donde un cliente no requiere renderizado progresivo y prefiere recibir el reporte consolidado de las 15 páginas en un único payload JSON.

---

## 5. Variables de Entorno

| Variable | Tipo | Default | Descripción |
| :--- | :--- | :--- | :--- |
| `PORT` | Entero | `8080` | Puerto en el que escucha Uvicorn (inyectado automáticamente por Cloud Run). |
| `HOST` | String | `0.0.0.0` | Host IP de enlace del servidor. |
| `ALLOWED_ORIGINS` | String | `*` | Orígenes CORS permitidos (separados por coma o `*` para todos). |
| `DEFAULT_TIMEOUT_MS` | Entero | `20000` | Timeout por defecto en milisegundos para auditorías Playwright (20 s). |
| `MAX_CRAWL_PAGES` | Entero | `15` | Límite máximo de páginas a rastrear por sitio web. |
| `PLAYWRIGHT_HEADLESS`| Booleano| `true` | Ejecutar Chromium en modo headless sin ventana gráfica. |
| `ENVIRONMENT` | String | `production` | Entorno de despliegue (`production`, `staging`, `development`). |
| `DEBUG` | Booleano| `false` | Activa logs verbosos de depuración en FastAPI. |

---

## 6. Endpoints de la API y Ejemplos de Respuesta

### `GET /health`
Verifica el estado del servicio y si Playwright está listo.
- **Respuesta (HTTP 200):**
  ```json
  {
    "status": "ok",
    "service": "CSA Tech Auditor · Havas",
    "version": "1.0.0",
    "playwright_ready": true
  }
  ```

---

### `GET /api/audit-page` (Acceso Rápido por URL)
Audita una página pasando únicamente el parámetro `url` en la Query String del navegador o curl, con los parámetros pre-cargados por defecto:
- `render_mode`: `"render"`
- `timeout_ms`: `20000`
- `allow_subdomains`: `false`
- `wait_until`: `"networkidle"`
- `include_html`: `true`

**Ejemplo de uso:**
```text
GET /api/audit-page?url=https://www.nespresso.com/mx/es
```
o en curl:
```bash
curl -X GET "https://TU-URL/api/audit-page?url=https://www.nespresso.com/mx/es"
```

---

### `POST /api/audit-page`
Audita a fondo una página individual. En **Modo Render** (`render`), levanta Playwright Chromium, ejecuta el JavaScript dinámico de SPAs, extrae `window.dataLayer` en memoria y captura hits de red en tiempo real. Permite personalizar cualquiera de los parámetros en el body JSON.

- **Petición (Request):**
  ```json
  {
    "url": "https://example.com",
    "render_mode": "render",
    "timeout_ms": 20000,
    "allow_subdomains": false,
    "wait_until": "networkidle",
    "include_html": true
  }
  ```

- **Respuesta de Ejemplo (Response HTTP 200):**
  ```json
  {
    "success": true,
    "url": "https://example.com",
    "final_url": "https://www.example.com/",
    "http_status": 200,
    "title": "Example Domain - Tienda Oficial",
    "canonical_url": "https://www.example.com/",
    "is_same_origin": true,
    "render_mode_used": "render",
    "load_time_ms": 1420.35,
    "detected_cmps": [
      {
        "name": "OneTrust",
        "detected_by": "cookie",
        "details": "Cookie: OptanonConsent"
      },
      {
        "name": "OneTrust",
        "detected_by": "global_var",
        "details": "window.OneTrust present in browser memory"
      }
    ],
    "data_layer": [
      {
        "event": "gtm.js",
        "gtm.start": 1728381200100
      },
      {
        "event": "consent",
        "analytics_storage": "granted",
        "ad_storage": "denied"
      },
      {
        "event": "page_view",
        "page_title": "Home",
        "page_location": "https://www.example.com/",
        "user_logged": false
      }
    ],
    "network_hits": [
      {
        "category": "gtm",
        "url": "https://www.googletagmanager.com/gtm.js?id=GTM-XXXXXX",
        "method": "GET",
        "status": 200,
        "resource_type": "script",
        "timestamp_ms": 320.4
      },
      {
        "category": "ga4",
        "url": "https://region1.google-analytics.com/g/collect?v=2&tid=G-YYYYYYY&en=page_view",
        "method": "POST",
        "status": 204,
        "resource_type": "fetch",
        "timestamp_ms": 890.1
      },
      {
        "category": "meta",
        "url": "https://www.facebook.com/tr/?id=123456789&ev=PageView&dl=https%3A%2F%2Fwww.example.com%2F",
        "method": "GET",
        "status": 200,
        "resource_type": "image",
        "timestamp_ms": 940.6
      }
    ],
    "internal_links": [
      "https://www.example.com/nosotros",
      "https://www.example.com/productos",
      "https://www.example.com/contacto"
    ],
    "cookies": [
      {
        "name": "OptanonConsent",
        "domain": ".example.com",
        "path": "/",
        "secure": true,
        "http_only": false,
        "same_site": "Lax"
      },
      {
        "name": "_ga",
        "domain": ".example.com",
        "path": "/",
        "secure": false,
        "http_only": false,
        "same_site": null
      }
    ],
    "security_headers": {
      "strict-transport-security": "max-age=31536000; includeSubDomains",
      "content-security-policy": "default-src 'self' https:;",
      "x-frame-options": "SAMEORIGIN",
      "x-content-type-options": "nosniff",
      "referrer-policy": "strict-origin-when-cross-origin"
    },
    "html_preview": "<!DOCTYPE html><html lang=\"es\"><head><title>Example Domain</title>...",
    "html": "<!DOCTYPE html><html lang=\"es\"><head>...<!-- DOM completo generado por React/Next tras JS -->...</body></html>",
    "error": null
  }
  ```

---

### `POST /api/crawl-site`
Rastrea recursivamente hasta 15 páginas internas del sitio en el servidor a partir de los enlaces descubiertos.

- **Petición (Request):**
  ```json
  {
    "start_url": "https://example.com",
    "max_pages": 3,
    "render_mode": "render",
    "allow_subdomains": false,
    "timeout_per_page_ms": 20000
  }
  ```

- **Respuesta de Ejemplo (Response HTTP 200):**
  ```json
  {
    "start_url": "https://example.com",
    "final_base_url": "https://www.example.com/",
    "total_pages_audited": 3,
    "duration_ms": 4850.2,
    "results": [
      {
        "success": true,
        "url": "https://example.com",
        "final_url": "https://www.example.com/",
        "http_status": 200,
        "title": "Home - Example",
        "canonical_url": "https://www.example.com/",
        "is_same_origin": true,
        "render_mode_used": "render",
        "load_time_ms": 1420.35,
        "detected_cmps": [
          { "name": "OneTrust", "detected_by": "cookie", "details": "Cookie: OptanonConsent" }
        ],
        "data_layer": [
          { "event": "page_view", "page_title": "Home" }
        ],
        "network_hits": [
          { "category": "gtm", "url": "https://www.googletagmanager.com/gtm.js?id=GTM-XXXXXX" },
          { "category": "ga4", "url": "https://region1.google-analytics.com/g/collect?v=2&tid=G-YYYYYYY" }
        ],
        "internal_links": [
          "https://www.example.com/nosotros",
          "https://www.example.com/productos"
        ],
        "cookies": [
          { "name": "_ga", "domain": ".example.com", "path": "/", "secure": false, "http_only": false, "same_site": null }
        ],
        "security_headers": {
          "strict-transport-security": "max-age=31536000"
        },
        "error": null
      },
      {
        "success": true,
        "url": "https://www.example.com/nosotros",
        "final_url": "https://www.example.com/nosotros",
        "http_status": 200,
        "title": "Quiénes Somos - Example",
        "canonical_url": "https://www.example.com/nosotros",
        "is_same_origin": true,
        "render_mode_used": "render",
        "load_time_ms": 1150.12,
        "detected_cmps": [
          { "name": "OneTrust", "detected_by": "cookie", "details": "Cookie: OptanonConsent" }
        ],
        "data_layer": [
          { "event": "page_view", "page_title": "Nosotros" }
        ],
        "network_hits": [
          { "category": "ga4", "url": "https://region1.google-analytics.com/g/collect?v=2&tid=G-YYYYYYY" }
        ],
        "internal_links": [
          "https://www.example.com/",
          "https://www.example.com/contacto"
        ],
        "cookies": [],
        "security_headers": {
          "strict-transport-security": "max-age=31536000"
        },
        "error": null
      },
      {
        "success": true,
        "url": "https://www.example.com/productos",
        "final_url": "https://www.example.com/productos",
        "http_status": 200,
        "title": "Catálogo de Productos - Example",
        "canonical_url": "https://www.example.com/productos",
        "is_same_origin": true,
        "render_mode_used": "render",
        "load_time_ms": 1680.45,
        "detected_cmps": [
          { "name": "OneTrust", "detected_by": "cookie", "details": "Cookie: OptanonConsent" }
        ],
        "data_layer": [
          { "event": "view_item_list", "item_list_name": "Destacados" }
        ],
        "network_hits": [
          { "category": "meta", "url": "https://www.facebook.com/tr/?id=123456789&ev=ViewContent" },
          { "category": "ga4", "url": "https://region1.google-analytics.com/g/collect?v=2&tid=G-YYYYYYY" }
        ],
        "internal_links": [
          "https://www.example.com/"
        ],
        "cookies": [],
        "security_headers": {
          "strict-transport-security": "max-age=31536000"
        },
        "error": null
      }
    ]
  }
  ```

---

### `GET /`
- **Función:** Entrega la interfaz Single Page Application (`static/index.html`).
- **Nota:** No audita sitios por sí mismo; es el dashboard web corporativo desde el cual el usuario interactúa para lanzar las auditorías contra `/api/audit-page` en tiempo real.
