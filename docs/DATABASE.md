# Ask the Warehouse database layer

This Oracle AI Database 26ai schema holds synthetic workforce data and a governed metric catalog for the hackathon demo. All application objects are prefixed `WH_`. No patient or employee data from a real organization is used.

## Run in FreeSQL

Execute these files in order, using the worksheet's script/run-all action so every statement runs:

1. [`sql/01_schema.sql`](../sql/01_schema.sql) — drops and recreates only the eight demo `WH_` tables, constraints, comments, and annotations. This also clears prior demo answer logs and metric requests.
2. [`sql/02_seed.sql`](../sql/02_seed.sql) — loads deterministic synthetic rows and five certified demo catalog entries. It may be rerun without recreating the tables; it resets those eight demo tables, including their logs and requests.
3. [`sql/03_verify.sql`](../sql/03_verify.sql) — read-only row-count, date-coverage, annotation, comment, and demo-metric checks.

These three demo SQL files contain standard Oracle SQL statements and no SQLcl-specific commands. They do not create an AI provider credential or Select AI profile.

## Data contract

| Table | Grain | Expected seed rows | Key use |
| --- | --- | ---: | --- |
| `WH_LOCATION` | One care location | 6 | `STATE` and location labels |
| `WH_EMPLOYEE` | One synthetic employee | 2,000 | Current roster attributes |
| `WH_TERMINATION` | One employment event | 240 | `TERM_DATE`, `TERM_TYPE`, and event-time `LOCATION_ID`/`JOB_FAMILY` |
| `WH_HEADCOUNT_MONTH` | Completed month-end × location × job family | 432 | Denominator snapshots |
| `WH_REQUISITION` | One staffing requisition | 360 | Fill date and internal-fill outcome |
| `WH_METRIC` | One approved metric/version | 5 | Definition, owner, allowlists, and compiler marker |
| `WH_METRIC_REQUEST` | One certification request | 0 initially | Requests for new metrics |
| `WH_ANSWER_LOG` | One answer provenance record | 0 initially | Metric version, compiled SQL, binds, result count |

The portable registered catalog is installed separately with `python scripts/install_catalog.py --apply`. It adds `WH_REGISTERED_METRIC` for versioned meanings and source-view contracts, `WH_REGISTERED_ANSWER_LOG` for user-attributed query provenance, and the `WH_METRIC_GOVERNANCE` package for draft, review, and certification transitions. Install its access layer with `python scripts/install_security.py --apply`: `WH_APP_USER`, `WH_SEC_LOCATION`, `WH_USER_LOCATION`, `WH_ACCESS_AUDIT`, and the `WH_SECURITY_ADMIN` package. These installers do not reset the eight synthetic tables or load source data. Portable certified queries require a bearer identity and an active location grant; see the [adoption guide](ADOPTION.md), [security guide](SECURITY.md), and [schema extension guide](EXTENDING.md).

The six locations are Orlando, Tampa, and Jacksonville in FL; Birmingham in AL; Dallas in TX; and Atlanta in GA. `JOB_FAMILY` is one of `NURSING`, `THERAPY`, `CLINICAL_SUPPORT`, or `OPERATIONS`.

`WH_HEADCOUNT_MONTH.MONTH_START` runs from **2025-04-01 through 2026-09-01**, inclusive: 18 completed monthly snapshots. `MONTH_START` is the first day of the month, but `HEADCOUNT` is measured at that month's end. This makes Q3 2026, `[2026-07-01, 2026-10-01)`, the latest fully completed quarter as of the hackathon date. Requisition metrics filter on `FILL_DATE`; employment-event metrics filter on `TERM_DATE`. All period comparisons use an inclusive start and exclusive end.

`WH_EMPLOYEE.LOCATION_ID` and `JOB_FAMILY` are current attributes. `WH_TERMINATION.LOCATION_ID` and `JOB_FAMILY` are event-time snapshots. Use the latter to attribute exits and transfers. Synthetic internal transfers intentionally have a different current location from their event location. This prevents a current-roster join from silently rewriting history.

## Certified metrics

All five rows have `STATUS='CERTIFIED'` and `VERSION=1`. The catalog stores the exact allowlists `ALLOWED_DIMS='["state","location","job_family","month","quarter"]'` and `ALLOWED_FILTERS='["state","location","job_family","month","quarter","period"]'`.

| `METRIC_ID` | Definition | Unit |
| --- | --- | --- |
| `HEADCOUNT` | At the end of the last completed month in the selected period. With month grouping, show each month-end; with quarter grouping, show each quarter's last month-end. Sum selected locations/jobs for each snapshot. | People |
| `TURNOVER_RATE` | Count `VOLUNTARY`, `INVOLUNTARY`, and `RETIREMENT` events in the period, divide by average monthly month-end headcount across the period, then multiply by 100. `INTERNAL_TRANSFER` is excluded. | Percent |
| `VOLUNTARY_TURNOVER_RATE` | Count `VOLUNTARY` events in the period, divide by the same average monthly denominator, then multiply by 100. | Percent |
| `TIME_TO_FILL_DAYS` | Median `FILL_DATE - OPEN_DATE` among requisitions filled in the period. | Calendar days |
| `INTERNAL_FILL_RATE` | Filled requisitions with `FILLED_INTERNALLY='Y'` divided by all requisitions filled in the period, then multiplied by 100. | Percent |

For a selected state/location/job group, the turnover denominator must first **sum** headcount across selected locations and job families separately for each month, then **average** those monthly totals. Averaging raw location/job rows would understate the denominator. The numerator uses event-time attributes on `WH_TERMINATION`. Turnover rates are period percentages, not annualized rates. A multi-month ungrouped headcount query returns the last month-end snapshot rather than summing snapshots.

`WH_METRIC.SQL_TEMPLATE` is the approved metric ID itself, for example `TURNOVER_RATE`. It is a **non-executable marker**. The backend maps that ID to reviewed fixed SQL and binds user values; it does not execute mutable SQL text from the catalog. The displayed compiled SQL and its bind values are written to `WH_ANSWER_LOG` for provenance.

## Designed ambiguity

The synthetic FL employment events contain exactly 24 `INTERNAL_TRANSFER` rows among 120 total events across the seeded history. For Q3 2026, the expected values are 21 total FL events, 4 internal transfers, and 17 approved exits. Average monthly FL headcount is about 901.67. The approved turnover rate is therefore **1.89%**; counting transfers yields **2.33%**. The latter is a deliberately plausible exploratory definition, not a certified answer. These are deterministic expectations; confirm them by running `sql/03_verify.sql` against the target database.

## Oracle 26ai details

The schema uses Oracle 26ai's `DROP TABLE IF EXISTS`, table and column annotations, and SQL comments. `DESCRIPTION` and `ALIASES` annotations support AI enrichment, while `BUSINESS_TERM` and `SYNONYMS` capture the terms requested for the demo. Oracle documents [the annotation syntax](https://docs.oracle.com/en/database/oracle/oracle-database/26/sqlrf/annotations_clause.html), [the `DROP TABLE IF EXISTS` clause](https://docs.oracle.com/en/database/oracle/oracle-database/26/sqlrf/DROP-TABLE.html), and [best practices for `DESCRIPTION` and `ALIASES`](https://docs.oracle.com/en/database/oracle/oracle-database/26/aienb/best-practices-enriching-your-database-schema.html).

A Select AI profile is optional for the **portable administrator's exploratory SQL preview**. Oracle's [profile prerequisites](https://docs.oracle.com/en/database/oracle/oracle-database/26/selai/manage-ai-profiles.html) include package privileges, an AI provider credential, and network access where applicable; this schema alone does not establish those. Oracle documents enabling annotations in a profile with `"annotations":"true"` in its [Select AI query examples](https://docs.oracle.com/en/database/oracle/oracle-database/26/selai/generate-and-improve-sql-queries.html). The [setup guide](SELECT_AI_SETUP.md) uses `scripts/select_ai_setup.py` to create an explicit owned-view profile after a DBA supplies a provider credential. The generated SQL is displayed for review and never executed by the preview endpoint. The certified path uses fixed SQL regardless of Select AI availability.
