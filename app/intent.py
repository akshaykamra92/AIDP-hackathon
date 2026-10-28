"""Conservative, deterministic question-to-intent parsing.

The parser deliberately refuses language it cannot account for. A guessed metric or a
silently dropped qualifier would make an answer look certified when it is not.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class Period:
    start: date
    end: date  # exclusive
    label: str


@dataclass(frozen=True)
class Intent:
    metric_id: str
    dimensions: Tuple[str, ...]
    filters: Dict[str, Any]
    period: Period

    def as_dict(self) -> Dict[str, Any]:
        return {
            "metric_id": self.metric_id,
            "dimensions": list(self.dimensions),
            "filters": dict(self.filters),
            "period": {
                "start": self.period.start.isoformat(),
                "end": self.period.end.isoformat(),
                "label": self.period.label,
            },
        }


@dataclass(frozen=True)
class ParseResult:
    intent: Optional[Intent] = None
    reason_code: Optional[str] = None
    message: Optional[str] = None
    unmapped_terms: Tuple[str, ...] = field(default_factory=tuple)


METRIC_PATTERNS: Sequence[Tuple[str, str]] = (
    (
        "VOLUNTARY_TURNOVER_RATE",
        r"\b(?:voluntary\s+(?:employee\s+)?(?:turnover|attrition))(?:\s+rate)?\b",
    ),
    (
        "INTERNAL_FILL_RATE",
        r"\b(?:internal\s+(?:fill|hire)(?:\s+rate)?|"
        r"(?:percentage|percent|share)\s+of\s+(?:requisitions|positions|roles)\s+(?:were\s+)?filled\s+internally)\b",
    ),
    (
        "TIME_TO_FILL_DAYS",
        r"\b(?:median\s+)?(?:time|days)\s+to\s+fill(?:\s+(?:requisitions|positions|roles))?\b",
    ),
    ("TURNOVER_RATE", r"\b(?:(?:employee|staff)\s+)?(?:turnover|attrition)(?:\s+rate)?\b"),
    (
        "HEADCOUNT",
        r"\b(?:head\s*count|employee\s+count|staff\s+count|"
        r"number\s+of\s+(?:employees|staff)|how\s+many\s+(?:employees|staff))\b",
    ),
)


DIMENSION_PATTERNS: Sequence[Tuple[str, str]] = (
    ("state", r"\b(?:by|per|across|and)\s+states?\b"),
    ("location", r"\b(?:by|per|across|and)\s+(?:locations?|facilit(?:y|ies)|sites?)\b"),
    ("job_family", r"\b(?:by|per|across|and)\s+(?:job\s+famil(?:y|ies)|roles?|disciplines?)\b"),
    ("month", r"\b(?:(?:by|per|and)\s+months?|monthly)\b"),
    ("quarter", r"\b(?:(?:by|per|and)\s+quarters?|quarterly)\b"),
)


STATE_NAMES = {
    "florida": "FL",
    "alabama": "AL",
    "texas": "TX",
    "georgia": "GA",
}

MONTH_NAMES = {
    name.lower(): number
    for number in range(1, 13)
    for name in (calendar.month_name[number], calendar.month_abbr[number])
}

FILLER_WORDS = {
    "a", "an", "and", "are", "at", "by", "can", "could", "did", "do", "during",
    "for", "from", "give", "had", "has", "have", "how", "i", "in", "is", "me",
    "my", "of", "on", "our", "please", "s", "show", "tell", "the", "to", "us",
    "was", "we", "were", "what", "whats", "would", "you",
}


def _add_months(value: date, months: int) -> date:
    index = value.year * 12 + (value.month - 1) + months
    return date(index // 12, index % 12 + 1, 1)


def _quarter_start(value: date) -> date:
    return date(value.year, ((value.month - 1) // 3) * 3 + 1, 1)


def _remove_span(text: str, start: int, end: int) -> str:
    return text[:start] + " " * (end - start) + text[end:]


def _period_candidates(question: str, today: date) -> List[Tuple[int, int, Period]]:
    candidates: List[Tuple[int, int, Period]] = []

    for match in re.finditer(r"\b(?:q([1-4])\s*(20\d{2})|(20\d{2})\s*q([1-4]))\b", question):
        quarter = int(match.group(1) or match.group(4))
        year = int(match.group(2) or match.group(3))
        start = date(year, 3 * (quarter - 1) + 1, 1)
        candidates.append((match.start(), match.end(), Period(start, _add_months(start, 3), "Q%d %d" % (quarter, year))))

    for match in re.finditer(r"\b(last|this)\s+(quarter|month|year)\b", question):
        which, unit = match.group(1), match.group(2)
        if unit == "quarter":
            current = _quarter_start(today)
            start = _add_months(current, -3) if which == "last" else current
            end = _add_months(start, 3)
        elif unit == "month":
            current = date(today.year, today.month, 1)
            start = _add_months(current, -1) if which == "last" else current
            end = _add_months(start, 1)
        else:
            start = date(today.year - (1 if which == "last" else 0), 1, 1)
            end = date(start.year + 1, 1, 1)
        candidates.append((match.start(), match.end(), Period(start, end, "%s %s" % (which, unit))))

    names = "|".join(sorted(MONTH_NAMES, key=len, reverse=True))
    for match in re.finditer(r"\b(" + names + r")\s+(20\d{2})\b", question):
        start = date(int(match.group(2)), MONTH_NAMES[match.group(1)], 1)
        candidates.append((match.start(), match.end(), Period(start, _add_months(start, 1), match.group(0).title())))

    for match in re.finditer(r"\b20\d{2}\b", question):
        if any(start <= match.start() and match.end() <= end for start, end, _ in candidates):
            continue
        year = int(match.group(0))
        candidates.append((match.start(), match.end(), Period(date(year, 1, 1), date(year + 1, 1, 1), str(year))))

    return sorted(candidates, key=lambda item: item[0])


def _normalize_residual(text: str) -> str:
    tokens = re.findall(r"[a-z0-9_]+", text.lower())
    return " ".join(token for token in tokens if token not in FILLER_WORDS)


def _location_candidates(locations: Iterable[Dict[str, Any]]) -> List[Tuple[str, int]]:
    rows = list(locations)
    candidates: List[Tuple[str, int]] = []
    for row in rows:
        location_id = int(row["location_id"])
        name = str(row["name"]).lower()
        candidates.append((name, location_id))
        city = str(row.get("city") or name.split()[0]).lower()
        if sum(1 for other in rows if str(other.get("city") or str(other["name"]).split()[0]).lower() == city) == 1:
            candidates.append((city, location_id))
    return sorted(set(candidates), key=lambda item: len(item[0]), reverse=True)


def parse_question(
    question: str,
    *,
    locations: Iterable[Dict[str, Any]] = (),
    job_families: Iterable[str] = (),
    today: Optional[date] = None,
) -> ParseResult:
    """Parse only explicitly supported metric language; reject all leftover content."""
    today = today or date.today()
    text = question.strip().lower().replace("’", "'")
    if not text:
        return ParseResult(reason_code="EMPTY_QUESTION", message="Enter a question.")
    if len(text) > 1000:
        return ParseResult(reason_code="QUESTION_TOO_LONG", message="Keep the question under 1,000 characters.")

    metric_id = None
    for candidate_id, pattern in METRIC_PATTERNS:
        match = re.search(pattern, text)
        if match:
            metric_id = candidate_id
            text = _remove_span(text, match.start(), match.end())
            break
    if metric_id is None:
        return ParseResult(
            reason_code="UNSUPPORTED_METRIC",
            message="No certified metric matched this question.",
            unmapped_terms=(question.strip(),),
        )
    if any(re.search(pattern, text) for _, pattern in METRIC_PATTERNS):
        return ParseResult(reason_code="MULTIPLE_METRICS", message="Ask about one metric at a time.")

    candidates = _period_candidates(text, today)
    if len(candidates) > 1:
        return ParseResult(reason_code="AMBIGUOUS_PERIOD", message="Specify one reporting period.")
    if not candidates:
        if metric_id == "HEADCOUNT":
            period_end = date(today.year, today.month, 1)
            period_start = _add_months(period_end, -1)
            period = Period(period_start, period_end, "latest completed month (default)")
        else:
            period_end = _quarter_start(today)
            period_start = _add_months(period_end, -3)
            period = Period(period_start, period_end, "latest completed quarter (default)")
    else:
        start, end, period = candidates[0]
        text = _remove_span(text, start, end)

    dimensions: List[str] = []
    for name, pattern in DIMENSION_PATTERNS:
        matches = list(re.finditer(pattern, text))
        if matches:
            dimensions.append(name)
            for match in reversed(matches):
                text = _remove_span(text, match.start(), match.end())
    if "month" in dimensions and "quarter" in dimensions:
        return ParseResult(reason_code="AMBIGUOUS_GRAIN", message="Choose monthly or quarterly grouping.")

    if metric_id == "TURNOVER_RATE":
        text = re.sub(r"\bexclud(?:e|es|ing)\s+internal\s+transfers?\b", " ", text)

    filters: Dict[str, Any] = {}
    state_hits: List[str] = []
    for state_name, code in STATE_NAMES.items():
        pattern = r"\b(?:" + re.escape(state_name) + r"|" + code.lower() + r")\b"
        if re.search(pattern, text):
            state_hits.append(code)
            text = re.sub(pattern, " ", text)
    if len(set(state_hits)) > 1:
        return ParseResult(reason_code="UNSUPPORTED_FILTER", message="Filter one state at a time.")
    if state_hits:
        filters["state"] = state_hits[0]

    location_hits: List[int] = []
    for candidate_name, location_id in _location_candidates(locations):
        pattern = r"\b" + re.escape(candidate_name) + r"\b"
        if re.search(pattern, text):
            location_hits.append(location_id)
            text = re.sub(pattern, " ", text)
    if len(set(location_hits)) > 1:
        return ParseResult(reason_code="UNSUPPORTED_FILTER", message="Filter one location at a time.")
    if location_hits:
        filters["location_id"] = location_hits[0]

    job_hits: List[str] = []
    for job in job_families:
        canonical = str(job).upper()
        phrase = canonical.lower().replace("_", " ")
        pattern = r"\b" + re.escape(phrase) + r"\b"
        if re.search(pattern, text):
            job_hits.append(canonical)
            text = re.sub(pattern, " ", text)
    if len(set(job_hits)) > 1:
        return ParseResult(reason_code="UNSUPPORTED_FILTER", message="Filter one job family at a time.")
    if job_hits:
        filters["job_family"] = job_hits[0]

    residual = _normalize_residual(text)
    if residual:
        return ParseResult(
            reason_code="UNMAPPED_QUALIFIER",
            message="Part of the question does not map to an approved metric, dimension, or filter.",
            unmapped_terms=(residual,),
        )

    return ParseResult(intent=Intent(metric_id, tuple(dimensions), filters, period))
