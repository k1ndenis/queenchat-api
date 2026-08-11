#!/usr/bin/env python3
"""Explicit, interactive bootstrap for QueenChat's first administrator."""
import sys

from app.core.database import SessionLocal, UserORM


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python /app/scripts/set_admin.py <exact-username>")
        return 2
    username = sys.argv[1]
    db = SessionLocal()
    try:
        user = db.query(UserORM).filter(UserORM.username == username).one_or_none()
        if user is None:
            print("No user exists with that exact username; no changes made.")
            return 1
        print(f"Candidate: id={user.id} username=@{user.username} display_name={user.display_name!r} current_role={user.role}")
        if input("Type SET ADMIN to grant this account administrator access: ") != "SET ADMIN":
            print("Confirmation did not match; no changes made.")
            return 1
        user.role = "admin"
        db.commit()
        print(f"Administrator role granted to id={user.id} (@{user.username}).")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
