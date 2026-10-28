"""Access CLI writes one-time tokens privately and cleans up failed delivery."""

from __future__ import annotations

import io
import stat
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from app.security import Principal, token_hash
from scripts import userctl


class _ReadySecurity:
    def __init__(self, _database=None):
        pass

    def security_ready(self):
        return True

    def authenticate_token(self, token):
        if token == "A" * 43:
            return Principal("admin-1", "Admin", "ADMIN")
        return None


class UserControlTests(unittest.TestCase):
    def test_token_file_is_private_and_path_cannot_escape_ignored_work(self):
        with tempfile.TemporaryDirectory() as root:
            with patch.object(userctl, "ROOT", Path(root)):
                path = userctl._write_token(Path("work/reader.token"), "A" * 43)
                self.assertEqual(path.read_text(encoding="ascii"), "A" * 43 + "\n")
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
                for unsafe in (Path("outside.token"), Path("work/../outside.token")):
                    with self.subTest(unsafe=unsafe), self.assertRaises(userctl.UserOperationError):
                        userctl._write_token(unsafe, "B" * 43)
                with self.assertRaises(userctl.UserOperationError):
                    userctl._write_token(Path("work/reader.token"), "B" * 43)

    def test_admin_token_file_requires_private_mode_and_active_admin(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "admin.token"
            path.write_text("A" * 43 + "\n", encoding="ascii")
            path.chmod(0o644)
            with self.assertRaises(userctl.UserOperationError):
                userctl._admin_from_file(path, _ReadySecurity())
            path.chmod(0o600)
            self.assertEqual(userctl._admin_from_file(path, _ReadySecurity()).user_id, "admin-1")
            path.write_text("B" * 43 + "\n", encoding="ascii")
            with self.assertRaises(userctl.UserOperationError):
                userctl._admin_from_file(path, _ReadySecurity())

    def test_bootstrap_failure_removes_token_and_never_prints_it(self):
        with tempfile.TemporaryDirectory() as root:
            output = io.StringIO()
            errors = io.StringIO()
            with patch.object(userctl, "ROOT", Path(root)), \
                 patch.object(userctl, "_database", return_value=object()), \
                 patch.object(userctl, "SecurityRepository", _ReadySecurity), \
                 patch.object(userctl, "generate_token", return_value="A" * 43), \
                 patch.object(userctl, "_call", side_effect=userctl.UserOperationError("simulated database failure")) as call, \
                 redirect_stdout(output), redirect_stderr(errors):
                result = userctl.main([
                    "bootstrap", "--user-id", "admin-1", "--display-name", "Admin",
                    "--token-out", "work/admin.token",
                ])
            self.assertEqual(result, 1)
            self.assertFalse((Path(root) / "work" / "admin.token").exists())
            self.assertEqual(call.call_args.args[2], ["admin-1", "Admin", token_hash("A" * 43)])
            self.assertNotIn("A" * 43, output.getvalue() + errors.getvalue())


if __name__ == "__main__":
    unittest.main()
