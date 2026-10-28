# Certified answers beside Oracle Select AI

[Oracle Select AI](https://docs.oracle.com/en/database/oracle/oracle-database/26/selai/select-ai-interfaces.html) can generate, show, explain, or run SQL from a natural-language prompt when an AI profile and provider are configured. That makes it useful for discovery. Ask the Warehouse adds a separate decision: **has this calculation, its business meaning, and the requester's data scope been approved for an official answer?**

| Step | Certified metric path | Optional Select AI path in this app |
| --- | --- | --- |
| Input | A registered metric ID, full-month period, and approved dimensions or filters. The synthetic demo also parses a narrow set of natural-language questions. | A question from an authenticated portable **ADMIN**. |
| Source context | A reviewed, owned `WH_` adapter view and a versioned manifest saved in Oracle; the reviewed full-view fingerprint is checked before an answer. | An enabled AI profile listing explicitly approved, owned `WH_` views. |
| SQL | The application's fixed `COUNT`/`SUM`/`AVG` compiler uses allowlisted identifiers and bind values. | `DBMS_CLOUD_AI.GENERATE(..., action => 'showsql')` produces SQL text for inspection. |
| Location access | The answer statement joins active user, grant, and location records against each source row's location key. | The endpoint is administrator-only and **does not execute** generated SQL. A profile object list is not row-level access control. |
| Output | A number plus approved definition, owner, version, SQL, binds, and answer-log record. | Exploratory SQL text with `executed: false` and `certified: false`. |
| Unsupported meaning | No certified number; propose a new reviewed metric. | A draft may help an analyst inspect possible source logic, but cannot publish a metric. |

## Example variations

| Question or situation | Ask the Warehouse | Select AI preview |
| --- | --- | --- |
| “What was our turnover in Florida last quarter?” | In the **synthetic Q3 2026 demo**, the certified definition excludes internal transfers and returns **1.89%** with its provenance. The portable explorer would require a separately registered, location-scoped turnover metric. | With a configured provider, it could draft SQL for inspection. No Select AI result was measured for this question. |
| “Turnover in Florida Q3 2026 including transfers” | The extra qualifier conflicts with the approved turnover definition, so the app refuses to certify it. A separate fixed demo comparison shows **2.33%** as an illustrative interpretation. | It could draft an alternate query for an analyst to review. **2.33% is not a Select AI output.** |
| “What's our burnout rate?” | No metric is certified, so the demo returns no number and offers a certification request. | A draft could suggest a source query, but cannot establish an approved business definition or owner. |
| A reader asks for a region containing both allowed and disallowed locations | The portable query aggregates only rows at the reader's active locations, regardless of the broader grouping or filter. | The preview route is unavailable to a reader. The profile's object list does not express that reader's location grants. |

The two turnover figures come from deterministic **synthetic** Oracle records. The four additional internal transfers and shared denominator can be checked in [`sql/03_verify.sql`](../sql/03_verify.sql). The FreeSQL demo has no AI provider credential or profile configured, so these are fixed application calculations, not live model outputs.

This project deliberately keeps exploration and certification separate. An analyst can use a generated draft to propose a `WH_` view and typed manifest. A steward checks its grain, exclusions, location key, and meaning; a different administrator certifies it. Only then can the fixed compiler produce an official number. A production deployment can leave `SELECT_AI_ENABLED=false` without affecting certified metric queries. [Select AI setup](SELECT_AI_SETUP.md) describes the optional preview and [adoption steps](ADOPTION.md) describe the approval flow.

Oracle's [usage guide](https://docs.oracle.com/en/database/oracle/oracle-database/26/selai/use-ai-keyword-enter-prompts.html) notes that generated SQL can fail or return an incorrect result. This is why the generated text here is never executed as a certified answer.
