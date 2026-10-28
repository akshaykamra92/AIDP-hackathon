# Project instructions

This repository is a hackathon prototype called **Ask the Warehouse**. Keep it portable to another organization by separating its metric definitions, approved SQL, and synthetic seed data from the question interface.

- Use only synthetic records. Never add real patient, employee, or company data.
- Keep every database object name prefixed `WH_`.
- A certified answer must come from an approved metric and fixed SQL with bind variables. Never execute user-written or model-written SQL as a certified answer.
- If a material qualifier cannot be mapped to a catalog field, return an exploratory refusal and offer a certification request. Do not silently drop the qualifier.
- Keep credentials in the local environment only. Do not print, log, commit, or place them in documentation.
- Use half-open date ranges `[start_date, end_date)` in metric queries. Treat the latest completed month or quarter as the default when applicable.
- Run the local tests before committing. Verify the public GitHub diff contains no secrets or real data.
- Portable metrics use an owned `WH_` adapter view and a typed manifest. Do not accept SQL text from a manifest or a question; only the fixed registered metric compiler may build a query.
- Create, review, and certify portable metric versions through `WH_METRIC_GOVERNANCE`. The web app can query certified versions but must not expose a catalog publish route.
- `scripts/deploy.py --apply` resets the synthetic demo schema. Use the non-destructive catalog installer for another organization's database.
