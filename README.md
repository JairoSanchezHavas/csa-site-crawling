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

## 6. Endpoints de la API

- `GET /health` → Devuelve `{ "status": "ok", "service": "CSA Tech Auditor · Havas", "version": "1.0.0", "playwright_ready": true }`.
- `POST /api/audit-page` → Audita una URL individual con el payload:
  ```json
  {
    "url": "https://example.com",
    "render_mode": "render",
    "timeout_ms": 20000,
    "allow_subdomains": false,
    "wait_until": "networkidle",
    "include_html": false
  }
  ```
- `POST /api/crawl-site` → Rastrea y audita hasta 15 páginas internas en servidor:
  ```json
  {
    "start_url": "https://example.com",
    "max_pages": 15,
    "render_mode": "render",
    "allow_subdomains": false,
    "timeout_per_page_ms": 20000
  }
  ```
- `GET /` → Entrega la SPA `static/index.html`.
