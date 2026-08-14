#!/usr/bin/env python
"""Reassign ownership of existing data from one user id to another.

Why this exists: flipping ``ENABLE_MULTI_USER=true`` changes who you are. In
single-user mode every collection is owned by ``default``; the moment Access is
in front, you arrive as ``you@example.com`` and every collection you built
belongs to somebody else. The app is working correctly and your data has
vanished — which is an alarming way to discover this.

Run this once, after enabling multi-user mode and before wondering where
everything went:

    python scripts/reassign_owner.py --to you@example.com            # dry run
    python scripts/reassign_owner.py --to you@example.com --apply

Dry run is the default and prints exactly what would change.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# Tables carrying an owner column, and the column's name. Kept explicit rather
# than discovered, so a new table with a user column has to be considered here
# deliberately instead of being silently rewritten.
_OWNED: tuple[tuple[str, str], ...] = (
    ("collections", "owner_id"),
    ("collection_shares", "owner_id"),
    ("collection_share_users", "user_id"),
    ("collection_groups", "owner_id"),
    ("user_api_keys", "user_id"),
)


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--to", required=True, help="New owner id (your Access email, lowercased)")
    ap.add_argument("--from", dest="from_", default="default", help="Old owner id (default: 'default')")
    ap.add_argument("--db", default="data/app.db", help="Path to app.db (default: data/app.db)")
    ap.add_argument("--apply", action="store_true", help="Actually write. Without this, dry run.")
    args = ap.parse_args()

    new_owner = args.to.strip().lower()
    old_owner = args.from_.strip()
    if not new_owner:
        print("error: --to cannot be empty", file=sys.stderr)
        return 2
    if new_owner == old_owner:
        print("error: --to and --from are the same", file=sys.stderr)
        return 2

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"error: no database at {db_path}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(db_path)
    try:
        plan: list[tuple[str, str, int]] = []
        for table, column in _OWNED:
            if not _table_exists(conn, table):
                continue
            cols = {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}
            if column not in cols:
                continue
            (count,) = conn.execute(
                f'SELECT COUNT(*) FROM "{table}" WHERE "{column}" = ?', (old_owner,)
            ).fetchone()
            if count:
                plan.append((table, column, count))

        if not plan:
            print(f"Nothing owned by '{old_owner}'. No changes needed.")
            return 0

        total = sum(c for _, _, c in plan)
        print(f"{'APPLYING' if args.apply else 'DRY RUN'}: '{old_owner}' -> '{new_owner}' in {db_path}\n")
        for table, column, count in plan:
            print(f"  {table}.{column}: {count} row(s)")
        print(f"\n  total: {total} row(s)")

        if not args.apply:
            print("\nRe-run with --apply to write these changes.")
            return 0

        backup = db_path.with_suffix(
            f".backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db"
        )
        shutil.copy2(db_path, backup)
        print(f"\nBackup written to {backup}")

        with conn:
            for table, column, _ in plan:
                conn.execute(
                    f'UPDATE "{table}" SET "{column}" = ? WHERE "{column}" = ?',
                    (new_owner, old_owner),
                )
            # The users table keys on user_id, so provision the new owner if the
            # app has not seen them yet. Without this the collection list works
            # but the display name falls back to the raw email.
            if _table_exists(conn, "users"):
                cols = {r[1] for r in conn.execute('PRAGMA table_info("users")')}
                if "user_id" in cols:
                    conn.execute(
                        "INSERT OR IGNORE INTO users (user_id) VALUES (?)", (new_owner,)
                    )

        print(f"Done. {total} row(s) reassigned to '{new_owner}'.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
