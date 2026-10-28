"""Approved SQL compiler for certified warehouse metrics.

Only identifiers from this module enter SQL text. Question text and filter values are
never interpolated into a statement; they are passed as Oracle bind values.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

from app.intent import Intent


class CompileError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


METRIC_UNITS = {
    "HEADCOUNT": ("people", "count"),
    "TURNOVER_RATE": ("percent", "percentage_points"),
    "VOLUNTARY_TURNOVER_RATE": ("percent", "percentage_points"),
    "TIME_TO_FILL_DAYS": ("days", "calendar_days"),
    "INTERNAL_FILL_RATE": ("percent", "percentage_points"),
}

DIMENSION_ORDER = ("state", "location", "job_family", "month", "quarter")
FILTER_ORDER = ("state", "location_id", "job_family")


def _read_text(value: Any) -> str:
    if hasattr(value, "read"):
        return str(value.read())
    return "" if value is None else str(value)


def _parse_names(value: Any) -> Tuple[str, ...]:
    raw = _read_text(value).strip()
    if not raw:
        return ()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = [part.strip() for part in raw.split(",")]
    if not isinstance(parsed, list):
        return ()
    return tuple(str(item).lower() for item in parsed)


@dataclass(frozen=True)
class MetricDefinition:
    metric_id: str
    name: str
    version: int
    owner: str
    status: str
    definition: str
    template_marker: str
    allowed_dims: Tuple[str, ...]
    allowed_filters: Tuple[str, ...]
    grain: str

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "MetricDefinition":
        lower = {str(key).lower(): value for key, value in row.items()}
        return cls(
            metric_id=_read_text(lower.get("metric_id")).strip().upper(),
            name=_read_text(lower.get("name")).strip(),
            version=int(lower.get("version") or 0),
            owner=_read_text(lower.get("owner")).strip(),
            status=_read_text(lower.get("status")).strip().upper(),
            definition=_read_text(lower.get("definition_text")).strip(),
            template_marker=_read_text(lower.get("sql_template")).strip().upper(),
            allowed_dims=_parse_names(lower.get("allowed_dims")),
            allowed_filters=_parse_names(lower.get("allowed_filters")),
            grain=_read_text(lower.get("grain")).strip(),
        )

    def public_dict(self) -> Dict[str, Any]:
        unit, value_scale = METRIC_UNITS[self.metric_id]
        return {
            "id": self.metric_id,
            "name": self.name,
            "version": self.version,
            "owner": self.owner,
            "definition": self.definition,
            "unit": unit,
            "value_scale": value_scale,
            "grain": self.grain,
        }


@dataclass(frozen=True)
class CompiledQuery:
    sql: str
    binds: Dict[str, Any]
    columns: Tuple[str, ...]


def _months_between(start: date, end: date) -> int:
    if start.day != 1 or end.day != 1 or start >= end:
        raise CompileError("INVALID_PERIOD", "Reporting periods must be full months.")
    return (end.year - start.year) * 12 + end.month - start.month


def _month_before(value: date) -> date:
    month = value.month - 1
    return date(value.year - 1, 12, 1) if month == 0 else date(value.year, month, 1)


def _dimension_expr(name: str, fact_alias: str, date_column: str) -> str:
    if name == "state":
        return "l.state"
    if name == "location":
        return "l.name"
    if name == "job_family":
        return fact_alias + ".job_family"
    if name == "month":
        return "TO_CHAR(" + date_column + ", 'YYYY-MM')"
    if name == "quarter":
        return "TO_CHAR(" + date_column + ", 'YYYY') || '-Q' || TO_CHAR(" + date_column + ", 'Q')"
    raise CompileError("UNSUPPORTED_DIMENSION", "Unsupported grouping dimension.")


def _select_dims(dimensions: Sequence[str], fact_alias: str, date_column: str) -> str:
    return ", ".join(_dimension_expr(name, fact_alias, date_column) + " AS " + name for name in dimensions)


def _group_dims(dimensions: Sequence[str], fact_alias: str, date_column: str) -> str:
    return ", ".join(_dimension_expr(name, fact_alias, date_column) for name in dimensions)


def _from_location(fact_table: str, alias: str) -> str:
    return "FROM " + fact_table + " " + alias + " JOIN WH_LOCATION l ON l.location_id = " + alias + ".location_id"


def _conditions(alias: str, date_column: str, filters: Mapping[str, Any]) -> List[str]:
    conditions = [date_column + " >= :start_date", date_column + " < :end_date"]
    if "state" in filters:
        conditions.append("l.state = :state")
    if "location_id" in filters:
        conditions.append(alias + ".location_id = :location_id")
    if "job_family" in filters:
        conditions.append(alias + ".job_family = :job_family")
    return conditions


def _comma(value: str) -> str:
    return value + ", " if value else ""


def _group_by(value: str, extra: str = "") -> str:
    parts = [part for part in (value, extra) if part]
    return " GROUP BY " + ", ".join(parts) if parts else ""


def _order_by(dimensions: Sequence[str]) -> str:
    return " ORDER BY " + ", ".join(dimensions) if dimensions else ""


def _headcount_sql(dimensions: Sequence[str], filters: Mapping[str, Any]) -> str:
    projection = _select_dims(dimensions, "h", "h.month_start")
    grouping = _group_dims(dimensions, "h", "h.month_start")
    where = " AND ".join(_conditions("h", "h.month_start", filters))
    dim_names = ", ".join(dimensions)
    partition = "PARTITION BY " + dim_names + " " if dimensions else ""
    time_dim = "month" if "month" in dimensions else ("quarter" if "quarter" in dimensions else None)
    if time_dim == "month":
        complete = ""
    elif time_dim == "quarter":
        complete = " AND month_start = ADD_MONTHS(TRUNC(month_start, 'Q'), 2)"
    else:
        complete = " AND month_start = :last_month_start"
    return (
        "WITH monthly AS ("
        " SELECT " + _comma(projection) + "h.month_start, SUM(h.headcount) AS monthly_count "
        + _from_location("WH_HEADCOUNT_MONTH", "h") + " WHERE " + where
        + _group_by(grouping, "h.month_start") + " ), "
        "ranked AS ( SELECT " + _comma(dim_names) + "month_start, monthly_count, "
        "ROW_NUMBER() OVER (" + partition + "ORDER BY month_start DESC) AS rn FROM monthly ) "
        "SELECT " + _comma(dim_names) + "monthly_count AS value FROM ranked "
        "WHERE rn = 1" + complete + _order_by(dimensions)
    )


def _turnover_sql(metric_id: str, dimensions: Sequence[str], filters: Mapping[str, Any]) -> str:
    h_projection = _select_dims(dimensions, "h", "h.month_start")
    h_grouping = _group_dims(dimensions, "h", "h.month_start")
    t_projection = _select_dims(dimensions, "t", "t.term_date")
    t_grouping = _group_dims(dimensions, "t", "t.term_date")
    h_where = " AND ".join(_conditions("h", "h.month_start", filters))
    t_conditions = _conditions("t", "t.term_date", filters)
    if metric_id == "TURNOVER_RATE":
        t_conditions.append("t.term_type <> 'INTERNAL_TRANSFER'")
    else:
        t_conditions.append("t.term_type = 'VOLUNTARY'")
    t_where = " AND ".join(t_conditions)
    dim_names = ", ".join(dimensions)
    d_projection = ", ".join("d." + name + " AS " + name for name in dimensions)
    join = " AND ".join("d." + name + " = n." + name for name in dimensions) if dimensions else "1 = 1"
    expected = 1 if "month" in dimensions else (3 if "quarter" in dimensions else None)
    if expected is None:
        expected_placeholder = ":expected_months"
    else:
        expected_placeholder = str(expected)
    return (
        "WITH monthly_hc AS ( SELECT " + _comma(h_projection)
        + "h.month_start, SUM(h.headcount) AS monthly_count "
        + _from_location("WH_HEADCOUNT_MONTH", "h") + " WHERE " + h_where
        + _group_by(h_grouping, "h.month_start") + " ), "
        "denom AS ( SELECT " + _comma(dim_names)
        + "AVG(monthly_count) AS avg_headcount FROM monthly_hc"
        + _group_by(dim_names) + " HAVING COUNT(*) = " + expected_placeholder + " ), "
        "numer AS ( SELECT " + _comma(t_projection) + "COUNT(*) AS terminations "
        + _from_location("WH_TERMINATION", "t") + " WHERE " + t_where
        + _group_by(t_grouping) + " ) "
        "SELECT " + _comma(d_projection)
        + "ROUND(100 * NVL(n.terminations, 0) / d.avg_headcount, 4) AS value "
        "FROM denom d LEFT JOIN numer n ON " + join
        + " WHERE d.avg_headcount > 0" + _order_by(dimensions)
    )


def _requisition_sql(metric_id: str, dimensions: Sequence[str], filters: Mapping[str, Any]) -> str:
    projection = _select_dims(dimensions, "r", "r.fill_date")
    grouping = _group_dims(dimensions, "r", "r.fill_date")
    where = " AND ".join(_conditions("r", "r.fill_date", filters))
    if metric_id == "TIME_TO_FILL_DAYS":
        expression = "MEDIAN(TRUNC(r.fill_date) - TRUNC(r.open_date))"
    else:
        expression = "ROUND(100 * SUM(CASE WHEN r.filled_internally = 'Y' THEN 1 ELSE 0 END) / COUNT(*), 4)"
    return (
        "SELECT " + _comma(projection) + expression + " AS value "
        + _from_location("WH_REQUISITION", "r") + " WHERE " + where
        + _group_by(grouping) + _order_by(dimensions)
    )


def compile_certified(intent: Intent, metric: MetricDefinition) -> CompiledQuery:
    """Compile a validated intent using one of five audited query families."""
    if metric.metric_id not in METRIC_UNITS or intent.metric_id != metric.metric_id:
        raise CompileError("UNSUPPORTED_METRIC", "No approved SQL compiler exists for this metric.")
    if metric.status != "CERTIFIED":
        raise CompileError("METRIC_NOT_CERTIFIED", "This metric is not certified.")
    if metric.template_marker != metric.metric_id:
        raise CompileError("CATALOG_TEMPLATE_MISMATCH", "The catalog template does not match the approved compiler.")
    if metric.version < 1:
        raise CompileError("INVALID_METRIC_VERSION", "The certified metric has no valid version.")
    if len(set(intent.dimensions)) != len(intent.dimensions):
        raise CompileError("DUPLICATE_DIMENSION", "A grouping dimension was repeated.")
    if any(name not in DIMENSION_ORDER or name not in metric.allowed_dims for name in intent.dimensions):
        raise CompileError("UNSUPPORTED_DIMENSION", "A requested grouping is not approved for this metric.")
    if "month" in intent.dimensions and "quarter" in intent.dimensions:
        raise CompileError("AMBIGUOUS_GRAIN", "Choose monthly or quarterly grouping.")
    if "quarter" in intent.dimensions and (intent.period.start.month not in (1, 4, 7, 10) or intent.period.end.month not in (1, 4, 7, 10)):
        raise CompileError("INVALID_PERIOD", "Quarterly grouping requires complete quarters.")
    for key in intent.filters:
        catalog_key = "location" if key == "location_id" else key
        if key not in FILTER_ORDER or catalog_key not in metric.allowed_filters:
            raise CompileError("UNSUPPORTED_FILTER", "A requested filter is not approved for this metric.")
    months = _months_between(intent.period.start, intent.period.end)
    binds: Dict[str, Any] = {"start_date": intent.period.start, "end_date": intent.period.end}
    for key in FILTER_ORDER:
        if key in intent.filters:
            binds[key] = intent.filters[key]
    if metric.metric_id == "HEADCOUNT":
        if "month" not in intent.dimensions and "quarter" not in intent.dimensions:
            binds["last_month_start"] = _month_before(intent.period.end)
        sql = _headcount_sql(intent.dimensions, intent.filters)
    elif metric.metric_id in ("TURNOVER_RATE", "VOLUNTARY_TURNOVER_RATE"):
        if "month" not in intent.dimensions and "quarter" not in intent.dimensions:
            binds["expected_months"] = months
        sql = _turnover_sql(metric.metric_id, intent.dimensions, intent.filters)
    else:
        sql = _requisition_sql(metric.metric_id, intent.dimensions, intent.filters)
    if not sql.lstrip().upper().startswith(("SELECT ", "WITH ")) or ";" in sql:
        raise CompileError("UNSAFE_SQL", "The approved compiler generated an invalid statement.")
    return CompiledQuery(sql=sql, binds=binds, columns=tuple(intent.dimensions) + ("value",))
