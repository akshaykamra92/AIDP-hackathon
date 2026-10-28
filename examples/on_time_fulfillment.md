# Worked RATIO_SUM metric: on-time fulfillment

This optional example uses only synthetic orders. It shows how a question that
needs several joins and several qualifiers can still produce a governed answer.
The approved adapter [view](on_time_fulfillment.view.sql) joins four `WH_`
sources, but the application accepts only the typed [manifest](on_time_fulfillment.json)
and bound filter values. Source SQL never comes from the question.

## Build the synthetic source

1. Install the non-destructive catalog and security objects using the commands
   in [ADOPTION.md](../docs/ADOPTION.md).
2. In a FreeSQL worksheet or SQLcl, run
   [on_time_fulfillment.sources.sql](on_time_fulfillment.sources.sql), then
   [on_time_fulfillment.view.sql](on_time_fulfillment.view.sql). The source
   script creates four optional `WH_EXAMPLE_` tables and inserts six synthetic
   orders if missing. Neither installer runs this example automatically.
3. Register `DEMO-A` and `DEMO-B` as active location keys with
   `scripts/userctl.py register-location`. Grant a test reader and each
   administrator only the locations they should see. The view also checks
   Oracle `CLIENT_IDENTIFIER`; an unbound database session sees no rows.
4. Use `scripts/metricctl.py draft examples/on_time_fulfillment.json`, preview
   version 1 under the drafting administrator's own grants, review it with one
   active administrator, and certify it with a different active administrator.
   Use the version printed by `draft` if this ID already existed. Exact CLI
   arguments and token-file handling are in [ADOPTION.md](../docs/ADOPTION.md).

For example, a certified reader with both demo locations can query:

```bash
python3 scripts/metricctl.py query ON_TIME_FULFILLMENT_RATIO \
  --token-file work/tokens/reader.token \
  --start 2026-07-01 --end 2026-10-01 \
  --filter STATE=FL --filter CHANNEL=DIRECT
```

That selection returns `1 / 2 = 0.5`: one on-time order among two Florida
direct orders. Without filters, both locations together have `3 / 5 = 0.6`;
`DEMO-A` alone has `2 / 3`, and `DEMO-B` alone has `1 / 2`. These values are
checks of the optional synthetic fixture, not defaults for another database.

## Why the join and calculation are safe

- `WH_EXAMPLE_ORDER_LINE` has multiple rows for order `1001`. The view first
  reduces them to one `line_totals` row per order, then joins to the uniquely
  keyed shipment. Each fulfilled order therefore contributes exactly one
  numerator and one denominator component.
- The shipment stores `SITE_CODE_AT_FULFILLMENT`. The source view emits that
  event-time location key before the compiler applies active user grants.
- `PROMISED_AT` is a timestamp deadline. Oracle `DATE` includes a time of day,
  so the view compares the full `FULFILLED_AT` and `PROMISED_AT` values. The
  synthetic fixture uses midnight for both; an organization requiring an
  inclusive calendar-day deadline must review a different adapter expression.
- The reviewed `RATIO_SUM` SQL shape calculates
  `SUM(ON_TIME_NUM) / NULLIF(SUM(ELIGIBLE_DEN), 0)` after date, location, and
  approved filter conditions, only when the denominator sum is positive.
  Every group with a nonpositive or missing denominator or a null component
  yields a null value; the application refuses the entire
  grouped answer if any group is invalid. The result is a fraction; no implicit
  multiplication by 100 occurs.
- `STATE`, `CHANNEL`, and `ORDER_SIZE_BAND` are the only approved filters and
  groupings. A question with another material qualifier requires a new
  reviewed mapping or an exploratory refusal.

To adapt this elsewhere, replace the synthetic source tables and view joins
with the organization's approved sources. Keep the same fact grain, event-time
location, component meanings, and governance steps. If a requested formula
uses a non-additive denominator, define and review a different fixed
calculation kind; `RATIO_SUM` is only for ratios of additive components.
For optional caching, track every direct and nested source dependency of the
adapter and advance its `WH_CACHE_SOURCE` revision in the same ingestion
transaction as a change. The registered view's DDL fingerprint alone does not
detect underlying data or nested-view changes. Grouped answers are capped at
500 rows; a 501st group refuses the whole result.
