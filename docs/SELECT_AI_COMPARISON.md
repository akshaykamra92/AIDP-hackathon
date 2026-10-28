# Certified answers and Oracle Select AI

Oracle Select AI can turn natural-language prompts into SQL, run the generated query, and show or explain the SQL. It is useful for exploring data and developing queries. [Oracle's Select AI guide](https://docs.oracle.com/en/database/oracle/oracle-database/26/selai/select-ai-interfaces.html) describes these actions and their AI-profile setup.

Ask the Warehouse answers a narrower question: **does this request match an approved business metric and all of its qualifiers?** A certified answer uses a stored definition, owner, and version plus a fixed compiler with bind values. Unsupported or conflicting requests receive a refusal and can be sent for review. Select AI and this governed layer can complement each other: exploration can help an analyst propose a metric, while certification controls what becomes an official answer.

| Question | Ask the Warehouse behavior | Select AI comparison |
| --- | --- | --- |
| “What was our turnover in Florida last quarter?” | The synthetic Q3 2026 example returns **1.89%** from the certified turnover definition, which excludes internal transfers. It displays the definition, version, owner, SQL, and binds. | Select AI could generate a query from this prompt and schema metadata. Its result would depend on the generated SQL and available context; **we did not run Select AI for this question**. |
| “Turnover in Florida Q3 2026 including transfers” | The qualifier conflicts with the approved turnover definition. The app refuses to certify an answer. The demo's separate, fixed comparison counts transfers and shows **2.33%**, labeled an illustrative ad hoc interpretation. | A generated query could explore this alternate interpretation, subject to review. **2.33% is not a measured Select AI output.** |
| “What's our burnout rate?” | No burnout metric is certified. The app returns no number and offers a certification request. | Select AI could help explore available data, but a generated result would not by itself create this project's approved definition, owner, or version. |

The 1.89% and 2.33% figures come from the repository's deterministic **synthetic** Oracle data. The only difference in the comparison is whether four internal transfers are counted alongside 17 approved exits; both rates use the same average monthly headcount. See [`sql/03_verify.sql`](../sql/03_verify.sql) for the read-only check.

This repository's runtime does **not** invoke Select AI or an LLM. The FreeSQL demo did not configure an AI provider profile. Oracle's [usage guide](https://docs.oracle.com/en/database/oracle/oracle-database/26/selai/use-ai-keyword-enter-prompts.html) explains that Select AI uses an active profile and notes that generated SQL may fail or produce an incorrect result. Here, the fixed comparison is an explanatory calculation, and certified answers always follow approved metric rules.
