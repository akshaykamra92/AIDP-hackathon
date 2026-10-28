# Ask the Warehouse

**A number is useful only when its definition is clear.** Ask the Warehouse answers supported business questions from certified metrics in Oracle AI Database 26ai. Every certified response includes the metric's definition, owner, version, reporting period, data coverage/freshness status, compiled SQL, bind values, and row count. Unsupported questions get a clear refusal and a way to request certification.

This is a hackathon prototype with **synthetic data only**. The same pattern can govern metrics in another organization by replacing the source tables, approved definitions, and question vocabulary.

## What happens when you ask

1. A conservative question parser maps supported phrasing to a metric, allowed dimensions and filters, and a half-open reporting period `[start, end)`.
2. The server checks that the metric is `CERTIFIED` and that every requested dimension, filter, and date is allowed. An unmapped qualifier causes a refusal; it is never silently ignored.
3. A fixed, reviewed SQL statement is selected by metric ID and executed with bind variables. The question does not become SQL. The metric catalog holds the approved metadata and the SQL compiler key; the executable statements live in application code.
4. The result and its query provenance are recorded in `WH_ANSWER_LOG` and returned to the browser.
5. An unsupported metric returns nearby certified choices and a certification button, which writes an `OPEN` row to `WH_METRIC_REQUEST`.

The runtime does **not** call Select AI. FreeSQL exposed the `DBMS_CLOUD_AI` package, but no provider credential or Select AI profile was configured for this demo. The unsupported-question path therefore refuses to invent a number. The comparison shown in the UI is a separate, fixed, read-only illustrative calculation, clearly labeled as an ad hoc interpretation.

## Run locally

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

To reset the `WH_` schema, load the synthetic seed, and check its expected results through the configured Python connection, run:

```bash
python scripts/deploy.py --apply
```

For a read-only check after deployment, run `python scripts/deploy.py --verify-only`. This check allows answer logs and certification requests created by the app while still checking the synthetic seed tables exactly. The runner accepts only the three repository-owned SQL files and does not print credentials. You can also run these files **one at a time, as scripts** in a FreeSQL worksheet or SQLcl session connected to the same schema:

1. [`sql/01_schema.sql`](sql/01_schema.sql) — create the `WH_` tables, comments, and 26ai annotations.
2. [`sql/02_seed.sql`](sql/02_seed.sql) — load deterministic synthetic records and five certified metrics.
3. [`sql/03_verify.sql`](sql/03_verify.sql) — run read-only row-count and Florida turnover checks.

`01_schema.sql` resets only `WH_` objects, so rerunning it removes prior synthetic rows and answer/request logs. `02_seed.sql` can be rerun independently to reset the seed rows. The verification script should show 17 approved Florida Q3 2026 exits after excluding 4 internal transfers, for a certified turnover rate of **1.89%**, versus **2.33%** when transfers are included. Database design and expected row counts are documented in [`docs/DATABASE.md`](docs/DATABASE.md).

Then start the app:

```bash
uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000/`. The header reports whether the database connection is available. The UI still loads without connection settings, but `/ask` returns a clear service error until they are supplied.

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

`POST /ask` accepts `{"question":"What was our turnover in Florida last quarter?"}`. Certified responses include `tier: "CERTIFIED"`, `metric`, `intent`, `rows`, `summary`, `sql`, `binds`, `freshness`, and `warnings`. Unsupported questions return `tier: "EXPLORATORY"`, a refusal message, unmapped terms, and nearest certified metrics; they do not run generated SQL.

## Design boundaries

The five seeded metrics are headcount, turnover rate, voluntary turnover rate, median time to fill, and internal fill rate. The parser and approved SQL are intentionally narrow so a new qualifier cannot quietly change an official number. Date predicates use half-open ranges, and the UI displays the final included date for clarity.

The seed does not record an ingestion timestamp, so the app reports the covered period and says ingestion freshness is unavailable. This prototype has no sign-in, row-level security, BI connector, real employee data, or approval workflow beyond logging a request. The fixed comparison illustrates definition ambiguity; it is not a live model answer. For production, add authentication, access controls, a managed metric approval process, monitoring, and source-specific data mapping.
