# Project instructions

This repository is a hackathon prototype called **Ask the Warehouse**. Keep it portable to another organization by separating its metric definitions, approved SQL, and synthetic seed data from the question interface.

- Use only synthetic records. Never add real patient, employee, or company data.
- Keep every database object name prefixed `WH_`.
- A certified answer must come from an approved metric and fixed SQL with bind variables. Never execute user-written or model-written SQL as a certified answer.
- If a material qualifier cannot be mapped to a catalog field, return an exploratory refusal and offer a certification request. Do not silently drop the qualifier.
- Keep credentials in the local environment only. Do not print, log, commit, or place them in documentation.
- In portable mode, require an active bearer-token identity for every metric read. Scope each answer in its SQL statement to the user's active `WH_USER_LOCATION` grants, active `WH_SEC_LOCATION`, and active `WH_APP_USER`; never trust a user ID or location scope supplied in a request body.
- New portable metric manifests must declare a text location key column in an owned `WH_` adapter view. Keep historical facts at their event-time location and reject metrics that cannot be scoped per source row. Existing unscoped catalog versions must remain unavailable to portable readers until replaced by reviewed, scoped versions.
- Manage users and locations through `WH_SECURITY_ADMIN` and metric versions through `WH_METRIC_GOVERNANCE`. Require distinct authenticated administrators for metric review and certification. The web app must not expose either package as a public mutation API.
- Select AI is opt-in exploration for an authenticated administrator. Call only `DBMS_CLOUD_AI.GENERATE` with `showsql`, display generated SQL without executing it, and keep it out of the certified answer path. A Select AI object list is not a location-access policy.
- Use half-open date ranges `[start_date, end_date)` in metric queries. Treat the latest completed month or quarter as the default when applicable.
- Run the local tests before committing. Verify the public GitHub diff contains no secrets or real data.
- Portable metrics use an owned `WH_` adapter view and a typed manifest. Do not accept SQL text from a manifest or a question; only the fixed registered metric compiler may build a query.
- Create, review, and certify portable metric versions through `WH_METRIC_GOVERNANCE`. The web app can query certified versions but must not expose a catalog publish route.
- Add calculation kinds through reviewed compiler and governance changes with tests; never accept arbitrary SQL from a manifest, browser, or model.
- `scripts/deploy.py --apply` resets the synthetic demo schema. Use the non-destructive catalog and security installers for another organization's database.

## Extending the question engine

- Read `docs/QUESTION_ENGINE.md`, `docs/EXTENDING.md`, and `docs/SECURITY.md` before changing question resolution, metric compilation, caching, or source adapters. Keep the docs aligned with what is implemented versus proposed.
- Resolve a question into a **complete typed request** (certified metric ID/version, period, dimensions, and every material filter) before executing or caching an answer. Rewordings may share a result only when they produce the same request. If a qualifier or required parameter cannot be resolved, ask for clarification or refuse certification; never drop it.
- Treat embeddings or Select AI as candidate discovery only. A similarity score, generated SQL statement, or cached natural-language answer cannot certify a metric.
- Cache only location-scoped certified results for a verified user with a declared freshness policy. A cache key must cover metric version, fixed compiled SQL, typed parameters, source change revision, and access change revision. Add an explicit compiler release ID if future semantic code changes would leave SQL unchanged. Recheck current identity, grants, certification, source integrity, and expiry on every hit. Record cache hits and fresh executions distinctly in the answer audit.
- For source views that join multiple tables, preserve the fact grain and one event-time `LOCATION_KEY` per contributing row. Advance the source revision when ingestion changes, deletes, or corrects a dependency; a timer bounds staleness but cannot detect an early load. Disable caching when source or policy dependencies cannot be tracked safely.
- Add complex formulas as named, fixed compiler kinds over validated view columns. Keep join logic and conditional eligibility in reviewed `WH_` adapters; bind values for approved parameters. Do not store executable SQL fragments in metric rows, question templates, or cache policies.
