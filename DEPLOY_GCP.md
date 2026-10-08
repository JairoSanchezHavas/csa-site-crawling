# Guía de Despliegue en Google Cloud Run vía Cloud Shell
> **Proyecto:** `csa-site-crawling` · **CSA Tech Auditor · Havas**  
> **Repositorio GitHub:** `https://github.com/JairoSanchezHavas/csa-site-crawling`

Esta guía describe el paso a paso exacto para desplegar el backend y la SPA en **Google Cloud Run** utilizando **Google Cloud Shell** directamente desde el navegador, clonando el repositorio público de GitHub sin necesidad de instalar dependencias ni herramientas locales en tu equipo.

---

## 1. Ventajas de Desplegar con Google Cloud Shell

- **Cero instalación local:** No necesitas tener instalado `gcloud`, Docker, Python ni Playwright en tu máquina de desarrollo.
- **Entorno preconfigurado:** Cloud Shell ya incluye `git`, `gcloud`, Docker y credenciales de autenticación activas vinculadas a tu cuenta de Google Cloud.
- **Conectividad directa:** La subida de imágenes a Artifact Registry se realiza a través de la red interna de Google, siendo ultra rápida.

---

## 2. Abrir Google Cloud Shell y Clonar el Proyecto

1. Entra a la consola de Google Cloud: [https://console.cloud.google.com/](https://console.cloud.google.com/)
2. Abre **Cloud Shell** haciendo clic en el icono **`>_`** en la barra superior o accede directamente a [https://shell.cloud.google.com/](https://shell.cloud.google.com/).
3. Clona el repositorio público y entra a la carpeta:

```bash
git clone https://github.com/JairoSanchezHavas/csa-site-crawling.git
cd csa-site-crawling
```

*(Si ya habías clonado el repositorio anteriormente en Cloud Shell y deseas actualizarlo con los últimos cambios:)*
```bash
git pull origin main
```

---

## 3. Definir Variables del Entorno en Cloud Shell

Configura las variables base para tu despliegue. Puedes utilizar tu proyecto activo actual automáticamente o ingresar uno específico:

```bash
# Detectar el proyecto activo en Cloud Shell o asignar uno manualmente
export PROJECT_ID=$(gcloud config get-value project)
# Si deseas especificar otro proyecto distinto al actual, descomenta:
# export PROJECT_ID="tu-id-de-proyecto-gcp"

export REGION="us-central1"
export SERVICE_NAME="csa-tech-auditor"
export REPO_NAME="csa-docker-repo"
export IMAGE_TAG="1.0.0"

# Confirmar configuración en gcloud
gcloud config set project $PROJECT_ID
gcloud config set run/region $REGION

echo "Desplegando en Proyecto: $PROJECT_ID | Región: $REGION"
```

---

## 4. Habilitar las APIs de Google Cloud

Asegúrate de que los servicios necesarios estén activados en el proyecto:

```bash
gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com
```

---

## 5. Crear el Repositorio en Artifact Registry

Crea el registro de contenedores Docker en tu región si aún no existe:

```bash
# Crear repositorio (solo la primera vez)
gcloud artifacts repositories create $REPO_NAME \
  --repository-format=docker \
  --location=$REGION \
  --description="Repositorio Docker para CSA Tech Auditor" \
  2>/dev/null || echo "El repositorio ya existe, continuando..."
```

---

## 6. Construir la Imagen con Google Cloud Build

Compilamos la imagen Docker directamente en la infraestructura serverless de GCP:

```bash
# Definir la URL canónica de la imagen en Artifact Registry
export IMAGE_URL="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/${SERVICE_NAME}:${IMAGE_TAG}"

echo "Construyendo imagen: $IMAGE_URL"
gcloud builds submit --tag $IMAGE_URL .
```

*Cloud Build compilará el Dockerfile, instalará las dependencias fijadas y configurará Playwright v1.44 con Chromium listo para producción.*

---

## 7. Desplegar en Google Cloud Run

Desplegamos el contenedor especificando los recursos recomendados para Playwright headless (**2 GiB de RAM y 2 vCPUs**) con concurrencia controlada para evitar saturación de memoria durante auditorías simultáneas:

```bash
gcloud run deploy $SERVICE_NAME \
  --image=$IMAGE_URL \
  --platform=managed \
  --region=$REGION \
  --memory=2Gi \
  --cpu=2 \
  --concurrency=10 \
  --timeout=180s \
  --min-instances=0 \
  --max-instances=10 \
  --allow-unauthenticated \
  --set-env-vars="ENVIRONMENT=production,ALLOWED_ORIGINS=*,DEFAULT_TIMEOUT_MS=20000,MAX_CRAWL_PAGES=15,PLAYWRIGHT_HEADLESS=true"
```

### Parámetros Explicados:
- `--memory=2Gi` y `--cpu=2`: Recursos necesarios para que Chromium ejecute JavaScript pesado y renderice SPAs sin errores OOM (*Out Of Memory*).
- `--concurrency=10`: Límite de solicitudes concurrentes por instancia antes de autogestionar escalado horizontal.
- `--timeout=180s`: Margen de tiempo suficiente para rastreos de hasta 15 páginas internas.
- `--min-instances=0`: Escala a 0 para no generar costes fijos mientras no se use la herramienta.
- `--allow-unauthenticated`: Permite acceder al dashboard web públicamente. Si requieres acceso restringido corporativo (IAM / SSO de Google), omite este parámetro.

---

## 8. Verificación y Pruebas en Cloud Shell

Al finalizar el despliegue, Cloud Run te mostrará la URL pública del servicio. Puedes obtenerla y probarla directamente desde Cloud Shell:

```bash
export SERVICE_URL=$(gcloud run services describe $SERVICE_NAME --platform=managed --region=$REGION --format='value(status.url)')
echo "Servicio disponible en: $SERVICE_URL"
```

### 8.1 Probar Healthcheck
```bash
curl -s -X GET "${SERVICE_URL}/health" | jq .
```
**Respuesta esperada:**
```json
{
  "status": "ok",
  "service": "CSA Tech Auditor · Havas",
  "version": "1.0.0",
  "playwright_ready": true
}
```

### 8.2 Probar Auditoría con Renderizado Playwright

**Opción A: Vía GET (Acceso Rápido desde navegador o curl con parámetros pre-cargados):**
```bash
curl -s -X GET "${SERVICE_URL}/api/audit-page?url=https://example.com" | jq '{success, final_url, http_status, render_mode_used, data_layer: .data_layer | length, hits: .network_hits | length}'
```
*(O accede directamente en tu navegador a: `${SERVICE_URL}/api/audit-page?url=https://example.com`)*

**Opción B: Vía POST (Payload JSON personalizable):**
```bash
curl -s -X POST "${SERVICE_URL}/api/audit-page" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://example.com",
    "render_mode": "render",
    "wait_until": "networkidle"
  }' | jq '{success, final_url, http_status, render_mode_used, data_layer: .data_layer | length, hits: .network_hits | length}'
```

### 8.3 Probar el Bloqueo de Ataques SSRF
```bash
curl -s -X POST "${SERVICE_URL}/api/audit-page" \
  -H "Content-Type: application/json" \
  -d '{"url": "http://169.254.169.254/computeMetadata/v1/", "render_mode": "fast"}' | jq .
```
*Debe responder HTTP 400 rechazando el intento de acceso a metadatos internos de Google Cloud.*

### 8.4 Acceder a la Interfaz Web (SPA)
Abre en tu navegador la URL de `$SERVICE_URL` (por ejemplo `https://csa-tech-auditor-xxxxx-uc.a.run.app`). Verás el dashboard de **"CSA Tech Auditor · Havas"** listo para auditar sitios web.

---

## 9. Mapeo de Dominio Personalizado (Opcional)

Si deseas apuntar tu dominio corporativo (ej. `auditor.csa.havas.com`):

```bash
gcloud beta run domain-mappings create \
  --service=$SERVICE_NAME \
  --domain=auditor.csa.havas.com \
  --region=$REGION
```
Agrega los registros DNS `CNAME` o `A` que te indique el comando en la zona DNS corporativa.

---

## 10. Consultar Logs en Vivo desde Cloud Shell

Para monitorear el comportamiento de Playwright y rastreos:

```bash
gcloud logging tail "resource.type=cloud_run_revision AND resource.labels.service_name=${SERVICE_NAME}"
```
