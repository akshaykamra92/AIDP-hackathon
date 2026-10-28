"""Compile governed metrics over registered Oracle views.

The catalog contains identifiers and a small aggregation enum, never SQL text.
Only an owned WH_ view and simple, unquoted uppercase column names may enter
the generated statement. Reporting dates and every filter value remain binds.
The repository must separately verify that the view and columns exist and that
their Oracle data types match the metric before certification.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Mapping, Sequence

from app.metrics import CompileError, CompiledQuery


_IDENTIFIER = re.compile(r"[A-Z][A-Z0-9_$#]*\Z", re.ASCII)
_STATUSES = frozenset({"DRAFT", "REVIEWED", "CERTIFIED", "RETIRED"})
_AGGREGATIONS = frozenset({"COUNT", "SUM", "AVG"})
_MANIFEST_KEYS = frozenset(
    {
        "metric_id",
        "name",
        "owner",
        "definition_text",
        "unit",
        "source_view",
        "date_column",
        "value_column",
        "aggregation",
        "allowed_dims",
        "allowed_filters",
    }
)


def _text(value: Any) -> str:
    if hasattr(value, "read"):
        value = value.read()
    return "" if value is None else str(value)


def _field(value: Any, name: str, max_length: int) -> str:
    text = _text(value).strip()
    if not text or len(text) > max_length or any(ord(char) < 32 for char in text):
        raise CompileError("INVALID_METRIC", f"{name} must be printable and 1–{max_length} characters long.")
    return text


def _identifier(value: Any, name: str, max_length: int = 128) -> str:
    text = _text(value)
    if len(text) > max_length or not _IDENTIFIER.fullmatch(text):
        raise CompileError("INVALID_IDENTIFIER", f"{name} must be a simple, unquoted uppercase Oracle identifier.")
    return text


def _names(value: Any, name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if hasattr(value, "read"):
        value = value.read()
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise CompileError("INVALID_CATALOG", f"{name} must be a JSON array of column names.") from exc
    if not isinstance(value, (list, tuple)):
        raise CompileError("INVALID_CATALOG", f"{name} must be an array of column names.")
    if len(value) > 20:
        raise CompileError("INVALID_CATALOG", f"{name} may contain at most 20 columns.")
    names = tuple(_identifier(item, name) for item in value)
    if len(names) != len(set(names)):
        raise CompileError("INVALID_CATALOG", f"{name} contains a duplicate column.")
    return names


def _lower_keys(row: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(row, Mapping):
        raise CompileError("INVALID_CATALOG", "Metric metadata must be an object.")
    return {str(key).lower(): value for key, value in row.items()}


def _version(value: Any) -> int:
    if isinstance(value, bool):
        raise CompileError("INVALID_METRIC_VERSION", "Metric version must be a positive integer.")
    if isinstance(value, int):
        version = value
    elif isinstance(value, Decimal) and value.is_finite() and value == value.to_integral_value():
        version = int(value)
    elif isinstance(value, str) and re.fullmatch(r"[1-9][0-9]*", value):
        version = int(value)
    else:
        raise CompileError("INVALID_METRIC_VERSION", "Metric version must be a positive integer.")
    if version < 1:
        raise CompileError("INVALID_METRIC_VERSION", "Metric version must be a positive integer.")
    return version


@dataclass(frozen=True)
class RegisteredMetric:
    metric_id: str
    version: int
    name: str
    owner: str
    definition_text: str
    unit: str
    source_view: str
    date_column: str
    value_column: str | None
    aggregation: str
    allowed_dims: tuple[str, ...]
    allowed_filters: tuple[str, ...]
    status: str
    created_at: date | datetime | None = None
    reviewed_at: date | datetime | None = None
    reviewed_by: str | None = None
    review_note: str | None = None
    certified_at: date | datetime | None = None
    certified_by: str | None = None
    source_ddl_at: date | datetime | None = None

    def __post_init__(self) -> None:
        _identifier(self.metric_id, "Metric ID", 64)
        object.__setattr__(self, "version", _version(self.version))
        _field(self.name, "Name", 200)
        _field(self.owner, "Owner", 200)
        _field(self.definition_text, "Definition", 16000)
        _field(self.unit, "Unit", 40)
        _identifier(self.source_view, "Source view")
        if not self.source_view.startswith("WH_"):
            raise CompileError("INVALID_SOURCE_VIEW", "The source must be an owned WH_ view.")
        _identifier(self.date_column, "Date column")
        if self.aggregation not in _AGGREGATIONS:
            raise CompileError("INVALID_AGGREGATION", "Use COUNT, SUM, or AVG.")
        if self.status not in _STATUSES:
            raise CompileError("INVALID_METRIC_STATUS", "Use DRAFT, REVIEWED, CERTIFIED, or RETIRED.")
        if self.aggregation == "COUNT":
            if self.value_column is not None:
                raise CompileError("INVALID_VALUE_COLUMN", "COUNT does not use a value column.")
        elif self.value_column is None:
            raise CompileError("INVALID_VALUE_COLUMN", "SUM and AVG require a numeric value column.")
        else:
            _identifier(self.value_column, "Value column")
        object.__setattr__(self, "allowed_dims", _names(self.allowed_dims, "Allowed dimensions"))
        object.__setattr__(self, "allowed_filters", _names(self.allowed_filters, "Allowed filters"))
        if "VALUE" in self.allowed_dims:
            raise CompileError("INVALID_CATALOG", "VALUE is reserved for the aggregate result column.")
        if self.reviewed_by is not None:
            _field(self.reviewed_by, "Reviewer", 120)
        if self.review_note is not None:
            _field(self.review_note, "Review note", 16000)
        if self.certified_by is not None:
            _field(self.certified_by, "Certifier", 120)

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "RegisteredMetric":
        """Materialize a WH_REGISTERED_METRIC row returned by Oracle."""
        data = _lower_keys(row)
        return cls(
            metric_id=_text(data.get("metric_id")),
            version=_version(data.get("version")),
            name=_text(data.get("name")),
            owner=_text(data.get("owner")),
            definition_text=_text(data.get("definition_text")),
            unit=_text(data.get("unit")),
            source_view=_text(data.get("source_view")),
            date_column=_text(data.get("date_column")),
            value_column=_text(data["value_column"]) if data.get("value_column") is not None else None,
            aggregation=_text(data.get("aggregation")),
            allowed_dims=_names(data.get("allowed_dims"), "Allowed dimensions"),
            allowed_filters=_names(data.get("allowed_filters"), "Allowed filters"),
            status=_text(data.get("status")),
            created_at=data.get("created_at"),
            reviewed_at=data.get("reviewed_at"),
            reviewed_by=_text(data["reviewed_by"]) if data.get("reviewed_by") is not None else None,
            review_note=_text(data["review_note"]) if data.get("review_note") is not None else None,
            certified_at=data.get("certified_at"),
            certified_by=_text(data["certified_by"]) if data.get("certified_by") is not None else None,
            source_ddl_at=data.get("source_ddl_at"),
        )

    @classmethod
    def from_manifest(
        cls, manifest: Mapping[str, Any], version: int, status: str = "DRAFT"
    ) -> "RegisteredMetric":
        """Create metadata from a typed manifest; SQL fragments are rejected."""
        data = _lower_keys(manifest)
        unexpected = set(data) - _MANIFEST_KEYS
        if unexpected:
            raise CompileError("INVALID_MANIFEST", "The metric manifest contains unsupported fields.")
        return cls(
            metric_id=_text(data.get("metric_id")),
            version=version,
            name=_text(data.get("name")),
            owner=_text(data.get("owner")),
            definition_text=_text(data.get("definition_text")),
            unit=_text(data.get("unit")),
            source_view=_text(data.get("source_view")),
            date_column=_text(data.get("date_column")),
            value_column=_text(data["value_column"]) if data.get("value_column") is not None else None,
            aggregation=_text(data.get("aggregation")),
            allowed_dims=_names(data.get("allowed_dims"), "Allowed dimensions"),
            allowed_filters=_names(data.get("allowed_filters"), "Allowed filters"),
            status=status,
        )

    def public_dict(self) -> dict[str, Any]:
        """Return the definition and query contract without an SQL fragment."""
        return {
            "id": self.metric_id,
            "version": self.version,
            "name": self.name,
            "owner": self.owner,
            "definition": self.definition_text,
            "unit": self.unit,
            "source_view": self.source_view,
            "date_column": self.date_column,
            "aggregation": self.aggregation,
            "allowed_dims": list(self.allowed_dims),
            "allowed_filters": list(self.allowed_filters),
            "status": self.status,
            "reviewed_at": self.reviewed_at.isoformat() if self.reviewed_at is not None else None,
            "reviewed_by": self.reviewed_by,
            "review_note": self.review_note,
            "certified_at": self.certified_at.isoformat() if self.certified_at is not None else None,
            "certified_by": self.certified_by,
            "source_ddl_at": self.source_ddl_at.isoformat() if self.source_ddl_at is not None else None,
        }


def _bind_scalar(value: Any) -> bool:
    if isinstance(value, bool) or value is None:
        return False
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, Decimal):
        return value.is_finite()
    return isinstance(value, (str, int, date, datetime))


def compile_registered(
    metric: RegisteredMetric,
    start_date: date,
    end_date: date,
    dimensions: Sequence[str],
    filters: Mapping[str, Any],
    require_certified: bool = True,
) -> CompiledQuery:
    """Generate one fixed, read-only aggregate SELECT from validated metadata."""
    if not isinstance(metric, RegisteredMetric):
        raise CompileError("INVALID_METRIC", "A registered metric is required.")
    if metric.status == "RETIRED" or (require_certified and metric.status != "CERTIFIED"):
        raise CompileError("METRIC_NOT_CERTIFIED", "The metric is not certified for answers.")
    if type(start_date) is not date or type(end_date) is not date:
        raise CompileError("INVALID_PERIOD", "Reporting dates must be dates.")
    if start_date.day != 1 or end_date.day != 1 or start_date >= end_date:
        raise CompileError("INVALID_PERIOD", "Reporting periods must be full, half-open months.")
    if isinstance(dimensions, (str, bytes)) or not isinstance(dimensions, Sequence):
        raise CompileError("UNSUPPORTED_DIMENSION", "Dimensions must be a list of approved columns.")
    selected_dims = tuple(_identifier(name, "Dimension") for name in dimensions)
    if len(selected_dims) != len(set(selected_dims)):
        raise CompileError("DUPLICATE_DIMENSION", "A grouping dimension was repeated.")
    if any(name not in metric.allowed_dims for name in selected_dims):
        raise CompileError("UNSUPPORTED_DIMENSION", "A grouping dimension is not approved for this metric.")
    if not isinstance(filters, Mapping):
        raise CompileError("UNSUPPORTED_FILTER", "Filters must be an object of approved columns and scalar values.")

    binds: dict[str, Any] = {"start_date": start_date, "end_date": end_date}
    conditions = [f"{metric.date_column} >= :start_date", f"{metric.date_column} < :end_date"]
    validated_filters = [(_identifier(name, "Filter"), value) for name, value in filters.items()]
    for index, (column, value) in enumerate(sorted(validated_filters)):
        if column not in metric.allowed_filters:
            raise CompileError("UNSUPPORTED_FILTER", "A filter is not approved for this metric.")
        if not _bind_scalar(value):
            raise CompileError("INVALID_FILTER_VALUE", "Filter values must be finite scalars.")
        bind_name = f"f{index}"
        binds[bind_name] = value
        conditions.append(f"{column} = :{bind_name}")

    target = "*" if metric.aggregation == "COUNT" else metric.value_column
    aggregate = f"{metric.aggregation}({target})"
    count_target = "*" if metric.aggregation == "COUNT" else metric.value_column
    projection = ", ".join(selected_dims)
    sql = "SELECT " + (projection + ", " if projection else "") + aggregate + " AS value"
    sql += f" FROM {metric.source_view} WHERE " + " AND ".join(conditions)
    if selected_dims:
        sql += " GROUP BY " + projection
    sql += f" HAVING COUNT({count_target}) > 0"
    if selected_dims:
        sql += " ORDER BY " + projection
    return CompiledQuery(sql=sql, binds=binds, columns=tuple(name.lower() for name in selected_dims) + ("value",))
