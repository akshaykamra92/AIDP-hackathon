# Approved questions, complex metrics, and reusable answers

The design separates three records: **a certified metric** defines what to calculate; a future **approved question template** would map wording and parameters to that metric; an optional **cached result** stores one already calculated answer for a limited time. Reusing an answer is safe only when the request has the same fully specified metric parameters and current access and freshness checks pass.

## What exists now

| Piece | Current implementation |
| --- | --- |
| Certified meaning | `WH_REGISTERED_METRIC` stores versioned definitions, approved source-view columns, grouping and equality-filter allowlists, and review/certification state. The trusted CLI authenticates administrator bearer tokens; `WH_METRIC_GOVERNANCE` checks the recorded reviewer and certifier IDs differ, but does not authenticate those tokens itself. |
| Complex source mapping | A same-schema `WH_` adapter view can contain reviewed joins, eligibility rules, and calculations. The fixed compiler accepts `COUNT`, `SUM`, `AVG`, and `RATIO_SUM` over one such view. See the [four-table synthetic ratio example](../examples/on_time_fulfillment.md) and [EXTENDING.md](EXTENDING.md). |
| Portable question interface | `POST /metric-query` accepts a structured metric ID, half-open full-month period, approved dimensions, and scalar equality filters. The portable mode does **not** yet resolve arbitrary natural-language questions. The synthetic demo has a separate conservative parser in `app/intent.py`. |
| Location security | Portable reads require an active bearer identity. The compiled source query checks `WH_APP_USER`, `WH_USER_LOCATION`, and `WH_SEC_LOCATION` in the same SQL statement, using the authenticated user ID. |
| Provenance | `WH_REGISTERED_ANSWER_LOG` records each served answer's metric version, user, request, fixed SQL, binds, and row count. `CACHE_STATUS` marks a cache `HIT` or `MISS`; the log is **not** a result cache or a question-template library. |
| Source integrity | Review records the owned adapter view's full DDL SHA-256. A changed view blocks a certified answer until a new metric version is reviewed and certified. This fingerprint does **not** detect new or changed source rows. |
| Optional result reuse | `WH_QUERY_CACHE` stores a bounded result per authenticated user and typed request when the app flag and exact metric-version policy are enabled. FreeSQL verified a 30-minute `MISS` then `HIT` with an unchanged calculation timestamp and a 1,800-second expiry interval. A separate one-second policy missed after expiration. |

The question-template and richer typed parameter-contract objects below are **future plans**. The cache and `RATIO_SUM` are implemented. In FreeSQL, the four-table ratio's scoped draft preview returned `0.5` and its version passed review and certification by distinct administrators. These checks do not establish every cache invalidation or concurrency scenario. Neither a natural-language template endpoint nor multi-value/range parameter operators are exposed by the portable API.

## Proposed natural-language question path

```text
authenticated question
  → retrieve approved template candidates
  → extract every material qualifier and parameter
  → reject or clarify anything ambiguous or unmapped
  → validate the metric's typed parameter contract
  → resolve relative dates to explicit [start, end) dates
  → canonical certified request
  → check current metric, view, user, grants, and data revision
  → safe result-cache hit, or compile and execute fixed SQL with binds
  → log provenance and return the answer's as-of time
```

The template retrieval and qualifier extraction above are planned; the current portable UI starts with an explicit structured request. In a future wording layer, “How many filled requisitions in Florida in Q3 2026?” and “Q3 2026 filled reqs for FL?” could resolve to the same request. “Only nursing requisitions” must add the approved `JOB_FAMILY` filter; it cannot reuse the unfiltered answer. “Excluding reopened requisitions” needs an approved field and rule. If those are absent, return an exploratory refusal with a certification request, never a certified answer with that phrase silently omitted.

An illustrative normalized request for the existing synthetic metric is:

```json
{
  "metric_id": "FILLED_REQUISITIONS",
  "metric_version": 2,
  "period": {"start": "2026-07-01", "end": "2026-10-01"},
  "dimensions": [],
  "filters": {"STATE": "FL"}
}
```

The version number above is illustrative; the active certified version is read from Oracle. Today, the structured request uses explicit dates, the compiler sorts filter **names**, and the cache identity includes typed binds and fixed SQL rather than the wording of a question. The current compiler preserves the caller's approved dimension order, so reversing dimensions produces a different output shape and key. A future wording layer should resolve relative periods to concrete dates, normalize approved catalog codes, deduplicate multi-value sets, and apply documented defaults before lookup. It must preserve database value semantics: arbitrary free text must not be case-folded if that would change an equality comparison. A canonical dimension order could improve reuse, but it needs a reviewed output contract.

### Future question catalog blueprint — not installed

The following is a design for a **future, non-destructive installer**, not a claim about objects in the current schema or a supported API route. A template maps wording to an already certified metric; it never holds a saved number or executable SQL. A cached result is a separate `WH_QUERY_CACHE` row for one fully resolved request. The proposed three-table contract is:

| Proposed table | Required columns and keys | Rules enforced at publication |
| --- | --- | --- |
| `WH_QUESTION_TEMPLATE` | `TEMPLATE_ID`, `VERSION` composite primary key; `METRIC_ID`, `METRIC_VERSION` composite foreign key to `WH_REGISTERED_METRIC`; `TITLE`, `OWNER`, `LOCALE_CODE`, `NORMALIZER_VERSION`, `STATUS`, creator/reviewer/certifier IDs and timestamps. | `STATUS` is `DRAFT`, `REVIEWED`, `CERTIFIED`, or `RETIRED`; only one certified version per `TEMPLATE_ID`. Certification requires the referenced metric version still be certified and its reviewed source contract valid. |
| `WH_QUESTION_ALIAS` | `ALIAS_ID` identity primary key; `TEMPLATE_ID`, `VERSION` foreign key; `ALIAS_TEXT`, bounded `NORMALIZED_TEXT`, `LOCALE_CODE`, `NORMALIZER_VERSION`, `STATUS`. | A certified normalized phrase must map to only one active template in a locale. A proposed [conditional unique index](https://docs.oracle.com/en/database/oracle/oracle-database/26/sqlrf/CREATE-INDEX.html) on alias-local `CASE WHEN STATUS = 'CERTIFIED' THEN ... END` expressions rejects active collisions; draft and retired aliases may coexist. The package changes alias and template states in one transaction. |
| `WH_QUESTION_PARAMETER` | `(TEMPLATE_ID, VERSION, PARAM_NAME)` primary key and template foreign key; `PARAM_ROLE` (`PERIOD`, `FILTER`, `DIMENSION`), approved `TARGET_COLUMN`, `VALUE_TYPE`, `OPERATOR_KIND`, `REQUIRED_FLAG`, `MIN_ITEMS`, `MAX_ITEMS`, typed `DEFAULT_JSON`, `ALLOWED_VALUES_JSON`, `VALUE_MAP_JSON`, `BOUNDS_JSON`, `NULL_POLICY`, and optional enumerated `DEPENDENCY_CODE`. | The target column and operation must be allowed by the linked metric and fixed compiler. Period uses the metric date contract; every filter and grouping maps to an approved column. JSON metadata is validated against a narrow schema and cannot contain SQL, predicates, or identifiers outside the catalog. `DEPENDENCY_CODE` selects a reviewed rule; it is never a predicate expression. For textual JSON in Oracle 26ai, use `NOT NULL` where required plus [strict `IS JSON` checks](https://docs.oracle.com/en/database/oracle/oracle-database/26/sqlrf/is-json-condition.html); `IS JSON` alone does not reject SQL `NULL`. |

For example, a future `INTERNAL_FILL_RATE` template could declare `PERIOD` as required `DATE`/`HALF_OPEN_RANGE` on `METRIC_DATE`, `JOB_FAMILY` as optional `TEXT`/`EQ` on an allowed column, `REOPENED_FLAG` as optional `TEXT`/`EQ` with default `N`, and `CHANNEL` as `TEXT`/`IN` with one to ten approved codes. An alias such as “excluding reopened” maps to the approved `N` value through a fixed value map, never a SQL condition stored in a row. A range such as `OPEN_DAYS` needs an approved numeric source column, explicit inclusive/exclusive bounds, and a new reviewed compiler shape; it does **not** work in today's scalar-equality API.

This is a logical blueprint, not migration SQL. The eventual installer should create explicit status/check constraints, foreign keys, a unique certified-version index, and the active-alias index, then verify them. Alias text is normalized for **candidate lookup** with a reviewed, versioned locale rule, for example Unicode NFKC, whitespace collapse, and case folding. Preserve numbers, negation, punctuation with meaning, and every qualifier. Normalization must not change the case or meaning of a value bound for database equality. Store `NORMALIZER_VERSION`; any rule, alias, default, slot, or linked metric change makes a **new template version**. An active-alias collision blocks publication; an ambiguous parse refuses the answer. Before a template is used, the linked metric must still be certified; changing or retiring that metric makes its old templates ineligible until reviewed again.

The proposed package interface below is a **signature sketch only**, not an installed or compiled package. Its body would validate all catalog references and typed metadata, freeze a reviewed version, retire the prior certified version and its aliases atomically, and check distinct reviewer/certifier IDs. The trusted administrator CLI would authenticate their bearer tokens before passing those IDs. A package `AUTHID DEFINER` clause governs Oracle privilege resolution, **not** human identity verification; the current shared schema owner can call packages directly, so a production split of database roles is required before treating this as database-enforced separation of duties. [Oracle package rules](https://docs.oracle.com/en/database/oracle/oracle-database/26/lnpls/package-body.html) require the eventual specification and body headings to match.

```sql
CREATE OR REPLACE PACKAGE WH_QUESTION_GOVERNANCE AUTHID DEFINER AS
  PROCEDURE CREATE_DRAFT(
    p_template_id IN VARCHAR2, p_metric_id IN VARCHAR2,
    p_metric_version IN NUMBER, p_owner IN VARCHAR2,
    p_actor_id IN VARCHAR2, p_version OUT NUMBER);
  PROCEDURE PUT_ALIAS(
    p_template_id IN VARCHAR2, p_version IN NUMBER,
    p_locale_code IN VARCHAR2, p_alias_text IN VARCHAR2,
    p_actor_id IN VARCHAR2);
  PROCEDURE PUT_PARAMETER(
    p_template_id IN VARCHAR2, p_version IN NUMBER,
    p_param_name IN VARCHAR2, p_param_role IN VARCHAR2,
    p_target_column IN VARCHAR2, p_value_type IN VARCHAR2,
    p_operator_kind IN VARCHAR2, p_required_flag IN CHAR,
    p_min_items IN NUMBER, p_max_items IN NUMBER,
    p_default_json IN CLOB, p_allowed_values_json IN CLOB,
    p_value_map_json IN CLOB, p_bounds_json IN CLOB,
    p_null_policy IN VARCHAR2, p_dependency_code IN VARCHAR2,
    p_actor_id IN VARCHAR2);
  PROCEDURE REVIEW_DRAFT(
    p_template_id IN VARCHAR2, p_version IN NUMBER,
    p_reviewer_id IN VARCHAR2, p_note IN VARCHAR2);
  PROCEDURE CERTIFY(
    p_template_id IN VARCHAR2, p_version IN NUMBER,
    p_certifier_id IN VARCHAR2);
  PROCEDURE RETIRE(
    p_template_id IN VARCHAR2, p_version IN NUMBER,
    p_actor_id IN VARCHAR2);
END WH_QUESTION_GOVERNANCE;
```

Only `DRAFT` versions may be edited. `REVIEW_DRAFT` validates and freezes the complete alias and parameter set. `CERTIFY` rejects a missing review, a stale or retired metric, an alias collision, or the same reviewer and certifier; it retires the previous active version and aliases before publishing the new ones in a single transaction. No package signature accepts query text, formula text, join clauses, or executable SQL. The web app would only **read** certified templates; it would have no template publish route.

Start with exact aliases and documented synonyms. Optionally add a separate `WH_QUESTION_ALIAS_VECTOR` table keyed by `ALIAS_ID`, with a fixed-dimension [Oracle `VECTOR(n,FLOAT32)`](https://docs.oracle.com/en/database/oracle/oracle-database/26/sqlrf/Data-Types.html) embedding and an embedding-model ID, only after the model and database grants are verified. [`VECTOR_DISTANCE`](https://docs.oracle.com/en/database/oracle/oracle-database/26/vecse/vector_distance.html) may rank candidates; an approximate index is optional for a small catalog. A nearest neighbor is only a suggestion: deterministic extraction, the typed contract, and complete qualifier accounting decide whether a certified request exists. The ordinary alias path must work without embeddings or a vector index.

For that future installer, first inventory existing question objects and add only missing columns/constraints without resetting metrics or cache rows. Verify `USER_OBJECTS` validity, foreign keys, conditional uniqueness, package status transitions, and token handling with synthetic drafts. Exercise two aliases for one request, a collision across templates, changed negation, unsupported qualifiers, a missing required parameter, reordered multi-value codes, a retired metric, and two distinct authenticated administrators. Confirm no template or parameter table has an executable SQL-fragment column and no browser route can publish. None of this migration or verification has been run in FreeSQL yet.

## One metric can accept several approved parameters

The current compiler permits one value per approved equality-filter column. A proposed versioned parameter contract can add bounded, typed operations while keeping SQL shapes in reviewed application code:

| Parameter kind | Example | Fixed compilation rule |
| --- | --- | --- |
| Required enum | `JOB_FAMILY = NURSING` | Validate a catalog code, then bind one value to an approved column. |
| Optional approved code | `REOPENED_FLAG = N` | Resolve its documented default, then bind the approved flag value. The wording “excluding reopened” maps to `N`. |
| Bounded multi-value set | `CHANNEL in [WEB, REFERRAL]` | Deduplicate and sort codes; cap the set; generate only the reviewed `IN` shape with one bind per member. |
| Numeric range | `OPEN_DAYS from 7 through 30` | Validate type and bounds; compile fixed `>=` and `<=` or half-open predicates with binds. Record inclusivity in the contract. |
| Reporting period | Q3 2026 | Use the existing `[start_date, end_date)` month contract, with both dates bound. |
| Grouping | By job family and state | Validate against the metric's allowed dimensions and use catalog order. |

An illustrative **future** contract might say `JOB_FAMILY` is optional with a known code list, `CHANNEL` permits up to 10 values, `OPEN_DAYS` permits an inclusive integer range, and `REOPENED_FLAG` defaults to `N`. The contract needs a version, owner, allowed column and type, operation, cardinality, default, null behavior, and any dependence between parameters. A request containing a parameter or operator outside that contract is refused. Neither the browser nor a manifest may provide a SQL expression, join, predicate fragment, function name, or arbitrary operator.

### A many-table rate example

Suppose an organization asks for **internal fill rate for nursing requisitions at eligible sites, excluding reopened requisitions**. Its data owner first defines the denominator grain: one eligible filled requisition, attributed to its fill date and its location at fill time. A reviewed `WH_` adapter view can join requisitions, fill events, candidate source, site history, and job-family mapping. It deduplicates child rows and exposes one row per eligible fact with `METRIC_DATE`, `LOCATION_KEY`, `JOB_FAMILY`, `REOPENED_FLAG`, `INTERNAL_FILL_VALUE` (0 or 1), and `ELIGIBLE_FILL_VALUE` (0 or 1). The view documents missing source values and exclusions. It must not merge facts from several locations into an unscopable row.

A reviewed `RATIO_SUM` metric compiles a fixed fractional formula equivalent to `SUM(INTERNAL_FILL_VALUE) / SUM(ELIGIBLE_FILL_VALUE)`, guarded by non-null components and a **positive** denominator sum, plus approved bound filters and the same-statement location grant predicate. A nonpositive or missing denominator or any null component yields a null group; the repository refuses the **whole grouped answer** rather than silently dropping it. For this 0/1-flag example, the output is a fraction from 0 to 1; displaying percent requires an explicitly reviewed display conversion. The implemented [on-time fulfillment example](../examples/on_time_fulfillment.md) uses four synthetic source tables and passed a `0.5` draft preview and separate review/certification in FreeSQL. For prior-quarter comparison, issue two certified period requests and label client-side arithmetic as exploratory, or add and review a separate fixed comparison calculation so the delta itself has certified provenance.

The metric version names approved column identifiers, not formula SQL. The compiler contains the reviewed `RATIO_SUM` shape; `WH_METRIC_GOVERNANCE` validates numeric components, and the catalog installer migrates older schemas to the new check constraint without deleting metric rows. Review a new metric version when the view, formula implementation, allowed parameters, or meaning changes. The current view fingerprint protects the adapter DDL; a future compiler-release identifier should also be recorded for cache invalidation and provenance.

## Result reuse with a 30-minute policy

The optional cache uses `WH_CACHE_POLICY` for per-certified-version opt-in and TTL, `WH_CACHE_SOURCE` for a monotonically increasing revision of each adapter's underlying data, `WH_CACHE_STATE` for one global access revision, and `WH_QUERY_CACHE` for bounded, per-user JSON results. A cache identity hashes the typed binds, fixed compiled SQL, metric/version, source-view DDL digest, authenticated user ID, and current access/data/policy revisions. The current format version and compiled SQL capture query-shape changes; an explicit compiler release ID would make future semantic invalidation easier to audit. `WH_CACHE_ADMIN` sets a policy, bumps a source revision, and purges expired rows; security-table triggers advance the global access revision in the same transaction as any user, grant, or location change. `RESULT_CACHE_ENABLED` must also be enabled in the app; a new policy defaults to disabled. The web app has no public cache mutation route. FreeSQL verified the installer and package, a 30-minute `MISS` → `HIT` with identical calculation-start time and exactly 1,800 seconds between `GENERATED_AT` and `EXPIRES_AT`, and a separate one-second policy that returned `MISS` after expiry. The ratio policy used for that short check was restored to disabled.

Install the optional cache **after** the catalog and security layers. In a disposable example where `FILLED_ORDERS` version `1` is certified, the commands are:

```bash
python scripts/install_cache.py --dry-run
python scripts/install_cache.py --apply
python scripts/install_cache.py --verify-only
python scripts/cachectl.py set-policy \
  --metric-id FILLED_ORDERS --version 1 --enabled Y --ttl-seconds 1800 \
  --admin-token-file work/tokens/catalog_admin.token
```

Use the actual certified version and private administrator token file in your installation. Set `RESULT_CACHE_ENABLED=true` in the local `.env` or deployment environment and restart the app. The cache policy and app flag must both be enabled for reuse. [ADOPTION.md](ADOPTION.md#6-optional-result-cache) gives the manual invalidation and pruning commands.

Example for a metric whose maximum age is 30 minutes:

1. At 09:00, an authenticated analyst submits a structured metric/version, period, dimensions, and filters request. With no eligible cache row, Oracle UTC is captured just **before the source query starts**. The fixed query executes under that user's current location grants; the result is cached for **that user** until 09:30, stamped with the current access and source-data revisions. `GENERATED_AT` and `EXPIRES_AT` use that calculation-start time, not the later cache-write time.
2. At 09:05, the same structured request can reuse the row after all authorization and revision checks. A future alias resolver could map two paraphrases to this same request. A changed qualifier, grouping, period, or parameter value gives a different key.
3. At 09:12, a new source load commits and advances that adapter's `WH_CACHE_SOURCE.DATA_REVISION` in the **same transaction**. The 09:00 row is ineligible immediately, even though its 09:30 expiration has not arrived.
4. At 09:16, a location grant is revoked. The `WH_USER_LOCATION` trigger advances `WH_CACHE_STATE.ACCESS_REVISION` in the same transaction. The earlier aggregate is ineligible; the next answer is recomputed for the remaining active grants.
5. At 09:30, the row expires regardless of whether revisions changed. The next request recomputes it. An optional bounded warmer for approved templates would be future work; current refresh is lazy on request.

The 30-minute setting is a **maximum age from source-query start**, not a guarantee of current data. A query that takes longer than its TTL can produce an entry already expired when written. Strong freshness needs trusted ingestion to call `WH_CACHE_ADMIN.BUMP_SOURCE_REVISION` for every affected adapter **in the transaction that changes its underlying rows**, before commit. Inventory the full dependency closure: base tables, nested views, tables behind those views, and any materialized source. An insert, update, delete, or correction to any dependency must advance each affected adapter revision. The following command can invalidate already committed data manually, but a separate ingestion commit leaves a stale interval before it runs:

```bash
python scripts/cachectl.py bump-source \
  --source-view WH_ORDER_METRIC_SOURCE \
  --admin-token-file work/tokens/catalog_admin.token
```

If an external system can alter the data without advancing the revision, a 30-minute TTL only bounds staleness; disable caching for metrics that cannot accept that limit. The implemented DDL fingerprint covers the **registered adapter view**. It does not fingerprint nested view definitions or detect changed underlying rows; review nested-view changes separately, release a new metric version when their meaning changes, and bump the affected source revision with their data changes.

### Exact cache authorization

An aggregate cannot be safely trimmed after retrieval when a user's grants change. The implementation uses **per-user** cache entries. On every hit, the app authenticates the current bearer token, verifies the metric is still certified and its owned view still matches the reviewed digest, and runs one Oracle cache-read statement that requires:

- The cache user ID equals the authenticated user ID; `WH_APP_USER` is active.
- At least one active `WH_USER_LOCATION` row joins an active `WH_SEC_LOCATION` row for that user.
- The current global access revision equals the cache row's revision. Insert, update, or delete triggers on `WH_APP_USER`, `WH_USER_LOCATION`, and `WH_SEC_LOCATION` increment this revision transactionally, so any changed effective grant set invalidates **all** older entries. Every write updates one `WH_CACHE_STATE` row; high-volume access administration may serialize on that row and reduce cache reuse. The revision triggers must remain enabled and valid; a direct schema change that bypasses them breaks this invariant.
- The metric/version, fixed SQL and typed-bind cache identity, source DDL digest, data revision, policy revision, and expiration match the cache row.

The app never accepts a user ID, location set, or cache key as an authority from a request body. A cache miss executes the existing location-scoped certified SQL. If a revision changes between calculating and inserting a result, that row carries the earlier revision and fails the next hit check. If any cache check fails, execute the certified path again or fail closed; never return an older unscoped aggregate. Retired metrics, revoked users, inactive locations, and revoked grants must not have a cache bypass. A cache hit checks current active grants and the matching global revision; it does not re-aggregate source rows. An exact location-set snapshot check could be added as defense in depth, but is not part of this implementation. Future cross-user sharing would need a separately reviewed scope policy.

Each request still needs an audit record. The answer-log extension records `CACHE_STATUS`, `CACHE_KEY`, and `RESULT_GENERATED_AT` for cache `HIT` and `MISS` attempts, distinguishing a reused answer from a new calculation. These columns are null for older, disabled, and bypassed answers; the response separately reports `HIT`, `MISS`, `DISABLED`, or `BYPASS` and calculation-start time. A future improvement can also show the source batch's `data_as_of` time. A certified grouped answer has a **500-row** limit: the compiler fetches a 501st sentinel row and the repository refuses the entire result if it exists. For `SUM` and `AVG`, a group with only null values surfaces as null and refuses the entire answer. Each cached result is also limited to 256 KiB; the cache table has **no total entry cap** and expired rows are not removed automatically. Schedule this command, for example hourly, from an external deployment scheduler:

```bash
python scripts/cachectl.py purge-expired \
  --admin-token-file work/tokens/catalog_admin.token
```

Check FreeSQL privileges before considering an Oracle `DBMS_SCHEDULER` job; an external scheduler works when `CREATE JOB` is unavailable. Track cache row count and storage, and define an operational retention limit in addition to expired-row purging. Keep bearer tokens out of cache and logs; review whether cached result values are suitable for the target data classification.

## Oracle acceleration choices

| Choice | Good use | Limit |
| --- | --- | --- |
| Application-managed `WH_QUERY_CACHE` | Reuse one canonical, authorized answer across equivalent structured requests and app instances; show its calculation-start time and provenance. A future alias resolver could map paraphrases to the same request. | Requires explicit policy, full dependency revision workflow, access checks, and pruning. FreeSQL verified a 30-minute `MISS` → `HIT` and a one-second expiry miss; other invalidation and concurrency cases still need target-specific checks. |
| Oracle SQL server result cache | Reuse an equivalent compiled SQL result with matching bind values. Oracle invalidates results after dependent database data changes. [Tuning guide](https://docs.oracle.com/en/database/oracle/oracle-database/26/tgdba/tuning-result-cache.html). | It does not resolve paraphrases. Cache hints are requests, not guaranteed storage. Session-context and nondeterministic-function dependencies need targeted security testing; [PL/SQL result cache](https://docs.oracle.com/en/database/oracle/oracle-database/26/lnpls/pl-sql-function-result-cache.html) does not automatically track session-specific application contexts. Do not enable it blindly on the current identity-scoped query. |
| `WH_` materialized view | Precompute expensive joins or aggregates while retaining an event-time `LOCATION_KEY` and date for the final access-scoped query. [Materialized view guide](https://docs.oracle.com/en/database/oracle/oracle-database/26/dwhsg/basic-materialized-views.html). | Refresh may be `ON DEMAND` or, for eligible views, `ON COMMIT`; use [`DBMS_MVIEW.EXPLAIN_MVIEW`](https://docs.oracle.com/en/database/oracle/oracle-database/26/dwhsg/refreshing-materialized-views.html) to check fast-refresh capability. Do not materialize a session-specific access decision into shared data. |

These optimizations can be combined: a materialized view reduces computation per miss, while an application cache reuses a result previously calculated under the user's scope. Every source query must apply the active-user/location predicate; a cache hit must pass the current user/grant and matching revision checks described above. Oracle [Select AI `showsql`](https://docs.oracle.com/en-us/iaas/autonomous-database-serverless/doc/select-ai-examples.html) remains an administrator-only exploratory SQL preview in this app; generated SQL is never the certified calculation or a cache key.

## Rollout and verification

1. Use the implemented `RATIO_SUM` only for reviewed additive components. For a new calculation kind, change the fixed compiler, catalog migration, governance package, tests, and metric version together. Keep the existing equality-only route working.
2. Add the **future** `WH_QUESTION_TEMPLATE`, `WH_QUESTION_ALIAS`, and `WH_QUESTION_PARAMETER` objects from the blueprint above with separate governance. Start with a small approved phrase set and explicit slot extraction. Add optional vector retrieval only after the database and embedding-model path are verified. Introduce multi-value and range operators through versioned typed contracts and reviewed compiler shapes.
3. Install the optional cache in each target schema and verify the package, triggers, and per-metric policy. Inventory each adapter's nested-view/source dependency closure and wire every source load to advance its adapter revision **in the same transaction**. Keep caching **off** for a metric until that freshness contract is defined or a TTL-only staleness limit is explicitly accepted.
4. Verify per-user cache writes and guarded hits, answer-log provenance, calculation-start expiry, scheduled pruning and storage retention, and access-revision triggers. An optional bounded warmer is future work. Only approved database packages should mutate policy or definitions through a trusted administrative account.
5. Test future wording support with synthetic questions: two paraphrases resolve to one canonical request; a material qualifier changes it; unsupported qualifiers refuse; required/missing and conflicting slots refuse; multi-value ordering and range bounds normalize correctly. Preserve the already-reviewed source grain, positive-denominator rule, and bind-only SQL.
6. Test two users with disjoint and overlapping locations, then revoke a grant, deactivate a location, revoke a user, rotate a token, retire or recertify a metric, change view DDL, publish a data revision, and expire the TTL. Each case must miss or deny as appropriate. Test concurrent refresh and access change before claiming those cases verified in a target deployment.

[Oracle FreeSQL](https://www.oracle.com/database/technologies/oracle-free-sql/) provides a personal Oracle schema and external client connectivity. This project's catalog, cache package, 30-minute `MISS` → `HIT`, and one-second expiry miss were verified in one FreeSQL schema. The proposed question catalog was **not** installed. FreeSQL's public overview does not promise vector, materialized-view, Oracle server result-cache, AI-profile, or scheduler privileges for every account. Probe the connected schema and keep the structured, fixed-SQL no-cache path usable if an optional feature is unavailable.
