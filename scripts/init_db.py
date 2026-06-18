from __future__ import annotations

from app.db import models  # noqa: F401
from app.db.base import Base
from app.db.session import engine


def main() -> None:
    Base.metadata.create_all(bind=engine)
    print("database schema created")


if __name__ == "__main__":
    main()
