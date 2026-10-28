# Adapt the app to another schema

The reusable contract is: **source tables → reviewed `WH_` adapter view → typed metric manifest → Oracle review and certification → location-scoped query**. The application does not need to know an adopter's original table names. Each certified answer still comes from a fixed compiler with bind variables.

## 1. Map the question to a source grain

Before writing a view, record these decisions for each proposed metric:

| Decision | Check |
| --- | --- |
| Fact grain | What does one source row count or measure? A join to a child table must not duplicate a fact. |
| Reporting date | Which event or snapshot date controls the half-open interval `[start_date, end_date)`? |
| Location | Which **single** location owned this fact at that event time? What stable uppercase text key represents it? |
| Eligibility | Which statuses, reversals, test rows, and nulls are excluded? |
| Value | For `SUM` or `AVG`, what numeric field and unit are approved? |
| Dimensions | Which text or numeric columns may users group or filter by? |
| Owner | Who verifies the meaning, the source mapping, and the release of a new version? |

For example, a fulfilled-order count needs one row per filled order, its fill date, and its site at fulfillment. Joining to order lines without first reducing them to one row per order would inflate `COUNT(*)`. A monthly snapshot needs an explicit month date and must not be summed across months unless that is the intended definition.

## 2. Build the adapter view

Install the [portable catalog and security objects](ADOPTION.md#1-connect-and-install), then create a **same-schema, owned `WH_` view**. Existing source tables may have any names, but every new database object in this project uses the `WH_` prefix. The view should expose only approved fields, use simple uppercase aliases, and emit one canonical text location key per fact.

```sql
CREATE OR REPLACE VIEW WH_ORDER_METRIC_SOURCE AS
SELECT o.FILLED_AT AS METRIC_DATE,
       UPPER(TRIM(s.SITE_CODE)) AS LOCATION_KEY,
       o.CHANNEL AS CHANNEL,
       o.NET_AMOUNT AS NET_AMOUNT
FROM WH_APPROVED_ORDER_SOURCE o
JOIN WH_APPROVED_SITE_SOURCE s ON s.SITE_ID = o.SITE_ID_AT_FILL
WHERE o.STATUS = 'FILLED'
  AND o.FILLED_AT IS NOT NULL;
```

The two `WH_APPROVED_*` relations are placeholders for approved sources. Replace the join and aliases with your own documented mapping. If the source location ID is numeric, use `TRIM(TO_CHAR(...))`; register the same text key with `scripts/userctl.py register-location`. Keep the location lookup at the **event time** for historical facts. If a source record represents several locations, split it into location-specific facts under a reviewed allocation rule or do not certify a location-scoped metric from it.

Keep sensitive fields out of the adapter. Review view grants, joins, and row-level source policy with the data owner. For an additional database table, add it to the adapter's reviewed join or create another `WH_` adapter view; the browser and metric compiler remain the same. An organization may share one adapter across several metrics when its row grain and location meaning are the same.

## 3. Register a meaning, then approve it

Create a JSON manifest containing identifiers and business meaning, with **no SQL text**:

```json
{
  "metric_id": "FILLED_ORDER_AMOUNT",
  "name": "Filled order amount",
  "owner": "Operations Analytics",
  "definition_text": "Sum of net amount for filled orders by fill month and site at fulfillment.",
  "unit": "currency units",
  "source_view": "WH_ORDER_METRIC_SOURCE",
  "date_column": "METRIC_DATE",
  "location_column": "LOCATION_KEY",
  "value_column": "NET_AMOUNT",
  "aggregation": "SUM",
  "allowed_dims": ["CHANNEL"],
  "allowed_filters": ["CHANNEL"]
}
```

Use `COUNT` with no `value_column` for a row count, or `AVG` with a numeric `value_column` for a row-level average. Add only fields that are safe for users to filter or group. The `location_column` is mandatory even when users will not select it as a filter; the query compiler injects the location access predicate independently.

Run `scripts/metricctl.py draft`, inspect the saved version with `show`, preview it under the drafting administrator's own location grants, review it with that administrator's token, and certify it with a **different active administrator**. Exact commands are in [ADOPTION.md](ADOPTION.md#4-draft-review-and-certify). The metric's meaning, owner, source contract, and review records are saved in Oracle, not merely in the JSON file. The source rows stay in the source database objects.

## 4. Test the mapping and location boundary

Use synthetic fixtures or a controlled non-sensitive test environment to check:

1. The adapter has the expected fact grain after every join, with no duplicate identifiers.
2. Location keys match registered `WH_SEC_LOCATION` keys and are never null for included facts.
3. An analyst granted one location gets only that location's facts, even when grouping by a broader dimension or requesting a different location in an allowed filter.
4. A user with no grant, a revoked grant, a revoked user, or an inactive location cannot receive an unscoped number.
5. The same metric with the same period and filters yields the same SQL shape and uses binds for every value.
6. The answer log stores the metric version, user, SQL, binds, and result count without a bearer token.

The [synthetic requisition adapter and manifest](../examples/filled_requisitions.json) give a runnable example. In the FreeSQL exercise, grants for Florida sites `1` and `2` produced 13 Q3 2026 filled requisitions; an Alabama-only grant for site `4` produced 5. The whole Florida synthetic set has 20. Those are checks of the seed and grants, not values to assume for another organization.

## 5. Add more metrics and calculation types

One view can support multiple manifests. For example, `COUNT` of filled rows, `SUM` of `NET_AMOUNT`, and `AVG` of `NET_AMOUNT` are three separately reviewed meanings with potentially different units or exclusions. `COUNT` counts source rows, not distinct IDs; `AVG` averages source values, not already-aggregated averages. Keep each metric's business definition explicit.

A ratio, rate, median, distinct count, or weighted average is **not** a manifest-only option in the current compiler. To add one:

1. Write the business formula, numerator/denominator grain, null and zero-denominator behavior, date attribution, and location rule with its owner.
2. Add a named aggregation kind and fixed SQL shape in `app/registered_metrics.py`; extend type and view validation in `app/registered_repo.py`.
3. Add a reviewed, non-destructive catalog constraint migration and update `WH_METRIC_GOVERNANCE` to validate that kind. Existing installations need the migration; editing the create-table file alone does not change an existing constraint.
4. Add synthetic tests for totals, edge cases, half-open periods, unsupported fields, SQL injection attempts, and two users with disjoint location grants. Do not allow model- or user-supplied SQL into the certified compiler.
5. Create a **new** metric version, preview it, have one administrator review and another certify it. Certification retires the prior version; if its source view changed earlier, that prior version stops answering immediately.

Changing an adapter view's definition changes its reviewed DDL fingerprint, even when Oracle's second-resolution DDL timestamp does not change. The app rejects that metric until a new version has passed review and certification, even if its manifest fields are unchanged. This makes source changes an explicit release step.

## 6. Decide whether to enable Select AI

Oracle Select AI is optional. It can draft SQL over an explicitly listed set of approved `WH_` views for an administrator to inspect. It does not execute the draft, grant row access, or certify a result. The app remains fully usable for governed metric answers with `SELECT_AI_ENABLED=false`. If your organization permits provider access, follow [SELECT_AI_SETUP.md](SELECT_AI_SETUP.md) and review which schema metadata and prompts may reach that provider before enabling the preview endpoint.
