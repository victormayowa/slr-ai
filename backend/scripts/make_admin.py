"""Grant or remove platform administrator rights (model catalog and benchmarks).

uv run python -m scripts.make_admin someone@example.org
uv run python -m scripts.make_admin someone@example.org --remove
"""

import argparse
import sys

from sqlalchemy import select

import models
from database import SessionLocal


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("email")
    parser.add_argument("--remove", action="store_true")
    args = parser.parse_args()
    with SessionLocal() as db:
        user = db.scalar(select(models.User).where(models.User.email == args.email))
        if user is None:
            print(f"No account uses {args.email}", file=sys.stderr)
            return 1
        user.is_platform_admin = not args.remove
        db.commit()
    print(f"{args.email} is {'no longer' if args.remove else 'now'} a platform administrator")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
