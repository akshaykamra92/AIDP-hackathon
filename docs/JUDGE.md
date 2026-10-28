# AI Judge submission draft and evaluation record

The four paragraphs below are prepared submission copy for this **synthetic-data hackathon prototype**. The [public repository](https://github.com/akshaykamra92/AIDP-hackathon) contains the live app's source, while the [GitHub Pages demo](https://akshaykamra92.github.io/AIDP-hackathon/) is a durable **static replay** with no database calls or saved requests. The live FreeSQL-backed app passed 3/3 browser workflows through a temporary public tunnel: certified turnover, definition comparison, and unsupported metric with a saved certification request. Keep the changing tunnel URL out of this file.

As of 2026-10-28, the AI Judge event is closed and rejected the attempted submission of this version. It has **no new score**; the displayed **91.7** belongs to a prior version.

## What did you build?

We built **Ask the Warehouse**, a prototype that answers natural-language business questions only when they map to a certified metric. Each answer shows the number, approved definition, owner, version, reporting period, data coverage, SQL, and bind values. Unsupported questions receive an explicit refusal and a one-click certification request.

## Who has this problem?

Data governance and analytics teams in multi-site organizations own official metrics, while leaders and operational analysts need quick answers. The same word can conceal different definitions: turnover may or may not count internal transfers, producing credible but conflicting numbers. The initial user is a leader asking a metric question; the likely buyer or internal sponsor is the team accountable for trusted reporting. The pattern transfers to other industries by replacing the metric catalog, source mapping, and question vocabulary. We have demonstrated this need with synthetic data, not customer interviews or measured demand.

## How does your solution solve it?

The app parses a question into a constrained metric, filters, dimensions, and reporting period. It checks these against a certified Oracle catalog, then runs reviewed SQL with bind variables and returns the result with its definition and provenance. It refuses unsupported qualifiers instead of silently dropping them. In the **synthetic** Florida Q3 2026 example, 21 employment events include 4 internal transfers. The approved turnover definition counts 17 exits and returns **1.89%**; a separately labeled, fixed ad hoc calculation that counts transfers returns **2.33%**. The certification request records a gap for review; it does not approve a new metric automatically. This is a narrow, repeatable prototype rather than a production governance workflow.

## How did you build it?

We used Oracle AI Database 26ai on FreeSQL for `WH_` synthetic workforce tables, schema comments and annotations, and a five-metric catalog. A Python FastAPI service uses `python-oracledb`, a conservative intent parser, allowlisted SQL templates, bind variables, read-only result queries, and an answer log. A one-page browser UI shows certified answers, the illustrative definition comparison, and refusals. Codex helped build the app, SQL scripts, documentation, and automated checks. The local suite has 23 unit tests; its golden set checks 15 supported intents, 8 refusals, and 3 paraphrase pairs. These tests validate parser and compiler behavior, not live customer accuracy. The FreeSQL-backed app was checked through a public tunnel. The runtime does not call Select AI because an AI provider profile was not configured for the demo.

## Rubric self-check

| Criterion | Evidence to show | Gap to address or state plainly |
| --- | --- | --- |
| Demand Reality (25%) | Concrete definition ambiguity and the synthetic 1.89% versus 2.33% example. | No customer interviews, live usage, or measured reporting losses yet. |
| Customer / Market (20%) | Named users (leaders and analysts), sponsor (data governance / analytics), and portability to other metric domains. | No market size, purchasing validation, or distribution evidence. |
| Solution Fit (20%) | Five certified metrics, visible definition and provenance, unsupported-qualifier refusal, and certification request. | Fixed vocabulary and no approval workflow, sign-in, or row-level permissions. |
| Technical / Demo (20%) | Live FreeSQL app passed 3/3 browser workflows; Oracle 26ai schema and annotations, reviewed SQL with binds, FastAPI UI, synthetic seed, 23 unit tests, and a durable recorded GitHub Pages demo. | The static replay makes no database calls; the live tunnel is temporary. Durable live hosting remains future work. |
| Differentiation (15%) | Certified answers use fixed approved SQL; similar-looking definitions are compared side by side; unsupported questions do not get an invented number. | The comparison is fixed and illustrative, not a live Select AI answer. AI-assisted development does not imply an AI model is called at runtime. |

## Feedback loop for a future judging window

1. Submit the checked public repository and durable recorded demo with the four paragraphs above. Add a freshly verified live link if one is available.
2. Save the judge's scores and exact feedback for each criterion. Treat them as evaluation input, not evidence of market demand or live technical performance.
3. Address the lowest-scoring issue with a concrete change or a clearer demonstration, run the local tests and live walkthrough again, then ask the judge to reassess. Keep every claim tied to a visible artifact or test result.
