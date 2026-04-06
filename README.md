# BOBJ → Datasphere & SAC Converter
### SAP BTP · Cloud Foundry · HANA Cloud · SAP AI Core

AI-powered tool that converts SAP BusinessObjects (BOBJ) artifacts — Universe XML,
`.rpt` reports, and metadata descriptions — into SAP Datasphere entity definitions
and SAP Analytics Cloud (SAC) model configurations, deployed fully on SAP BTP.

---

## Architecture

```
User / Browser
      │
      ▼
Build Work Zone (CF)          ← UI portal + app router
      │
      ▼
FastAPI Backend (CF)          ← REST API, conversion orchestration
  ├──► XSUAA / IAS            ← JWT token validation, RBAC
  ├──► SAP AI Core            ← LLM conversion engine
  ├──► HANA Cloud             ← Jobs, metadata, session data
  ├──► SAP Datasphere         ← Push entity definitions (REST API)
  └──► SAP Analytics Cloud    ← Push model & story config (REST API)
```

---

## Prerequisites

| Requirement | Notes |
|---|---|
| SAP BTP subaccount | Trial or paid, EU20 / US10 region |
| Cloud Foundry space | `cf login` and `cf target` ready |
| HANA Cloud entitlement | Plan: `hana` (or `trial`) |
| XSUAA entitlement | Plan: `application` |
| SAP AI Core entitlement | Plan: `extended` |
| SAP Build Work Zone | Standard edition |
| Python 3.12+ | For local development |
| `cf` CLI v8+ | Cloud Foundry CLI |

---

## Quick Start

### 1. Clone & configure

```bash
git clone <your-repo>/bobj-converter
cd bobj-converter
cp .env.example .env          # fill in your values
```

### 2. Create BTP services (or run the deploy script)

```bash
# XSUAA
cf create-service xsuaa application bobj-converter-xsuaa \
  -c security/xs-security.json

# HANA Cloud (takes 5–10 min to provision)
cf create-service hana hana bobj-converter-hana \
  -c '{"data":{"memory":32,"systempassword":"<strong-password>"}}'

# SAP AI Core
cf create-service aicore extended bobj-converter-aicore
```

### 3. Deploy to Cloud Foundry

```bash
export CF_DOMAIN="cfapps.eu20.hana.ondemand.com"
export HANA_PASSWORD="<your-hana-password>"
bash scripts/deploy.sh
```

### 4. Run HANA schema

Connect to your HANA Cloud instance via the BTP HANA Cockpit or DBeaver,
then execute **`db/schema.sql`**.

### 5. Configure role collections

In BTP Cockpit → Security → Role Collections, assign:
- `BOBJ Converter - Publisher` → your user / IDP group

---

## Project Structure

```
bobj-converter/
├── backend/
│   ├── app/
│   │   ├── main.py                 # FastAPI app, CF binding, startup
│   │   ├── auth/
│   │   │   └── xsuaa.py            # JWT validation, scope enforcement
│   │   ├── db/
│   │   │   └── hana.py             # HANA Cloud connection pool (hdbcli)
│   │   ├── models/
│   │   │   └── schemas.py          # Pydantic request/response schemas
│   │   ├── routers/
│   │   │   ├── conversion.py       # POST /conversions — submit job
│   │   │   ├── projects.py         # CRUD /projects
│   │   │   ├── jobs.py             # GET /jobs — list & filter
│   │   │   └── health.py           # GET /health — DB liveness probe
│   │   └── services/
│   │       ├── ai_core.py          # SAP AI Core / Anthropic LLM client
│   │       ├── datasphere.py       # Datasphere REST API push
│   │       └── sac.py              # SAC REST API push
│   ├── manifest.yml                # CF app manifest
│   └── requirements.txt
├── frontend/
│   └── index.html                  # Single-page UI
├── db/
│   └── schema.sql                  # HANA Cloud DDL (all 7 tables)
├── security/
│   └── xs-security.json            # XSUAA scopes & role-collections
├── scripts/
│   └── deploy.sh                   # One-shot deploy script
└── README.md
```

---

## API Endpoints

| Method | Path | Auth scope | Description |
|--------|------|-----------|-------------|
| `GET`  | `/api/health` | — | Liveness + HANA check |
| `POST` | `/api/v1/conversions` | `convert` | Submit BOBJ artifact, returns job_id |
| `GET`  | `/api/v1/conversions/{job_id}` | `read` | Poll conversion result |
| `GET`  | `/api/v1/projects` | `read` | List your projects |
| `POST` | `/api/v1/projects` | `convert` | Create project |
| `DELETE` | `/api/v1/projects/{id}` | `convert` | Delete project |
| `GET`  | `/api/v1/jobs` | `read` | List jobs (filterable) |

Interactive docs: `https://bobj-converter-api.<your-domain>/api/docs`

---

## HANA Cloud Tables

| Table | Purpose |
|---|---|
| `BOBJ_PROJECTS` | User projects grouping multiple conversion jobs |
| `BOBJ_CONVERSION_JOBS` | Job queue with status, raw input, result JSON |
| `BOBJ_METADATA_OBJECTS` | Parsed BOBJ objects (tables, classes, measures) |
| `DATASPHERE_ENTITIES` | Generated Datasphere entity definitions |
| `SAC_MODEL_CONFIGS` | Generated SAC model configurations |
| `CONVERSION_MAPPING` | Object-level mapping audit trail |
| `USER_PREFERENCES` | Per-user settings and defaults |
| `AUDIT_LOG` | Full action audit log |

---

## XSUAA Role Collections

| Collection | Scopes | Intended for |
|---|---|---|
| BOBJ Converter - Viewer | `read` | Stakeholders who review results |
| BOBJ Converter - Converter | `read`, `convert` | Migration analysts |
| BOBJ Converter - Publisher | `read`, `convert`, `push` | Leads who push to Datasphere/SAC |
| BOBJ Converter - Admin | all | Admins |

---

## Environment Variables

Set via `cf set-env bobj-converter-api <KEY> <VALUE>`:

| Variable | Description |
|---|---|
| `DATASPHERE_BASE_URL` | Datasphere tenant URL |
| `DATASPHERE_SPACE_ID` | Target Datasphere space |
| `DATASPHERE_TOKEN_URL` | OAuth2 token endpoint |
| `DATASPHERE_CLIENT_ID` | OAuth2 client ID |
| `DATASPHERE_CLIENT_SECRET` | OAuth2 client secret |
| `SAC_TENANT_URL` | SAC tenant URL |
| `SAC_TOKEN_URL` | SAC OAuth2 token endpoint |
| `SAC_CLIENT_ID` | SAC client ID |
| `SAC_CLIENT_SECRET` | SAC client secret |
| `AICORE_DEPLOYMENT_ID` | AI Core deployment ID for your LLM |
| `AICORE_RESOURCE_GROUP` | AI Core resource group (default: `default`) |
| `HANA_POOL_SIZE` | HANA connection pool size (default: `5`) |

---

## Local Development

```bash
cd backend
pip install -r requirements.txt
cp ../.env.example .env

# Start with hot reload
uvicorn app.main:app --reload --port 8000
```

For local HANA access, set `HANA_HOST`, `HANA_PORT`, `HANA_USER`, `HANA_PASSWORD`
in your `.env` file instead of VCAP_SERVICES.

---

## License
MIT
