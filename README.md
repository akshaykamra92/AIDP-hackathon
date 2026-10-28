# Ask the Warehouse

**A number is useful only when its definition is clear.** Ask the Warehouse returns certified metric results from Oracle AI Database 26ai with the definition, owner, version, compiled SQL, bind values, and result count. Its synthetic natural-language demo also shows data coverage and offers a certification request when a question cannot be mapped. Its portable explorer queries registered definitions over an organization's approved Oracle views.

The included workforce walkthrough uses **synthetic data only**. A separate portable path lets another organization register versioned metric meanings in its own Oracle schema and query approved views without importing the demo records.

## Demo

The [GitHub Pages demo](https://akshaykamra92.github.io/AIDP-hackathon/) is a durable **static replay** of three synthetic scenarios. It makes no database calls and does not save certification requests. The live FreeSQL-backed app was also checked in a browser through a temporary public tunnel: **3/3 workflows passed** (certified Florida turnover, definition comparison, and unsupported burnout metric with a saved certification request). The tunnel address is temporary; use the local run steps below for a repeatable live demo.

## Two paths

| Mode | What it serves | Database setup |
| --- | --- | --- |
| `APP_MODE=demo` (default) | The synthetic natural-language walkthrough and five fixed workforce metrics. | Use the disposable `WH_` demo schema and seed. |
| `APP_MODE=portable` | A certified metric explorer and structured query API for an organization's own Oracle data. | Install the non-destructive registered catalog, add reviewed `WH_` source views, and register metric versions. |

The portable metric's name, meaning, owner, version, source-view contract, allowlists, and status are saved in `WH_REGISTERED_METRIC` in Oracle. An administrator creates a draft, reviews the view, and certifies it with a different approver. The browser lists only certified versions. See the [adoption guide](docs/ADOPTION.md) for the exact commands and deployment limits.

## What happens in the synthetic question flow

1. A conservative question parser maps supported phrasing to a metric, allowed dimensions and filters, and a half-open reporting period `[start, end)`.
2. The server checks that the metric is `CERTIFIED` and that every requested dimension, filter, and date is allowed. An unmapped qualifier causes a refusal; it is never silently ignored.
3. A fixed, reviewed SQL statement is selected by metric ID and executed with bind variables. The question does not become SQL. The metric catalog holds the approved metadata and the SQL compiler key; the executable statements live in application code.
4. The result and its query provenance are recorded in `WH_ANSWER_LOG` and returned to the browser.
5. An unsupported metric returns nearby certified choices and a certification button, which writes an `OPEN` row to `WH_METRIC_REQUEST`.

The runtime does **not** call Select AI. FreeSQL exposed the `DBMS_CLOUD_AI` package, but no provider credential or Select AI profile was configured for this demo. The unsupported-question path therefore refuses to invent a number. The comparison shown in the UI is a separate, fixed, read-only illustrative calculation, clearly labeled as an ad hoc interpretation.

For a question-by-question comparison with Oracle Select AI, see [`docs/SELECT_AI_COMPARISON.md`](docs/SELECT_AI_COMPARISON.md).

## Run the synthetic demo locally

You need Python 3.10 or newer and access to an Oracle AI Database 26ai schema. The app uses `python-oracledb` in thin mode.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Copy the environment template, then fill it locally:

```bash
cp .env.example .env
```

Set `ORACLE_USER` and `ORACLE_PASSWORD` for the same schema where you run the SQL scripts. Copy `ORACLE_DSN` from FreeSQL's **Connect to the Database → Python** panel; its hosted form is `tcps://db.freesql.com:2484/<your-service-name>`. Python thin mode connects without wallet files. The template includes `DEMO_AS_OF_DATE=2026-10-28`, which pins “last quarter” to Q3 2026 for the repeatable hackathon demo. Keep credentials out of source control and terminal output.

Parse the reviewed SQL scripts locally before changing the database:

```bash
python scripts/deploy.py --dry-run
```

To reset the eight synthetic demo tables, load their seed, and check expected results through the configured Python connection, run:

```bash
python scripts/deploy.py --apply
```

For a read-only check after deployment, run `python scripts/deploy.py --verify-only`. This check allows answer logs and certification requests created by the app while still checking the synthetic seed tables exactly. The runner accepts only the three repository-owned SQL files and does not print credentials. You can also run these files **one at a time, as scripts** in a FreeSQL worksheet or SQLcl session connected to the same schema:

1. [`sql/01_schema.sql`](sql/01_schema.sql) — create the `WH_` tables, comments, and 26ai annotations.
2. [`sql/02_seed.sql`](sql/02_seed.sql) — load deterministic synthetic records and five certified metrics.
3. [`sql/03_verify.sql`](sql/03_verify.sql) — run read-only row-count and Florida turnover checks.

`01_schema.sql` resets only the eight demo `WH_` tables, so rerunning it removes prior synthetic rows and demo answer/request logs. `02_seed.sql` can be rerun independently to reset those demo rows. Neither script initializes the separate portable catalog. The verification script should show 17 approved Florida Q3 2026 exits after excluding 4 internal transfers, for a certified turnover rate of **1.89%**, versus **2.33%** when transfers are included. Database design and expected row counts are documented in [`docs/DATABASE.md`](docs/DATABASE.md).

Then start the app:

```bash
uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000/`. The header reports whether the database connection is available. The UI still loads without connection settings, but `/ask` returns a clear service error until they are supplied.

For your own Oracle data, **do not run the demo reset**. Set `APP_MODE=portable`, run `python scripts/install_catalog.py --apply`, create a same-schema `WH_` source view, register its metric through `scripts/metricctl.py`, then start the same app. The full [portable setup and metric lifecycle](docs/ADOPTION.md) explains the view, manifest, review, certification, and query steps. This path currently requires Oracle AI Database 26ai and uses a structured metric picker, not open-ended natural-language SQL.

## Run in Docker

After configuring the local `.env` and setting up the database for your selected mode, build and start the container:

```bash
docker compose up --build -d
```

Open `http://127.0.0.1:8000/` by default. Set `APP_PORT` in your shell or local `.env` to change the host port. The container runs the app and checks `/health`; it does **not** run schema scripts or move source data. Run `docker compose down` to stop it. Docker was not available in the development sandbox, so the image and Compose configuration have static checks but still need an end-to-end container run on a Docker host.

## Try the demo

1. Ask **“What was our turnover in Florida last quarter?”** The certified definition excludes internal transfers. Open **Show SQL** to inspect the reviewed query and bound date/state values.
2. Click **See the difference**. The comparison includes internal transfers in an illustrative ad hoc numerator while keeping the same denominator. It is not a Select AI response.
3. Ask **“What's our burnout rate?”** The app refuses because no certified burnout metric exists. Click **Request certification** to create a review request.

The full presenter script is in [`docs/DEMO.md`](docs/DEMO.md).

## Verify

The parser, approved SQL compiler, service behavior, and golden question set can be checked without database credentials:

```bash
python3 -m unittest discover -s tests -v
python3 tests/run_golden.py
```

The golden runner prints intent accuracy, refusal correctness, and whether paraphrases compile to identical SQL and binds using **synthetic metadata**. These are parser/compiler checks, not measured live-database accuracy.

After loading the schema, run [`sql/03_verify.sql`](sql/03_verify.sql) in the FreeSQL worksheet to check row counts, data coverage, and the Florida definition contrast. A live UI check requires the local `.env` connection settings.

## API

| Route | Purpose |
| --- | --- |
| `GET /` | One-page browser interface |
| `GET /health` | Service and database connection status |
| `POST /ask` | Answer from a certified metric or return an exploratory refusal |
| `POST /certification-request` | Log a request for an unsupported metric |
| `GET /demo/turnover-comparison` | Fixed Q3 2026 Florida definition comparison |
| `GET /catalog` | Registered metric explorer |
| `GET /metrics` | List certified registered metric versions |
| `POST /metric-query` | Query a certified registered metric using approved dimensions and bound filters |

`POST /ask` accepts `{"question":"What was our turnover in Florida last quarter?"}`. Certified responses include `tier: "CERTIFIED"`, `metric`, `intent`, `rows`, `summary`, `sql`, `binds`, `freshness`, and `warnings`. Unsupported questions return `tier: "EXPLORATORY"`, a refusal message, unmapped terms, and nearest certified metrics; they do not run generated SQL.

In portable mode, the synthetic `/ask`, request, and comparison routes are disabled. `POST /metric-query` accepts a registered metric ID, first-of-month `start_date` and exclusive `end_date`, plus allowlisted `dimensions` and `filters`; see the [request example](docs/ADOPTION.md#4-query-a-certified-version). Public routes cannot draft, review, or certify a metric.

## Design boundaries

The five seeded metrics are headcount, turnover rate, voluntary turnover rate, median time to fill, and internal fill rate. The parser and approved SQL are intentionally narrow so a new qualifier cannot quietly change an official number. Date predicates use half-open ranges, and the UI displays the final included date for clarity.

The seed does not record an ingestion timestamp, so the app reports the covered period and says ingestion freshness is unavailable. The demo request button only logs a request; the portable path has a separate administrator CLI for draft, review, and certification. This prototype has no sign-in, per-user row policy, or BI connector. The fixed comparison illustrates definition ambiguity; it is not a live model answer. For production, add authentication, source access controls, role separation, monitoring, and organization-specific data mapping.
