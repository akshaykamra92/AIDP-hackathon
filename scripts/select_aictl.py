"""Check an owned Select AI profile or display unexecuted exploratory SQL.

The ask command reads its question from standard input rather than a shell
argument. It does not execute or certify the generated SQL.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.db import DatabaseUnavailable, OracleRepository  # noqa: E402
from app.select_ai import SelectAIError, SelectAIExplorer  # noqa: E402


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "ask"))
    args = parser.parse_args(argv)
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env", override=False)
        database = OracleRepository()
        explorer = SelectAIExplorer(database, enabled=True if args.command == "check" else None)
        if args.command == "check":
            result = explorer.check_profile()
            print(f"{result['profile']} is enabled with {len(result['views'])} owned WH_ view(s).")
        else:
            prompt = input("Question: ") if sys.stdin.isatty() else sys.stdin.read()
            result = explorer.generate_sql(prompt)
            print(json.dumps(result, indent=2))
        return 0
    except (SelectAIError, DatabaseUnavailable, ImportError) as exc:
        print(f"Select AI unavailable: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
