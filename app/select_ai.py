"""Opt-in, SQL-display-only Oracle Select AI exploration.

Generated SQL is untrusted text. This module never sends it back to Oracle for
execution and never presents it as a certified metric answer.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from app.db import DatabaseUnavailable


_IDENTIFIER = re.compile(r"^[A-Z][A-Z0-9_]*$")
_ACTION_PREFIX = re.compile(r"\bSELECT\s+AI\b", re.IGNORECASE)
_SELECT_START = re.compile(r"^\s*(?:SELECT|WITH)\b", re.IGNORECASE)
_SHOWSQL = (
    "SELECT DBMS_CLOUD_AI.GENERATE("
    "prompt => :prompt, profile_name => :profile_name, action => 'showsql'"
    ") AS generated_sql FROM dual"
)


class SelectAIError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _text(value: Any) -> str:
    if hasattr(value, "read"):
        value = value.read()
    return str(value) if value is not None else ""


def _true(value: Any) -> bool:
    return _text(value).strip().lower() == "true"


class SelectAIExplorer:
    """Generate exploratory SQL from one explicitly scoped, owned AI profile."""

    def __init__(
        self,
        database: Any,
        *,
        enabled: bool | None = None,
        profile_name: str | None = None,
    ):
        self.database = database
        self.enabled = (
            enabled if enabled is not None
            else os.getenv("SELECT_AI_ENABLED", "false").strip().lower() == "true"
        )
        self.profile_name = (
            profile_name if profile_name is not None
            else os.getenv("SELECT_AI_PROFILE", "")
        ).strip().upper()

    def _read(self, sql: str, binds: dict[str, Any]) -> list[dict[str, Any]]:
        try:
            return self.database.run_readonly(sql, binds)
        except DatabaseUnavailable as exc:
            raise SelectAIError(
                "SELECT_AI_UNAVAILABLE",
                "Select AI is unavailable for this Oracle connection. Check package privileges and profile setup.",
            ) from exc

    def check_profile(self) -> dict[str, Any]:
        """Verify the current profile before any provider call."""
        if not self.enabled:
            raise SelectAIError("SELECT_AI_DISABLED", "Select AI exploration is not enabled.")
        if not self.profile_name or not _IDENTIFIER.fullmatch(self.profile_name) or not self.profile_name.startswith("WH_"):
            raise SelectAIError("SELECT_AI_PROFILE_REQUIRED", "Configure an owned WH_ Select AI profile first.")

        rows = self._read(
            "SELECT status FROM USER_CLOUD_AI_PROFILES WHERE profile_name = :profile_name",
            {"profile_name": self.profile_name},
        )
        if len(rows) != 1 or _text(rows[0].get("status")).upper() != "ENABLED":
            raise SelectAIError("SELECT_AI_PROFILE_UNAVAILABLE", "The configured Select AI profile is absent or disabled.")

        rows = self._read(
            "SELECT attribute_name, attribute_value FROM USER_CLOUD_AI_PROFILE_ATTRIBUTES "
            "WHERE profile_name = :profile_name AND LOWER(attribute_name) IN "
            "('object_list', 'enforce_object_list', 'object_list_mode', 'conversation')",
            {"profile_name": self.profile_name},
        )
        attrs: dict[str, str] = {}
        for row in rows:
            key = str(row["attribute_name"]).lower()
            if key in attrs:
                raise SelectAIError("SELECT_AI_PROFILE_UNSAFE", "The Select AI profile has duplicate attributes.")
            attrs[key] = _text(row["attribute_value"])
        if not _true(attrs.get("enforce_object_list")):
            raise SelectAIError("SELECT_AI_PROFILE_UNSAFE", "The Select AI profile must enforce an explicit object list.")
        if _true(attrs.get("conversation")) or attrs.get("object_list_mode", "all").lower() != "all":
            raise SelectAIError("SELECT_AI_PROFILE_UNSAFE", "The Select AI profile has unsupported exploration settings.")

        try:
            objects = json.loads(attrs["object_list"])
        except (KeyError, TypeError, ValueError) as exc:
            raise SelectAIError("SELECT_AI_PROFILE_UNSAFE", "The Select AI profile has no valid object list.") from exc
        if not isinstance(objects, list) or not 1 <= len(objects) <= 64:
            raise SelectAIError("SELECT_AI_PROFILE_UNSAFE", "The Select AI profile needs 1 to 64 explicit views.")

        owner = _text(getattr(self.database, "user", "")).strip().upper()
        if not owner:
            raise SelectAIError("SELECT_AI_PROFILE_UNSAFE", "The Oracle schema owner is unavailable.")
        views: list[str] = []
        for item in objects:
            if not isinstance(item, dict) or set(item) != {"owner", "name"}:
                raise SelectAIError("SELECT_AI_PROFILE_UNSAFE", "The Select AI object list contains an invalid entry.")
            view = _text(item.get("name")).upper()
            if (
                _text(item.get("owner")).upper() != owner
                or not _IDENTIFIER.fullmatch(view)
                or not view.startswith("WH_")
            ):
                raise SelectAIError("SELECT_AI_PROFILE_UNSAFE", "The Select AI profile must name owned WH_ views only.")
            views.append(view)
        if len(set(views)) != len(views):
            raise SelectAIError("SELECT_AI_PROFILE_UNSAFE", "The Select AI profile repeats a view.")
        view_binds = {f"view_{index}": view for index, view in enumerate(views)}
        placeholders = ", ".join(f":view_{index}" for index in range(len(views)))
        view_rows = self._read(
            f"SELECT view_name FROM USER_VIEWS WHERE view_name IN ({placeholders})",
            view_binds,
        )
        if {str(row.get("view_name", "")).upper() for row in view_rows} != set(views):
            raise SelectAIError("SELECT_AI_PROFILE_UNSAFE", "A Select AI profile view is missing or is not owned.")
        return {"profile": self.profile_name, "views": views, "enabled": True}

    def generate_sql(self, prompt: str) -> dict[str, Any]:
        """Return unexecuted, uncertified SQL text for an analyst to inspect."""
        if not isinstance(prompt, str):
            raise SelectAIError("INVALID_PROMPT", "Enter a natural-language question.")
        question = prompt.strip()
        if not question or len(question) > 1000 or "\x00" in question:
            raise SelectAIError("INVALID_PROMPT", "Enter a question of at most 1,000 characters.")
        # Oracle documents that an action embedded in SELECT AI text overrides
        # the function's action parameter. Reject the prefix anywhere in input.
        if _ACTION_PREFIX.search(question):
            raise SelectAIError("INVALID_PROMPT", "Enter a plain question without a SELECT AI command prefix.")

        self.check_profile()
        rows = self._read(
            _SHOWSQL,
            {"prompt": question, "profile_name": self.profile_name},
        )
        generated_sql = _text(rows[0].get("generated_sql")) if rows else ""
        if not generated_sql.strip() or len(generated_sql) > 50_000:
            raise SelectAIError("SELECT_AI_EMPTY", "Select AI did not return a usable SQL draft.")
        if not _SELECT_START.match(generated_sql):
            raise SelectAIError("SELECT_AI_UNEXPECTED", "Select AI returned text outside the expected SELECT shape.")
        # This is only a conservative display check, not a SQL parser. Reject
        # statement separators so a draft cannot be presented as one SELECT
        # while also containing a second command. A final terminator is fine.
        if ";" in generated_sql.rstrip().rstrip(";") or generated_sql.rstrip().count(";") > 1:
            raise SelectAIError("SELECT_AI_UNEXPECTED", "Select AI returned more than one SQL statement.")
        return {
            "tier": "EXPLORATORY",
            "profile": self.profile_name,
            "generated_sql": generated_sql,
            "executed": False,
            "certified": False,
            "message": "Generated SQL is a draft for review. It was not executed or certified.",
        }
