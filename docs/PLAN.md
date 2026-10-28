# Ask the Warehouse: build plan

## Demo outcome

A user asks a natural-language metric question and receives either a certified result with its definition, owner, version, approved SQL, and freshness, or a clear refusal with a way to request certification.

## Work sequence

1. Create a public repository and a reproducible, synthetic Oracle schema.
2. Seed five governed metrics and enough history to show definition ambiguity.
3. Build a FastAPI endpoint that extracts a constrained intent, validates it against the catalog, compiles allowlisted SQL with binds, and records the answer.
4. Build a one-page interface for the answer and refusal flows.
5. Run golden questions, SQL checks, and a live FreeSQL smoke test.

## Acceptance checks

- Florida turnover for Q3 2026 excludes internal transfers; a separately labeled transfer-inclusive comparison is higher.
- Reworded questions with the same intent compile to the same approved SQL and binds.
- "Burnout rate" and questions with unsupported qualifiers cannot be shown as certified.
- The database can be rebuilt from checked-in scripts, and no password appears in Git history.

## Portability boundary

The original five-metric natural-language walkthrough remains tied to synthetic workforce data. The portable path lets an adopter expose its own data through a reviewed, same-schema `WH_` view and save versioned `COUNT`, `SUM`, `AVG`, or `RATIO_SUM` metric meanings in Oracle. It uses a structured certified metric explorer, approved identifiers, and bound values.

The implemented extension adds database-backed users and location grants, a separate administration package, CLI-authenticated review and certification with distinct administrator tokens, a fixed ratio-of-sums calculation, optional per-user certified result caching, and an optional administrator-only Select AI SQL preview. The metric package checks the recorded actor IDs differ; a shared schema owner can still call it directly. Source queries bind the authenticated user and check current grants in the same aggregate statement; cache hits use current user/grant and revision checks. Existing metric versions without a location column need a new scoped version. The [adoption](ADOPTION.md), [security](SECURITY.md), [extension](EXTENDING.md), [question and cache](QUESTION_ENGINE.md), and [Select AI setup](SELECT_AI_SETUP.md) guides describe the current implementation and production privilege boundary.

Approved natural-language question aliases and richer typed multi-value/range parameters are the next design stage. The portable browser currently submits a structured metric request with scalar equality filters; its cache reuses equivalent validated requests without interpreting free-form wording.
