# Guía de Despliegue en Google Cloud Run · CSA Tech Auditor

Esta guía contiene los pasos exactos y comandos de `gcloud` para desplegar el backend y la SPA de **CSA Tech Auditor · Havas** en **Google Cloud Run**, configurando el entorno con los recursos adecuados para la ejecución de Chromium headless (1–2 vCPU y 1–2 GiB de RAM).

---

## 1. Prerrequisitos en Google Cloud Platform

1. Cuenta de Google Cloud con facturación activa.
2. Google Cloud SDK (`gcloud`) instalado y actualizado (`gcloud components update`).
3. Permisos de IAM requeridos en el proyecto:
   - `roles/run.admin` (Cloud Run Admin)
   - `roles/artifactregistry.admin` (Artifact Registry Admin)
   - `roles/cloudbuild.builds.editor` (Cloud Build Editor)
   - `roles/iam.serviceAccountUser` (Service Account User)

---

## 2. Configuración Inicial del Entorno

Reemplaza `TU_PROJECT_ID` y `TU_REGION` con los valores de tu entorno (por ejemplo, `csa-analytics-prod` y `us-central1` o `europe-west1`).

```bash
# 1. Autenticarse en Google Cloud
gcloud auth login

# 2. Configurar el ID del proyecto
export PROJECT_ID="TU_PROJECT_ID"
export REGION="us-central1"
export SERVICE_NAME="csa-tech-auditor"
export REPO_NAME="csa-docker-repo"
export IMAGE_TAG="1.0.0"

gcloud config set project $PROJECT_ID
gcloud config set run/region $REGION

# 3. Habilitar las APIs necesarias en GCP
gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  compute.googleapis.com
```

---

## 3. Crear el Repositorio en Artifact Registry

Artifact Registry es el registro de contenedores recomendado para Cloud Run.

```bash
gcloud artifacts repositories create $REPO_NAME \
  --repository-format=docker \
  --location=$REGION \
  --description="Repositorio Docker para CSA Tech Auditor"
```

---

## 4. Construcción y Empaquetado de la Imagen

### Opción A: Construcción Serverless con Google Cloud Build (Recomendado)
No requiere Docker instalado localmente:

```bash
IMAGE_URL="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/${SERVICE_NAME}:${IMAGE_TAG}"

gcloud builds submit --tag $IMAGE_URL .
```

### Opción B: Construcción Local con Docker y Push
```bash
# Configurar credenciales de Docker para Artifact Registry
gcloud auth configure-docker ${REGION}-docker.pkg.dev

# Construir imagen localmente
docker build -t $IMAGE_URL .

# Subir al repositorio
docker push $IMAGE_URL
```

---

## 5. Despliegue en Google Cloud Run

Dado que Playwright ejecuta instancias de Chromium en memoria para evaluar JavaScript y capturar el `dataLayer`, se requiere un dimensionamiento mínimo de **2 GiB de memoria RAM y 2 vCPUs**, con una concurrencia controlada (10–20 peticiones concurrentes por instancia) para evitar saturación de memoria.

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

### Explicación de los Parámetros de Despliegue:
- `--memory=2Gi`: Asigna 2 GiB de memoria RAM para garantizar espacio suficiente para páginas pesadas de SPAs y múltiples pestañas de Chromium.
- `--cpu=2`: 2 vCPUs dedicadas para procesar compilación y ejecución rápida de JavaScript en el motor V8.
- `--concurrency=10`: Limita el número de peticiones simultáneas procesadas por contenedor antes de que Cloud Run escale una nueva instancia horizontal.
- `--timeout=180s`: Permite tiempo suficiente para auditorías batch de hasta 15 páginas sin interrupción de conexión.
- `--min-instances=0`: Escala a 0 para optimizar costes cuando no hay auditorías en curso (cámbialo a `1` si deseas eliminar cold starts).
- `--allow-unauthenticated`: Hace que el servicio sea accesible públicamente en la web (si requieres acceso solo corporativo con IAM, omite este flag).

---

## 6. Verificación Post-Despliegue

Obtén la URL pública asignada por Cloud Run:

```bash
export SERVICE_URL=$(gcloud run services describe $SERVICE_NAME --platform=managed --region=$REGION --format='value(status.url)')
echo "Servicio desplegado en: $SERVICE_URL"
```

### Probar el Endpoint de Salud:
```bash
curl -X GET "${SERVICE_URL}/health"
```

### Probar la Auditoría de Página:
```bash
curl -X POST "${SERVICE_URL}/api/audit-page" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://example.com",
    "render_mode": "render"
  }'
```

---

## 7. Mapeo de Dominio Personalizado (Opcional)

Si deseas apuntar un subdominio corporativo de Havas (ej. `auditor.csa.havas.com`):

```bash
gcloud beta run domain-mappings create \
  --service=$SERVICE_NAME \
  --domain=auditor.csa.havas.com \
  --region=$REGION
```

Sigue las instrucciones que devolverá `gcloud` para agregar los registros DNS `CNAME` o `A` correspondientes en tu proveedor DNS corporativo.

---

## 8. Monitorización y Logs

Para auditar el consumo de memoria y ver trazas de Playwright en Cloud Logging:

```bash
# Ver los últimos 50 logs del servicio
gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=${SERVICE_NAME}" \
  --limit=50 \
  --format="table(timestamp, textPayload, jsonPayload.message)"
```
