# Ask the Warehouse

**Ask for a number. See its meaning and who can see it.** Ask the Warehouse combines Oracle AI Database with a versioned metric catalog, reviewed source views, location-scoped users, bound SQL, and an answer log. An optional Oracle Select AI path drafts SQL for an administrator to inspect; it never executes that draft or turns it into a certified answer.

The repository contains only **synthetic** records. Its portable path connects to an adopter's existing Oracle schema through reviewed `WH_` adapter views; it does not import the demo records into that schema.

## Demo and repository

- [Recorded browser demo](https://akshaykamra92.github.io/AIDP-hackathon/) — durable static replay of three synthetic scenarios. It makes no database calls and saves no requests.
- [73-second showcase video](https://akshaykamra92.github.io/AIDP-hackathon/#video) — narrated, captioned overview of the Oracle catalog, approval flow, location access, and answer evidence. The [MP4](docs/media/ask-the-warehouse-showcase.mp4) is included in this repository.
- [Public GitHub repository](https://github.com/akshaykamra92/AIDP-hackathon) — application, Oracle migrations and packages, synthetic seed, tests, and setup guides.
- [Three-minute presenter script](docs/DEMO.md) — the live synthetic walkthrough. The FreeSQL-backed app has also been exercised through a temporary tunnel; run it locally for a repeatable live demo.

## Choose a path

| Mode | Question interface | Data and access |
| --- | --- | --- |
| `APP_MODE=demo` (default) | A conservative natural-language parser covers five fixed workforce metrics. Unsupported qualifiers produce a refusal and certification request. | Disposable synthetic tables and fixed reviewed SQL. |
| `APP_MODE=portable` | A structured explorer and API query certified `COUNT`, `SUM`, or `AVG` metrics registered in Oracle. | Your approved `WH_` views; bearer-token users and active location grants are required for every metric read. |

Portable metric meanings, owners, versions, source contracts, review records, and certification states live in `WH_REGISTERED_METRIC`. Review records a SHA-256 fingerprint of the complete source-view DDL, and queries reject a changed view. `WH_METRIC_GOVERNANCE` moves a new version through **DRAFT → REVIEWED → CERTIFIED**, with a different authenticated administrator approving it. `WH_SECURITY_ADMIN` manages users and location grants. Each certified query checks those grants inside the SQL statement, then records its version, SQL, bind values, user ID, and row count in `WH_REGISTERED_ANSWER_LOG`.

| Output | How it is produced | What the user receives |
| --- | --- | --- |
| Certified metric | An approved definition and a fixed compiler over a reviewed `WH_` view; dates, filters, and authenticated user ID are binds. | A scoped number, definition, version, SQL, binds, and provenance. |
| Select AI preview | Optional `DBMS_CLOUD_AI.GENERATE(..., action => 'showsql')`, available only to an authenticated portable administrator. | Generated SQL text labeled exploratory; **no execution and no certified number**. |

The Select AI provider profile is **not configured in the included FreeSQL demo**. The integration is disabled by default and has explicit [setup steps](docs/SELECT_AI_SETUP.md). See the [side-by-side comparison](docs/SELECT_AI_COMPARISON.md) for why an approved metric answer differs from generated SQL.

## Run the synthetic demo

You need Python 3.10 or newer and an Oracle AI Database 26ai schema. Python uses `python-oracledb` thin mode.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Set `ORACLE_USER`, `ORACLE_PASSWORD`, and `ORACLE_DSN` in the local, git-ignored `.env`. FreeSQL shows the DSN under **Connect to the Database → Python**; a hosted DSN has the form `tcps://db.freesql.com:2484/<your-service-name>`. Set `APP_MODE=demo`. The template's `DEMO_AS_OF_DATE=2026-10-28` pins “last quarter” to Q3 2026 for the replay.

```bash
python scripts/deploy.py --dry-run
python scripts/deploy.py --apply
python scripts/deploy.py --verify-only
uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000/`. `deploy.py --apply` **resets the eight synthetic demo tables**; run it only in a disposable demo schema. The seed contains five certified demo definitions and produces **1.89%** Florida Q3 2026 turnover under the approved definition versus **2.33%** in a separately labeled illustrative calculation that includes internal transfers. Neither figure is a Select AI result. See the [database design](docs/DATABASE.md) and [presenter script](docs/DEMO.md).

## Connect your own Oracle data

Set `APP_MODE=portable` in `.env`, then install the non-destructive catalog and security layers:

```bash
python scripts/install_catalog.py --dry-run
python scripts/install_catalog.py --apply
python scripts/install_security.py --dry-run
python scripts/install_security.py --apply
```

Create a first administrator with `scripts/userctl.py bootstrap`, register location keys, grant users only the locations they may query, and create a second administrator for metric approval. Expose source facts through a same-schema `WH_` view with a **text `LOCATION_KEY` per source row**. Save a typed JSON metric manifest and use `scripts/metricctl.py` to draft, preview, review, and certify it. CLI tokens are written once to private files under the git-ignored `work/` directory and are never printed.

Follow the full [adoption workflow](docs/ADOPTION.md), [location security guide](docs/SECURITY.md), and [schema and calculation extension guide](docs/EXTENDING.md). Existing catalog installations can be migrated without deleting their metrics; old versions lacking a location column or reviewed view fingerprint remain unavailable to portable readers until a new scoped version is certified. **Do not run `deploy.py --apply` against your organization's schema.**

Portable mode currently uses a structured metric picker and query API. It does not translate arbitrary natural-language questions into certified SQL. Only approved metric and filter fields can affect a certified answer.

## Run in Docker

After setting up the chosen database path and local `.env`:

```bash
docker compose up --build -d
```

Open `http://127.0.0.1:8000/` by default; set `APP_PORT` to change the host port. The container runs the app and checks `/health`; it does not create database objects, load rows, or receive `SELECT_AI_API_KEY`. Compose configuration has been validated; an end-to-end image build still needs a Docker host because Docker Desktop was paused during development.

## API

| Mode | Route | Purpose |
| --- | --- | --- |
| Both | `GET /health` | Database and setup status. Portable mode reports catalog, security, and bearer authentication readiness. |
| Demo | `POST /ask` | Answer one of five fixed synthetic metrics or return an exploratory refusal. |
| Demo | `POST /certification-request` | Save an unsupported synthetic question for review. |
| Demo | `GET /demo/turnover-comparison` | Fixed Q3 2026 Florida definition comparison. |
| Portable | `GET /catalog` | Public sign-in page for the metric explorer. The token stays in page memory. |
| Portable | `GET /me`, `GET /metrics` | Authenticated identity/location summary and certified metric definitions eligible for scoped queries. |
| Portable | `POST /metric-query` | Authenticated, location-scoped certified metric query. |
| Portable, opt-in | `POST /select-ai/preview` | Administrator-only, display-only generated SQL. Absent when disabled. |

The portable API requires `Authorization: Bearer <user-token>` for protected routes. A query supplies the registered metric ID, inclusive first-of-month `start_date`, exclusive first-of-month `end_date`, and only approved dimensions and filters. The authenticated identity comes from the token, never from the request body. See the [request example](docs/ADOPTION.md#5-query-a-certified-version).

## Verify and deployment boundary

```bash
python3 -m unittest discover -s tests -v
python3 tests/run_golden.py
```

The golden runner checks parser and compiler behavior on synthetic metadata. A live database check also needs the configured Oracle connection and `sql/03_verify.sql`. The portable security path was exercised against FreeSQL with two users on different locations; a Florida user granted two sites saw 13 Q3 filled requisitions, while an Alabama user granted one site saw 5. Cross-location queries returned no data, and reactivating a disabled location did not restore a revoked grant.

This remains a deployable **prototype** using one Oracle schema owner for administration and runtime. The application enforces bearer identity and location predicates; the schema owner still has direct table privileges. For sensitive deployment, use HTTPS and enterprise identity management, split Oracle runtime and administration accounts, restrict source privileges, and test database row policies as defense in depth. See [SECURITY.md](docs/SECURITY.md) for the exact boundary.
