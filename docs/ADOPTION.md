# Connect your Oracle data

The portable path registers governed metrics over **your Oracle AI Database 26ai schema**. Business meanings and access grants are saved in Oracle; your source rows remain in your existing tables. The application accepts typed `COUNT`, `SUM`, and `AVG` metrics over approved views and applies each user's location grants inside every certified query. Its browser interface is a structured metric explorer, not open-ended natural-language SQL.

Set `APP_MODE=portable` to serve the registered metric explorer at `/`. This mode does not expose the synthetic `/ask`, certification-request, or turnover-comparison routes.

| Item | Stored or enforced in |
| --- | --- |
| Metric meaning, owner, version, view/column contract, review and approval | `WH_REGISTERED_METRIC` and `WH_METRIC_GOVERNANCE` |
| User, role, location registry, grants, and access changes | `WH_APP_USER`, `WH_SEC_LOCATION`, `WH_USER_LOCATION`, `WH_ACCESS_AUDIT`, and `WH_SECURITY_ADMIN` |
| Organization data and source eligibility rules | Your existing source objects, exposed through same-schema `WH_` adapter views |
| Executable aggregate SQL | The application's fixed compiler; manifests and questions cannot supply SQL text |
| Exact compiled SQL, binds, metric version, user ID, and row count | `WH_REGISTERED_ANSWER_LOG` |

## 1. Connect and install

Create a local, git-ignored `.env` from `.env.example`. Set `ORACLE_USER`, `ORACLE_PASSWORD`, `ORACLE_DSN`, and `APP_MODE=portable`. This prototype uses one Oracle schema owner for setup and runtime; keep those credentials in the local environment or your deployment secret store.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
python scripts/install_catalog.py --dry-run
python scripts/install_catalog.py --apply
python scripts/install_catalog.py --verify-only
python scripts/install_security.py --dry-run
python scripts/install_security.py --apply
python scripts/install_security.py --verify-only
```

The installers create the registered catalog, governance package, four security tables, and access package. They add no source records, users, locations, or provider credentials. Reapplying them preserves saved metric versions, users, grants, and audit history while refreshing the reviewed package definitions. An existing catalog receives nullable `LOCATION_COLUMN`, `SOURCE_DDL_SHA256`, and `ASKED_BY` columns. Old versions without a location column or view fingerprint cannot answer through portable mode; create and certify a new scoped version to replace one.

**Never run `python scripts/deploy.py --apply` on an existing organization's schema.** It resets the eight synthetic demo tables and their logs.

## 2. Establish users and location keys

Bootstrap the first administrator once, then create a second administrator for independent metric approval and a reader for queries:

```bash
python scripts/userctl.py bootstrap \
  --user-id catalog_admin --display-name "Catalog Admin" \
  --token-out work/tokens/catalog_admin.token
python scripts/userctl.py create-user \
  --user-id certifier --display-name "Metric Certifier" --role ADMIN \
  --admin-token-file work/tokens/catalog_admin.token \
  --token-out work/tokens/certifier.token
python scripts/userctl.py create-user \
  --user-id analyst --display-name "Example Analyst" --role READER \
  --admin-token-file work/tokens/catalog_admin.token \
  --token-out work/tokens/analyst.token
```

Choose stable uppercase location keys that your source view can emit, then register and grant them:

```bash
python scripts/userctl.py register-location \
  --location-key SITE_A --display-name "Example Site A" \
  --admin-token-file work/tokens/catalog_admin.token
python scripts/userctl.py grant-location \
  --user-id catalog_admin --location-key SITE_A \
  --admin-token-file work/tokens/catalog_admin.token
python scripts/userctl.py grant-location \
  --user-id analyst --location-key SITE_A \
  --admin-token-file work/tokens/catalog_admin.token
```

These are **illustrative keys and identities**; replace them with your organization-approved mapping. Token-producing commands write 256-bit tokens once into private mode-`0600` files under git-ignored `work/`; they do not print tokens and will not overwrite an existing token file. Store delivered tokens in a secret manager for deployment. The CLI uses an active administrator token for access changes and derives metric reviewer/approver IDs from separate active administrator tokens. See [SECURITY.md](SECURITY.md) for revocation, rotation, and the production privilege boundary.

## 3. Expose a reviewed source view

Create a same-schema view beginning with `WH_` over only the source fields your metric needs. Each row must have one event or snapshot date and **one text location key** that matches an active `WH_SEC_LOCATION.LOCATION_KEY`. For historical facts, use the location at the event time. Put source-specific joins, eligibility rules, deduplication, and sensitive-field exclusions in this reviewed view.

For example, if your approved source relation has one row per order, a steward could adapt this illustrative shape:

```sql
CREATE OR REPLACE VIEW WH_ORDER_METRIC_SOURCE AS
SELECT o.FILLED_AT AS METRIC_DATE,
       UPPER(TRIM(o.SITE_CODE)) AS LOCATION_KEY,
       o.REGION AS REGION,
       o.NET_AMOUNT AS NET_AMOUNT
FROM WH_APPROVED_ORDER_SOURCE o
WHERE o.STATUS = 'FILLED'
  AND o.FILLED_AT IS NOT NULL;
```

`WH_APPROVED_ORDER_SOURCE` is a placeholder, not an object installed by this repository. Replace it with your approved source mapping. The `WH_` adapter view must be owned by the connected schema; underlying objects in another schema need direct grants to that schema. Use `TO_CHAR(source_numeric_location_id)` when matching numeric keys. Do not pre-aggregate several locations into one row: a shared row cannot be safely assigned to one user's scope.

The metric manifest names the view and allowed fields, **not SQL text**:

```json
{
  "metric_id": "FILLED_ORDERS",
  "name": "Filled orders",
  "owner": "Operations Analytics",
  "definition_text": "Number of fulfilled orders in the selected completed months, attributed to the site at fulfillment.",
  "unit": "orders",
  "source_view": "WH_ORDER_METRIC_SOURCE",
  "date_column": "METRIC_DATE",
  "location_column": "LOCATION_KEY",
  "aggregation": "COUNT",
  "allowed_dims": ["REGION"],
  "allowed_filters": ["REGION"]
}
```

For `SUM` or `AVG`, change `aggregation` and add `"value_column": "NET_AMOUNT"` for an approved numeric column. The catalog validates simple uppercase identifiers, the owned view, column types, its DDL timestamp, and a SHA-256 fingerprint of the full Oracle view definition. The location key is applied even if it is absent from the user's filters. The complete mapping checklist and calculation extension path are in [EXTENDING.md](EXTENDING.md).

## 4. Draft, review, and certify

Save the JSON manifest locally. These commands use version `1` as an example; substitute the version printed by `draft` if the metric already exists:

```bash
python scripts/metricctl.py draft path/to/filled_orders.json \
  --admin-token-file work/tokens/catalog_admin.token
python scripts/metricctl.py list FILLED_ORDERS \
  --admin-token-file work/tokens/catalog_admin.token
python scripts/metricctl.py show FILLED_ORDERS 1 \
  --admin-token-file work/tokens/catalog_admin.token
python scripts/metricctl.py preview FILLED_ORDERS 1 \
  --start 2026-07-01 --end 2026-10-01 --dimension REGION \
  --admin-token-file work/tokens/catalog_admin.token
python scripts/metricctl.py review FILLED_ORDERS 1 \
  --note "Checked source grain, location key, exclusions, and amounts." \
  --admin-token-file work/tokens/catalog_admin.token
python scripts/metricctl.py certify FILLED_ORDERS 1 \
  --admin-token-file work/tokens/certifier.token
```

`preview` is an **unapproved**, read-only calculation and is itself limited to the previewing administrator's location grants. `review` checks the owned view and saves its DDL timestamp, full-definition SHA-256 fingerprint, and authenticated reviewer's ID. `certify` checks that fingerprint again, requires a different active administrator, retires an earlier certified version, and makes the new one queryable. Each certified query checks the fingerprint again; if the view changes, draft and approve a new version. The web app has no user, grant, draft, review, or publish route.

### Optional synthetic adapter exercise

In a **disposable schema** with the synthetic demo seed, the repository includes [a requisition adapter](../examples/filled_requisitions.view.sql) and [its manifest](../examples/filled_requisitions.json). Install both portable layers before creating that view because it also checks Oracle's `CLIENT_IDENTIFIER`. Its `LOCATION_KEY` is the text form of the synthetic `WH_LOCATION.LOCATION_ID`: Florida keys are `1`, `2`, and `3`; Alabama is `4`. Register those keys and grant the previewing administrator the sites to inspect. Q3 2026 Florida has **20** filled requisitions across all three sites; grants for only sites `1` and `2` yield **13**. The interval is `[2026-07-01, 2026-10-01)`. This adapter is optional and is not installed by either portable installer.

## 5. Query a certified version

Start the app with `APP_MODE=portable`:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open `http://127.0.0.1:8000/` or `/catalog`, enter the reader's token, and select a certified metric. The page keeps the token only in memory and clears it on refresh. Use **HTTPS** for a remote deployment. The equivalent API contract is:

```http
GET /metrics
Authorization: Bearer <reader-token>

POST /metric-query
Authorization: Bearer <reader-token>
Content-Type: application/json

{"metric_id":"FILLED_ORDERS","start_date":"2026-07-01","end_date":"2026-10-01","dimensions":["REGION"],"filters":{}}
```

Or query from the CLI without putting a token in a shell argument:

```bash
python scripts/metricctl.py query FILLED_ORDERS \
  --start 2026-07-01 --end 2026-10-01 --dimension REGION \
  --token-file work/tokens/analyst.token
```

Both dates must be the **first of a month**; the start is included and the end excluded. A query spans at most **36 months**, and a text filter value is at most **256 characters**. Dimensions and equality filters must be on the metric's allowlist. The compiler always adds a bound `auth_user_id` and an `EXISTS` check against the active user, active grant, and active location. A client filter can narrow that scope, never expand it. No active grant yields an access denial; no matching scoped rows yields no data rather than an unscoped number. A changed source view or legacy metric version without a location key or review fingerprint cannot produce a certified answer. Answer rows, SQL, binds, metric version, and user ID are recorded in `WH_REGISTERED_ANSWER_LOG`.

## 6. Optional Select AI exploration

After a DBA prepares a provider credential and an explicit, restricted Oracle AI profile, set `SELECT_AI_ENABLED=true` and `SELECT_AI_PROFILE=WH_SELECT_AI_EXPLORE`. An **ADMIN** bearer token can then call `POST /select-ai/preview` with a question to receive generated SQL text labeled exploratory. The endpoint is absent when disabled, does not run the SQL, and does not bypass the certified metric compiler. The profile object list restricts generation context; it does **not** enforce user location access. See [SELECT_AI_SETUP.md](SELECT_AI_SETUP.md) for the exact credential/profile script and [SELECT_AI_COMPARISON.md](SELECT_AI_COMPARISON.md) for the different answer types.

## Deployment boundary

The container packages the FastAPI service but does not create views, install catalog/security objects, bootstrap users, or load records. `docker compose up --build -d` reads the local `.env` and exposes `http://localhost:8000` unless `APP_PORT` changes it. The runtime uses a bounded Oracle connection pool; `ORACLE_POOL_MAX` defaults to `4`. Compose passes the Select AI enable/profile settings, but it deliberately does **not** pass the provider setup key into the app.

This prototype uses one Oracle schema identity for both administration and runtime. The API enforces bearer identities and location-scoped SQL, and the optional synthetic adapter also checks `CLIENT_IDENTIFIER`; the schema owner still has direct table privileges. Before regulated deployment, split runtime and administration Oracle accounts, use HTTPS and enterprise identity, restrict source grants, and test database row policies for defense in depth. [SECURITY.md](SECURITY.md) describes the current boundary and access-change behavior.
