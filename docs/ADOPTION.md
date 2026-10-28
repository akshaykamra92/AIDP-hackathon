# Connect your Oracle data

The portable path registers governed metrics over views in **your Oracle AI Database 26ai schema**. The metric's business meaning is saved in Oracle; source rows stay in your existing tables. This path supports typed `COUNT`, `SUM`, and `AVG` metrics with approved grouping columns and equality filters. It does not turn arbitrary natural-language questions into SQL.

The synthetic workforce walkthrough is separate. Use `APP_MODE=portable` to serve the registered metric explorer at `/` and disable its synthetic `/ask`, certification-request, and turnover-comparison routes.

| Item | Where it lives |
| --- | --- |
| Metric name, business meaning, owner, unit, version, status, view and column contract, and review identities | `WH_REGISTERED_METRIC` in your Oracle schema |
| Organization data | Your source tables, exposed through an approved same-schema `WH_` view |
| Executable query shape | The application's fixed `COUNT`/`SUM`/`AVG` compiler; a manifest cannot supply SQL |
| Exact compiled SQL, bind values, selected metric version, and result count for a certified query | `WH_REGISTERED_ANSWER_LOG` |

## 1. Connect and install the catalog

Copy `.env.example` to a local, git-ignored `.env`. Set `ORACLE_USER`, `ORACLE_PASSWORD`, and `ORACLE_DSN` for the Oracle schema that will own the catalog and adapter views, and set `APP_MODE=portable`. The current app uses this **one schema identity** for catalog administration and runtime queries. Keep the credentials out of source control and logs.

Install dependencies, then check and apply the non-destructive catalog migration:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python scripts/install_catalog.py --dry-run
python scripts/install_catalog.py --apply
python scripts/install_catalog.py --verify-only
```

The installer creates `WH_REGISTERED_METRIC`, `WH_REGISTERED_ANSWER_LOG`, and the `WH_METRIC_GOVERNANCE` package if needed, or verifies their existing shape. It does not load synthetic rows or delete saved metric versions and audit records. It replaces the governance package definition on `--apply`; review it before applying in a managed environment.

**Do not use `python scripts/deploy.py --apply` on an existing organization's schema.** That command resets the `WH_` synthetic demo tables and their logs. It is intended for a disposable demo schema. The portable installer above is the onboarding path.

## 2. Expose an approved source view

A data steward creates a **same-schema view whose name starts with `WH_`** over the source tables the application is allowed to query. The view should expose one row per fact, a `DATE` or `TIMESTAMP` column, and any approved text or numeric dimensions. `SUM` and `AVG` also require a numeric value column. Keep source-specific joins, eligibility rules, and sensitive-column exclusions in this reviewed view. The catalog does not copy the source rows.

For example, a `WH_ORDER_METRIC_SOURCE` view could expose `FILLED_DATE`, `REGION`, and `AMOUNT` from an organization's governed order data. The example manifest below describes a count of filled orders; it contains identifiers and meaning, **no SQL text**:

```json
{
  "metric_id": "FILLED_ORDERS",
  "name": "Filled orders",
  "owner": "Operations Analytics",
  "definition_text": "Number of fulfilled orders in the selected completed months, attributed to the region recorded at fulfillment.",
  "unit": "orders",
  "source_view": "WH_ORDER_METRIC_SOURCE",
  "date_column": "FILLED_DATE",
  "aggregation": "COUNT",
  "allowed_dims": ["REGION"],
  "allowed_filters": ["REGION"]
}
```

For `SUM` or `AVG`, set `aggregation` accordingly and add `value_column` with an approved numeric column. All view and column identifiers must be simple uppercase names. The app verifies the owned view and column types against Oracle's data dictionary before review and again before each certified query. If the view changes after review, queries stop until a new metric version is reviewed and certified.

For a **disposable synthetic schema**, the repository includes a working [adapter view](../examples/filled_requisitions.view.sql) and [manifest](../examples/filled_requisitions.json) over the demo requisition tables. Run the view script in an Oracle worksheet after loading the demo seed, then try:

```bash
python scripts/metricctl.py draft examples/filled_requisitions.json
python scripts/metricctl.py preview FILLED_REQUISITIONS 1 --start 2026-07-01 --end 2026-10-01 --dimension STATE --filter STATE=FL
```

The Q3 2026 Florida **unapproved preview** should count 20 filled requisitions from the synthetic seed. The full-month interval is `[2026-07-01, 2026-10-01)`; it excludes October 1. Replace `1` with the version printed by `draft` if this metric was registered earlier. Review and certify the saved version before making it available in the explorer. The portable installer does not install this example or touch the source tables.

## 3. Save the meaning, review, and certify

Save the JSON manifest as a local file, then use the administrator CLI. The following commands assume the example metric ID and version `1`; use the version printed by `draft` for later revisions.

```bash
python scripts/metricctl.py draft path/to/filled_orders.json
python scripts/metricctl.py list FILLED_ORDERS
python scripts/metricctl.py show FILLED_ORDERS 1
python scripts/metricctl.py preview FILLED_ORDERS 1 --start 2026-07-01 --end 2026-10-01 --dimension REGION
python scripts/metricctl.py review FILLED_ORDERS 1 --reviewer "Data Steward" --note "Checked source view, exclusions, and grouping grain."
python scripts/metricctl.py certify FILLED_ORDERS 1 --approver "Metric Owner"
```

The lifecycle is **DRAFT → REVIEWED → CERTIFIED → RETIRED**. `draft` writes the definition, owner, source-view contract, allowlists, and a new version to `WH_REGISTERED_METRIC`. The optional `preview` runs a read-only calculation labeled **unapproved**; it is not a certified answer. `review` checks the source view and saves the reviewer and note. `certify` requires a different approver name, publishes that version, and retires any earlier certified version of the same metric. A changed definition or view gets a new draft version; the CLI does not edit the previous definition in place. The administrator commands are not public web routes.

This catalog is separate from the five seeded `WH_METRIC` demo definitions. The demo's unsupported-question button writes an `OPEN` row to `WH_METRIC_REQUEST`; it does **not** automatically create, review, or certify a registered metric.

## 4. Query a certified version

Start the app locally:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Or run it with Docker after the database setup is complete:

```bash
docker compose up --build -d
```

Open `http://127.0.0.1:8000/` or `/catalog`. The explorer lists only certified metrics, shows their definitions and owner, and offers the metric's approved dimensions and filters. The same query is available by API:

```http
GET /metrics
POST /metric-query
Content-Type: application/json

{"metric_id":"FILLED_ORDERS","start_date":"2026-07-01","end_date":"2026-10-01","dimensions":["REGION"],"filters":{}}
```

The start date is included and the end date excluded. Both must be the **first day of a month**, and the end must follow the start. The compiler accepts only certified catalog identifiers and `COUNT`, `SUM`, or `AVG`; all dates and filter values are bind variables. Certified responses include the definition/version, result rows, compiled SQL, and binds. `WH_REGISTERED_ANSWER_LOG` records the exact metric version and query provenance. If no certified version exists, a filter or dimension is not allowed, or the reviewed view changed, the API returns an error instead of a certified number.

## Deployment boundary

The container packages the FastAPI app; it does not create your source views or install the Oracle catalog. `docker compose up --build -d` reads local `.env` settings, exposes `http://localhost:8000` by default (or the local `APP_PORT`), and uses `/health` to check the Oracle connection and catalog. The same image can run on a host that supplies the required `ORACLE_*` environment variables and `APP_MODE=portable`. Each app process uses a bounded Oracle connection pool; `ORACLE_POOL_MAX` defaults to 4 and can be adjusted for the database's connection budget.

This is a deployable **prototype**, not a complete access-control system. There is no application sign-in or per-user row policy. Reviewer and approver names are supplied to the CLI; their identities are not authenticated. The current one-schema connection can both manage the catalog and query data, and that schema's owner could modify catalog rows outside the CLI. Before connecting sensitive or regulated data, put the service behind authentication and network controls, restrict the source view to authorized columns/rows, and separate runtime read privileges from catalog administration. That separation requires database grants and connection-role changes beyond this prototype. Use synthetic data for this repository's tests and examples.
