# Three-minute demo

This demonstration uses **synthetic employee data**. The displayed Florida comparison is fixed to Q3 2026, so it remains reproducible even when the app is run later.

## Before presenting

1. Load the `WH_` seed and metric catalog using the SQL scripts described in the README. Set `DEMO_AS_OF_DATE=2026-10-28` in the local environment so “last quarter” maps to Q3 2026.
2. Start the FastAPI app and open its home page. Confirm the header says **Database connected**.
3. Keep the browser at desktop width so the definition and owner appear beside the number.

## Script

**0:00 — A number with a definition.** Ask: “What was our turnover in Florida last quarter?” On October 28, 2026, “last quarter” is Q3 2026. The certified card displays the rate, the approved definition, metric version, owner, data freshness, and result-row count. Point out that **internal transfers are excluded**. Open **Show SQL** to show the approved query and bind values.

**1:00 — Why a plausible answer can differ.** Click **See the difference**. The second calculation includes internal transfers in the numerator and is therefore higher: **1.89% certified versus 2.33% illustrative ad hoc** in the seeded Q3 2026 Florida data. The script seeds 17 approved exits and 4 additional internal transfers. Both use the same synthetic data and denominator. The second value is an **illustrative ad hoc interpretation**, not a Select AI output. Say: “Two right-looking answers. Only one uses the approved definition.”

**1:45 — An honest refusal.** Ask: “What's our burnout rate?” The app has no certified burnout metric, so it refuses to invent one, lists nearby certified metrics, and offers **Request certification**. Click it and show the request ID.

**2:30 — Repeatability.** Run `python3 tests/run_golden.py`. The seeded checks print intent accuracy **15/15**, refusal correctness **8/8**, and paraphrase SQL plus bind consistency **3/3**. These are deterministic parser/compiler checks on synthetic metadata, not live-database accuracy. Return to the card and close with: “Every number comes with its definition, version, and owner. When there's no approved definition, the app says so.”

## Presenter notes

- The UI never labels the ad hoc comparison as a live model or Select AI response.
- The certification button logs a request; it does not approve or create a metric automatically.
- If the live database is unavailable, demonstrate the interface and query contract, then clearly say that no live answer was computed.
