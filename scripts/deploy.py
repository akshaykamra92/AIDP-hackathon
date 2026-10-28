"""Deploy and verify the reviewed, synthetic Ask the Warehouse SQL scripts.

The runner accepts only the three repository-owned scripts. It does not accept
SQL or paths from command-line input and never prints connection settings.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = (
    ("schema", ROOT / "sql" / "01_schema.sql", {"DROP", "CREATE", "COMMENT"}),
    ("seed", ROOT / "sql" / "02_seed.sql", {"DELETE", "INSERT", "UPDATE", "COMMIT"}),
    ("verify", ROOT / "sql" / "03_verify.sql", {"SELECT", "WITH"}),
)


class DeploymentError(RuntimeError):
    """A deployment or verification step failed."""


def split_sql_script(source: str) -> list[str]:
    """Split plain Oracle SQL at semicolons outside quotes and comments.

    These reviewed scripts contain SQL statements, not PL/SQL blocks or SQLcl
    commands. Reject an unterminated final statement so a partial edit cannot
    silently change what gets deployed.
    """

    statements: list[str] = []
    current: list[str] = []
    state = "normal"
    index = 0
    while index < len(source):
        char = source[index]
        next_char = source[index + 1] if index + 1 < len(source) else ""

        if state == "normal":
            if char == "-" and next_char == "-":
                current.append(" ")
                state = "line_comment"
                index += 2
                continue
            if char == "/" and next_char == "*":
                current.append(" ")
                state = "block_comment"
                index += 2
                continue
            if char == "'":
                state = "single_quote"
            elif char == '"':
                state = "double_quote"
            elif char == ";":
                statement = "".join(current).strip()
                if statement:
                    statements.append(statement)
                current = []
                index += 1
                continue
            current.append(char)
        elif state == "line_comment":
            if char == "\n":
                current.append("\n")
                state = "normal"
        elif state == "block_comment":
            if char == "*" and next_char == "/":
                state = "normal"
                index += 2
                continue
        elif state == "single_quote":
            current.append(char)
            if char == "'":
                if next_char == "'":
                    current.append(next_char)
                    index += 2
                    continue
                state = "normal"
        else:  # double_quote
            current.append(char)
            if char == '"':
                if next_char == '"':
                    current.append(next_char)
                    index += 2
                    continue
                state = "normal"
        index += 1

    if state not in {"normal", "line_comment"}:
        raise DeploymentError("SQL script has an unterminated quote or block comment.")
    if "".join(current).strip():
        raise DeploymentError("SQL script ends with an unterminated statement; add a semicolon.")
    return statements


def load_statements() -> dict[str, list[str]]:
    """Read only the fixed scripts and reject unexpected statement types."""

    loaded: dict[str, list[str]] = {}
    for stage, path, allowed_verbs in SCRIPTS:
        statements = split_sql_script(path.read_text(encoding="utf-8"))
        if not statements:
            raise DeploymentError(f"{stage} script contains no statements.")
        for number, statement in enumerate(statements, 1):
            verb = statement.split(None, 1)[0].upper()
            if verb not in allowed_verbs:
                raise DeploymentError(f"{stage} statement {number} uses an unexpected verb: {verb}.")
        loaded[stage] = statements
    return loaded


def _check_table_counts(rows: Sequence[tuple[Any, ...]], *, fresh_seed: bool) -> None:
    """Keep seed tables exact; allow legitimate audit activity on later checks."""

    expected_seed_counts = {
        "WH_LOCATION": 6,
        "WH_EMPLOYEE": 2000,
        "WH_TERMINATION": 240,
        "WH_HEADCOUNT_MONTH": 432,
        "WH_REQUISITION": 360,
        "WH_METRIC": 5,
    }
    activity_tables = {"WH_METRIC_REQUEST", "WH_ANSWER_LOG"}
    actual_counts = {str(name): int(count) for name, count in rows}
    if set(actual_counts) != set(expected_seed_counts) | activity_tables:
        raise DeploymentError(f"Synthetic table list differs from expected: {sorted(actual_counts)}.")
    if any(actual_counts[name] != count for name, count in expected_seed_counts.items()):
        raise DeploymentError(f"Synthetic table counts differ from expected: {actual_counts}.")
    if any(actual_counts[name] < 0 or (fresh_seed and actual_counts[name] != 0) for name in activity_tables):
        qualifier = "immediately after seed load" if fresh_seed else "during read-only verification"
        raise DeploymentError(f"Audit/request counts are invalid {qualifier}: {actual_counts}.")


def _check_verification(results: Sequence[list[tuple[Any, ...]]], *, fresh_seed: bool) -> None:
    """Check the synthetic seed's core invariants, in 03_verify.sql order."""

    if len(results) != 10:
        raise DeploymentError(f"Verification script returned {len(results)} result sets; expected 10.")
    _check_table_counts(results[0], fresh_seed=fresh_seed)

    first_month, last_month, months, snapshot_rows = results[1][0]
    if (first_month.date().isoformat(), last_month.date().isoformat(), int(months), int(snapshot_rows)) != (
        "2025-04-01", "2026-09-01", 18, 432
    ):
        raise DeploymentError("Monthly snapshot coverage differs from expected.")
    if results[2]:
        raise DeploymentError("Some location/job combinations do not have 18 monthly snapshots.")
    if len(results[3]) != 5 or any(
        status != "CERTIFIED" or int(version) != 1 or marker != metric_id
        for metric_id, _name, status, version, marker, _dims, _filters in results[3]
    ):
        raise DeploymentError("Certified metric catalog differs from expected.")

    q3_rows = [row for row in results[4] if row[0] == "Q3 2026"]
    if len(q3_rows) != 1 or tuple(int(value) for value in q3_rows[0][1:4]) != (21, 4, 17):
        raise DeploymentError("Florida Q3 2026 event counts differ from expected.")
    if (float(q3_rows[0][5]), float(q3_rows[0][6])) != (1.89, 2.33):
        raise DeploymentError("Florida Q3 2026 turnover rates differ from expected.")
    if int(results[5][0][0]) != 33:
        raise DeploymentError("Historical transfer location count differs from expected.")
    if tuple(int(value) for value in results[6][0][:2]) != (288, 72):
        raise DeploymentError("Filled requisition counts differ from expected.")
    if tuple(int(value) for value in results[7][0][:2]) != (36, 7):
        raise DeploymentError("Q3 2026 filled requisition counts differ from expected.")
    if len(results[8]) != 8 or any(int(row[1]) != 4 for row in results[8]):
        raise DeploymentError("Table annotation coverage differs from expected.")
    if len(results[9]) != 8 or any(int(row[1]) != int(row[2]) for row in results[9]):
        raise DeploymentError("Column comment coverage differs from expected.")


def _connect():
    try:
        from dotenv import load_dotenv
        import oracledb
    except ImportError as exc:
        raise DeploymentError("Install requirements.txt before deploying.") from exc

    load_dotenv(ROOT / ".env", override=False)
    user = os.getenv("ORACLE_USER", "")
    password = os.getenv("ORACLE_PASSWORD", "")
    dsn = os.getenv("ORACLE_DSN", "")
    if not all((user, password, dsn)):
        raise DeploymentError("Set ORACLE_USER, ORACLE_PASSWORD, and ORACLE_DSN in the local environment.")
    try:
        return oracledb.connect(user=user, password=password, dsn=dsn)
    except Exception as exc:
        raise DeploymentError("Oracle connection failed. Check the local environment configuration.") from exc


def _oracle_code(exc: Exception) -> str:
    """Return only a driver error code, never connection details or SQL text."""

    error = exc.args[0] if exc.args else None
    code = getattr(error, "code", None)
    return f"ORA-{code:05d}" if isinstance(code, int) else type(exc).__name__


def _execute_stage(connection, stage: str, statements: Sequence[str]) -> None:
    with connection.cursor() as cursor:
        for number, statement in enumerate(statements, 1):
            try:
                if statement.upper() == "COMMIT":
                    connection.commit()
                else:
                    cursor.execute(statement)
            except Exception as exc:
                connection.rollback()
                raise DeploymentError(
                    f"{stage} statement {number}/{len(statements)} failed ({_oracle_code(exc)})."
                ) from exc
    print(f"Applied {stage}: {len(statements)} statements.")


def _verify(connection, statements: Sequence[str], *, fresh_seed: bool) -> None:
    results: list[list[tuple[Any, ...]]] = []
    with connection.cursor() as cursor:
        try:
            cursor.execute("SET TRANSACTION READ ONLY")
            for number, statement in enumerate(statements, 1):
                try:
                    cursor.execute(statement)
                    results.append(cursor.fetchall())
                except Exception as exc:
                    raise DeploymentError(
                        f"verify statement {number}/{len(statements)} failed ({_oracle_code(exc)})."
                    ) from exc
        finally:
            connection.rollback()
    _check_verification(results, fresh_seed=fresh_seed)
    print("Verification passed: counts, dates, certified metrics, Florida turnover, annotations, comments.")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Parse and validate scripts without connecting")
    mode.add_argument("--apply", action="store_true", help="Reset WH_ schema, load synthetic data, then verify")
    mode.add_argument("--verify-only", action="store_true", help="Run read-only synthetic data checks")
    args = parser.parse_args(argv)

    try:
        loaded = load_statements()
        if args.dry_run:
            for stage, statements in loaded.items():
                print(f"Parsed {stage}: {len(statements)} allowed SQL statements.")
            return 0

        connection = _connect()
        try:
            if args.apply:
                _execute_stage(connection, "schema", loaded["schema"])
                _execute_stage(connection, "seed", loaded["seed"])
            _verify(connection, loaded["verify"], fresh_seed=args.apply)
        finally:
            connection.close()
        return 0
    except DeploymentError as exc:
        print(f"Deployment failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
