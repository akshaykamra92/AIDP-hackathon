"""Print a compact, reproducible golden-set score for the hackathon demo."""

import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.intent import parse_question
from app.metrics import MetricDefinition, compile_certified


TODAY = date(2026, 10, 28)
LOCATIONS = [
    {"location_id": 1, "name": "Orlando Rehab Center", "city": "Orlando", "state": "FL"},
    {"location_id": 2, "name": "Tampa Rehab Center", "city": "Tampa", "state": "FL"},
    {"location_id": 3, "name": "Jacksonville Rehab Center", "city": "Jacksonville", "state": "FL"},
    {"location_id": 4, "name": "Birmingham Rehab Center", "city": "Birmingham", "state": "AL"},
    {"location_id": 5, "name": "Dallas Rehab Center", "city": "Dallas", "state": "TX"},
    {"location_id": 6, "name": "Atlanta Rehab Center", "city": "Atlanta", "state": "GA"},
]
JOBS = ["NURSING", "THERAPY", "CLINICAL_SUPPORT", "OPERATIONS"]
PARAPHRASES = [
    ("What was our turnover in Florida last quarter?", "Give me attrition rate for FL Q3 2026"),
    ("Headcount in Florida last month", "What was our employee count in FL September 2026?"),
    ("Median time to fill in Q3 2026", "Days to fill last quarter"),
]


def parse(question):
    return parse_question(question, locations=LOCATIONS, job_families=JOBS, today=TODAY)


def metric_for(metric_id):
    return MetricDefinition.from_row({
        "metric_id": metric_id,
        "name": metric_id,
        "version": 1,
        "owner": "Demo",
        "status": "CERTIFIED",
        "definition_text": "Approved demo definition",
        "sql_template": metric_id,
        "allowed_dims": '["state","location","job_family","month","quarter"]',
        "allowed_filters": '["state","location","job_family","month","quarter","period"]',
        "grain": "PERIOD",
    })


def score():
    cases = json.loads((Path(__file__).parent / "golden.json").read_text())
    intent_total = intent_correct = refusal_total = refusal_correct = 0
    failures = []
    for case in cases:
        result = parse(case["question"])
        if "refusal" in case:
            refusal_total += 1
            correct = result.intent is None and result.reason_code == case["refusal"]
            refusal_correct += int(correct)
        else:
            intent_total += 1
            item = result.intent
            correct = bool(
                item
                and item.metric_id == case["metric_id"]
                and item.filters == case["filters"]
                and list(item.dimensions) == case["dimensions"]
                and item.period.start.isoformat() == case["start"]
                and item.period.end.isoformat() == case["end"]
            )
            intent_correct += int(correct)
        if not correct:
            failures.append(case["question"])

    pair_correct = 0
    for left, right in PARAPHRASES:
        left_intent, right_intent = parse(left).intent, parse(right).intent
        if left_intent is None or right_intent is None:
            continue
        left_query = compile_certified(left_intent, metric_for(left_intent.metric_id))
        right_query = compile_certified(right_intent, metric_for(right_intent.metric_id))
        pair_correct += int(left_query.sql == right_query.sql and left_query.binds == right_query.binds)

    print("Golden-set score (deterministic parser; synthetic metadata)")
    print("Intent accuracy: %d/%d" % (intent_correct, intent_total))
    print("Refusal correctness: %d/%d" % (refusal_correct, refusal_total))
    print("Paraphrase SQL + binds consistency: %d/%d" % (pair_correct, len(PARAPHRASES)))
    if failures:
        print("Failures:")
        for question in failures:
            print("- " + question)
    return 0 if not failures and pair_correct == len(PARAPHRASES) else 1


if __name__ == "__main__":
    raise SystemExit(score())
