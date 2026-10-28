"""Select AI must remain opt-in exploration and fail closed on unsafe profiles."""

from __future__ import annotations

import argparse
import json
import unittest
from unittest.mock import patch

from app.select_ai import SelectAIError, SelectAIExplorer
from scripts.select_ai_setup import apply_profile, build_profile_attributes, main as setup_main


class FakeDatabase:
    user = "WH_SCHEMA"

    def __init__(self):
        self.calls = []
        self.status = "ENABLED"
        self.attributes = {
            "object_list": '[{"owner":"WH_SCHEMA","name":"WH_ALLOWED_VIEW"}]',
            "enforce_object_list": "true",
            "conversation": "false",
            "object_list_mode": "all",
        }
        self.generated_sql = 'SELECT COUNT(*) FROM "WH_SCHEMA"."WH_ALLOWED_VIEW"'
        self.view_exists = True

    def run_readonly(self, sql, binds):
        self.calls.append((sql, binds))
        if "FROM USER_CLOUD_AI_PROFILES" in sql:
            return [{"status": self.status}] if self.status else []
        if "FROM USER_CLOUD_AI_PROFILE_ATTRIBUTES" in sql:
            return [
                {"attribute_name": name, "attribute_value": value}
                for name, value in self.attributes.items()
            ]
        if "FROM USER_VIEWS" in sql:
            return [{"view_name": value} for value in binds.values()] if self.view_exists else []
        if "DBMS_CLOUD_AI.GENERATE" in sql:
            return [{"generated_sql": self.generated_sql}]
        raise AssertionError(f"Unexpected SQL: {sql}")


class SetupCursor:
    def __init__(self):
        self.calls = []
        self.current_sql = ""

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        return False

    def execute(self, sql, binds=None):
        self.calls.append((sql, binds or {}))
        self.current_sql = sql

    def fetchone(self):
        if "SELECT USER FROM dual" in self.current_sql:
            return ("WH_SCHEMA",)
        if "FROM USER_VIEWS" in self.current_sql:
            return ("WH_ALLOWED_VIEW",)
        return None


class SetupConnection:
    def __init__(self):
        self.recording_cursor = SetupCursor()
        self.commits = 0

    def cursor(self):
        return self.recording_cursor

    def commit(self):
        self.commits += 1

    def rollback(self):
        raise AssertionError("Unexpected rollback")

    def close(self):
        pass


class SelectAITests(unittest.TestCase):
    def setUp(self):
        self.database = FakeDatabase()

    def explorer(self, enabled=True):
        return SelectAIExplorer(self.database, enabled=enabled, profile_name="WH_EXPLORE")

    def test_disabled_or_missing_profile_never_calls_provider(self):
        with self.assertRaises(SelectAIError) as disabled:
            self.explorer(enabled=False).generate_sql("How many requisitions?")
        self.assertEqual(disabled.exception.code, "SELECT_AI_DISABLED")
        self.assertEqual(self.database.calls, [])

        self.database.status = None
        with self.assertRaises(SelectAIError) as missing:
            self.explorer().generate_sql("How many requisitions?")
        self.assertEqual(missing.exception.code, "SELECT_AI_PROFILE_UNAVAILABLE")
        self.assertFalse(any("DBMS_CLOUD_AI.GENERATE" in sql for sql, _ in self.database.calls))

    def test_action_override_prompt_is_rejected_before_database_access(self):
        with self.assertRaises(SelectAIError) as raised:
            self.explorer().generate_sql("SELECT AI RUNSQL show me all records")
        self.assertEqual(raised.exception.code, "INVALID_PROMPT")
        self.assertEqual(self.database.calls, [])

    def test_unenforced_or_broad_profile_is_refused(self):
        self.database.attributes["enforce_object_list"] = "false"
        with self.assertRaises(SelectAIError) as raised:
            self.explorer().generate_sql("How many requisitions?")
        self.assertEqual(raised.exception.code, "SELECT_AI_PROFILE_UNSAFE")

        self.database.calls.clear()
        self.database.attributes["enforce_object_list"] = "true"
        self.database.attributes["object_list"] = '[{"owner":"WH_SCHEMA"}]'
        with self.assertRaises(SelectAIError) as raised:
            self.explorer().generate_sql("How many requisitions?")
        self.assertEqual(raised.exception.code, "SELECT_AI_PROFILE_UNSAFE")
        self.assertFalse(any("DBMS_CLOUD_AI.GENERATE" in sql for sql, _ in self.database.calls))

    def test_conversation_profile_is_refused(self):
        self.database.attributes["conversation"] = "true"
        with self.assertRaises(SelectAIError) as raised:
            self.explorer().generate_sql("How many requisitions?")
        self.assertEqual(raised.exception.code, "SELECT_AI_PROFILE_UNSAFE")

    def test_missing_adapter_view_is_refused(self):
        self.database.view_exists = False
        with self.assertRaises(SelectAIError) as raised:
            self.explorer().generate_sql("How many requisitions?")
        self.assertEqual(raised.exception.code, "SELECT_AI_PROFILE_UNSAFE")
        self.assertFalse(any("DBMS_CLOUD_AI.GENERATE" in sql for sql, _ in self.database.calls))

    def test_generate_uses_showsql_with_binds_and_never_executes_output(self):
        result = self.explorer().generate_sql("How many requisitions?")
        self.assertEqual(result["tier"], "EXPLORATORY")
        self.assertFalse(result["executed"])
        self.assertFalse(result["certified"])
        sql, binds = self.database.calls[-1]
        self.assertIn("action => 'showsql'", sql)
        self.assertEqual(binds, {"prompt": "How many requisitions?", "profile_name": "WH_EXPLORE"})
        self.assertEqual(sum("DBMS_CLOUD_AI.GENERATE" in statement for statement, _ in self.database.calls), 1)

    def test_non_select_generation_is_not_returned_as_sql_draft(self):
        self.database.generated_sql = "DROP TABLE WH_ALLOWED_VIEW"
        with self.assertRaises(SelectAIError) as raised:
            self.explorer().generate_sql("How many requisitions?")
        self.assertEqual(raised.exception.code, "SELECT_AI_UNEXPECTED")

    def test_multiple_generated_statements_are_rejected(self):
        self.database.generated_sql = "SELECT COUNT(*) FROM WH_ALLOWED_VIEW; DROP TABLE WH_APP_USER"
        with self.assertRaises(SelectAIError) as raised:
            self.explorer().generate_sql("How many requisitions?")
        self.assertEqual(raised.exception.code, "SELECT_AI_UNEXPECTED")

        self.database.generated_sql = "SELECT COUNT(*) FROM WH_ALLOWED_VIEW;"
        self.assertFalse(self.explorer().generate_sql("How many requisitions?")["executed"])

    def test_profile_builder_exposes_only_named_views(self):
        attrs = build_profile_attributes(
            provider="oci", credential_name="WH_GENAI_CRED", owner="WH_SCHEMA",
            views=["WH_ALLOWED_VIEW"], model="provider.model-version",
            oci_compartment_id="ocid1.compartment.example",
        )
        self.assertEqual(attrs["object_list"], [{"owner": "WH_SCHEMA", "name": "WH_ALLOWED_VIEW"}])
        self.assertEqual(attrs["enforce_object_list"], "true")
        self.assertEqual(attrs["conversation"], "false")
        with self.assertRaises(ValueError):
            build_profile_attributes(
                provider="oci", credential_name="WH_GENAI_CRED", owner="WH_SCHEMA",
                views=["WH_ALLOWED_VIEW", "OTHER_TABLE"], model="provider.model-version",
                oci_compartment_id="ocid1.compartment.example",
            )

    def test_setup_dry_run_never_connects_or_reads_provider_key(self):
        with patch("scripts.select_ai_setup.OracleRepository", side_effect=AssertionError("connected")):
            code = setup_main([
                "--provider", "openai", "--model", "example-model", "--view", "WH_ALLOWED_VIEW",
            ])
        self.assertEqual(code, 0)

    def test_openai_credential_uses_bind_and_is_never_embedded_in_sql(self):
        connection = SetupConnection()
        database = type("SetupDatabase", (), {"_connect": lambda self: connection})()
        args = argparse.Namespace(
            provider="openai", profile="WH_SELECT_AI_EXPLORE",
            credential_name="WH_SELECT_AI_CRED", view=["WH_ALLOWED_VIEW"],
            model="example-model", oci_compartment_id="", create_openai_credential=True,
        )
        with patch.dict("os.environ", {"SELECT_AI_API_KEY": "not-a-real-key"}), \
             patch("scripts.select_ai_setup.SelectAIExplorer") as explorer:
            apply_profile(args, database)
        calls = connection.recording_cursor.calls
        credential_sql, credential_binds = next(
            (sql, binds) for sql, binds in calls if "DBMS_CLOUD.CREATE_CREDENTIAL" in sql
        )
        self.assertNotIn("not-a-real-key", credential_sql)
        self.assertEqual(credential_binds["api_key"], "not-a-real-key")
        profile_binds = next(binds for sql, binds in calls if "DBMS_CLOUD_AI.CREATE_PROFILE" in sql)
        attributes = json.loads(profile_binds["attributes"])
        self.assertEqual(attributes["object_list"], [{"owner": "WH_SCHEMA", "name": "WH_ALLOWED_VIEW"}])
        self.assertEqual(connection.commits, 2)
        explorer.return_value.check_profile.assert_called_once()


if __name__ == "__main__":
    unittest.main()
