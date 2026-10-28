"""Create a tightly scoped Oracle Select AI profile (dry-run unless --apply).

No provider secret is accepted on the command line or written to source. For
OpenAI, --create-openai-credential reads SELECT_AI_API_KEY from the local
environment and sends it to DBMS_CLOUD using a bind variable.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.db import DatabaseUnavailable, OracleRepository  # noqa: E402
from app.select_ai import SelectAIError, SelectAIExplorer  # noqa: E402


_IDENTIFIER = re.compile(r"^[A-Z][A-Z0-9_]*$")


def _wh_identifier(value: str, label: str) -> str:
    name = value.strip().upper()
    if len(name) > 125 or not _IDENTIFIER.fullmatch(name) or not name.startswith("WH_"):
        raise ValueError(f"{label} must be a simple WH_ prefixed Oracle identifier.")
    return name


def build_profile_attributes(
    *,
    provider: str,
    credential_name: str,
    owner: str,
    views: list[str],
    model: str,
    oci_compartment_id: str = "",
) -> dict[str, object]:
    """Build a profile that never discovers the owner's whole schema."""
    if provider not in {"oci", "openai"}:
        raise ValueError("Provider must be 'oci' or 'openai'.")
    credential = _wh_identifier(credential_name, "Credential name")
    schema = owner.strip().upper()
    if not _IDENTIFIER.fullmatch(schema):
        raise ValueError("The current Oracle schema name is unsupported.")
    names = [_wh_identifier(view, "View name") for view in views]
    if not 1 <= len(names) <= 64 or len(set(names)) != len(names):
        raise ValueError("Provide 1 to 64 distinct owned WH_ views.")
    if not model.strip() or len(model) > 200 or any(ord(char) < 32 for char in model):
        raise ValueError("Provide a supported model name without control characters.")
    attrs: dict[str, object] = {
        "provider": provider,
        "credential_name": credential,
        "model": model.strip(),
        "object_list": [{"owner": schema, "name": name} for name in names],
        "enforce_object_list": "true",
        "object_list_mode": "all",
        "conversation": "false",
        "max_tokens": 1024,
    }
    if provider == "oci":
        if not oci_compartment_id.strip():
            raise ValueError("OCI provider requires --oci-compartment-id.")
        attrs["oci_compartment_id"] = oci_compartment_id.strip()
    elif oci_compartment_id:
        raise ValueError("--oci-compartment-id applies only to the OCI provider.")
    return attrs


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("oci", "openai"), required=True)
    parser.add_argument("--profile", default="WH_SELECT_AI_EXPLORE")
    parser.add_argument("--credential-name", default="WH_SELECT_AI_CRED")
    parser.add_argument("--view", action="append", required=True, help="Owned WH_ adapter view; repeat as needed")
    parser.add_argument("--model", required=True, help="Supported provider model ID")
    parser.add_argument("--oci-compartment-id", default="")
    parser.add_argument(
        "--create-openai-credential",
        action="store_true",
        help="On --apply only, create the WH_ DB credential from local SELECT_AI_API_KEY if absent",
    )
    parser.add_argument("--apply", action="store_true", help="Create profile and optional credential in Oracle")
    return parser


def _exists(cursor, statement: str, binds: dict[str, str]) -> bool:
    cursor.execute(statement, binds)
    return cursor.fetchone() is not None


def apply_profile(args: argparse.Namespace, database: OracleRepository) -> None:
    """Apply only after all local and database preflight checks pass."""
    profile = _wh_identifier(args.profile, "Profile name")
    credential = _wh_identifier(args.credential_name, "Credential name")
    views = [_wh_identifier(view, "View name") for view in args.view]
    connection = database._connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT USER FROM dual")
            owner_row = cursor.fetchone()
            if owner_row is None:
                raise ValueError("Cannot determine the Oracle schema owner.")
            owner = str(owner_row[0]).upper()
            attributes = build_profile_attributes(
                provider=args.provider,
                credential_name=credential,
                owner=owner,
                views=views,
                model=args.model,
                oci_compartment_id=args.oci_compartment_id,
            )

            if _exists(
                cursor,
                "SELECT profile_name FROM USER_CLOUD_AI_PROFILES WHERE profile_name = :profile_name",
                {"profile_name": profile},
            ):
                raise ValueError("The profile already exists. Inspect it with select_aictl.py check before changing it.")
            for view in views:
                if not _exists(
                    cursor,
                    "SELECT view_name FROM USER_VIEWS WHERE view_name = :view_name",
                    {"view_name": view},
                ):
                    raise ValueError(f"{view} is not an owned view in this Oracle schema.")

            credential_exists = _exists(
                cursor,
                "SELECT credential_name FROM USER_CREDENTIALS WHERE credential_name = :credential_name",
                {"credential_name": credential},
            )
            if not credential_exists:
                if args.provider != "openai" or not args.create_openai_credential:
                    raise ValueError("The named database credential is absent. Create it before the profile.")
                api_key = os.getenv("SELECT_AI_API_KEY", "")
                if not api_key:
                    raise ValueError("Set SELECT_AI_API_KEY in the local environment to create the credential.")
                cursor.execute(
                    "BEGIN DBMS_CLOUD.CREATE_CREDENTIAL("
                    "credential_name => :credential_name, username => 'OPENAI', password => :api_key"
                    "); END;",
                    {"credential_name": credential, "api_key": api_key},
                )
                # A credential may be created even if subsequent profile setup fails.
                connection.commit()

            cursor.execute(
                "BEGIN DBMS_CLOUD_AI.CREATE_PROFILE("
                "profile_name => :profile_name, attributes => :attributes, "
                "description => 'Ask the Warehouse exploratory SQL display only'"
                "); END;",
                {"profile_name": profile, "attributes": json.dumps(attributes, separators=(",", ":"))},
            )
            connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    SelectAIExplorer(database, enabled=True, profile_name=profile).check_profile()


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        profile = _wh_identifier(args.profile, "Profile name")
        build_profile_attributes(
            provider=args.provider,
            credential_name=args.credential_name,
            owner="YOUR_SCHEMA",
            views=args.view,
            model=args.model,
            oci_compartment_id=args.oci_compartment_id,
        )
        if args.create_openai_credential and args.provider != "openai":
            raise ValueError("--create-openai-credential applies only to the OpenAI provider.")
        if not args.apply:
            print(f"Dry run: would create {profile} for {len(args.view)} named WH_ view(s). No Oracle connection opened.")
            return 0

        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env", override=False)
        apply_profile(args, OracleRepository())
        print(f"Created and verified {profile}. Set SELECT_AI_ENABLED=true and SELECT_AI_PROFILE={profile} to use it.")
        return 0
    except (ValueError, SelectAIError, DatabaseUnavailable, ImportError) as exc:
        print(f"Select AI setup failed: {exc}", file=sys.stderr)
        return 1
    except Exception:
        # Driver errors may include connection/provider details. Keep CLI output generic.
        print("Select AI setup failed. Check Oracle privileges, credential, model, and region locally.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
