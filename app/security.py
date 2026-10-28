"""Bearer-token identities and active location grants stored in Oracle.

Tokens are generated with 256 bits of randomness by ``scripts/userctl.py``.
The database stores their SHA-256 digests, never the bearer values. Metric SQL
still enforces the grants in the same statement that reads source rows; this
module's grant lookup is for the user interface and early denial only.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass
from typing import Any

from app.db import OracleRepository


_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}\Z", re.ASCII)


def generate_token() -> str:
    """Return a URL-safe 256-bit bearer token for one-time delivery."""
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    if not isinstance(token, str) or not _TOKEN.fullmatch(token):
        raise ValueError("Application tokens must be 43 URL-safe characters.")
    return hashlib.sha256(token.encode("ascii")).hexdigest()


@dataclass(frozen=True)
class Principal:
    user_id: str
    display_name: str
    role: str


class SecurityRepository:
    def __init__(self, database: OracleRepository | None = None):
        self.database = database or OracleRepository()

    def security_ready(self) -> bool:
        rows = self.database.run_readonly(
            "SELECT COUNT(*) AS object_count FROM USER_TABLES WHERE TABLE_NAME IN "
            "('WH_APP_USER', 'WH_SEC_LOCATION', 'WH_USER_LOCATION', 'WH_ACCESS_AUDIT')",
            {},
        )
        if not rows or int(rows[0]["object_count"]) != 4:
            return False
        package_rows = self.database.run_readonly(
            "SELECT OBJECT_TYPE, STATUS FROM USER_OBJECTS "
            "WHERE OBJECT_NAME = 'WH_SECURITY_ADMIN' "
            "AND OBJECT_TYPE IN ('PACKAGE', 'PACKAGE BODY')",
            {},
        )
        units = {
            str(row["object_type"]).upper(): str(row["status"]).upper()
            for row in package_rows
        }
        return units == {"PACKAGE": "VALID", "PACKAGE BODY": "VALID"}

    def authenticate_token(self, token: str) -> Principal | None:
        try:
            digest = token_hash(token)
        except ValueError:
            return None
        rows = self.database.run_readonly(
            "SELECT USER_ID, DISPLAY_NAME, ROLE FROM WH_APP_USER "
            "WHERE TOKEN_HASH = :token_hash AND STATUS = 'ACTIVE'",
            {"token_hash": digest},
        )
        if not rows:
            return None
        row = rows[0]
        return Principal(
            user_id=str(row["user_id"]),
            display_name=str(row["display_name"]),
            role=str(row["role"]),
        )

    def active_locations(self, user_id: str) -> list[dict[str, Any]]:
        rows = self.database.run_readonly(
            "SELECT loc.LOCATION_KEY, loc.DISPLAY_NAME "
            "FROM WH_USER_LOCATION acl "
            "JOIN WH_SEC_LOCATION loc ON loc.LOCATION_KEY = acl.LOCATION_KEY "
            "JOIN WH_APP_USER usr ON usr.USER_ID = acl.USER_ID "
            "WHERE acl.USER_ID = :auth_user_id AND usr.STATUS = 'ACTIVE' "
            "AND acl.STATUS = 'ACTIVE' AND loc.STATUS = 'ACTIVE' "
            "ORDER BY loc.LOCATION_KEY",
            {"auth_user_id": user_id},
        )
        return [
            {"location_key": str(row["location_key"]), "display_name": str(row["display_name"])}
            for row in rows
        ]
