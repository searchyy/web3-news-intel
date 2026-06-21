from __future__ import annotations

import argparse

import uvicorn
from fastapi import HTTPException

from app.core.config import load_sources, settings
from app.db import models  # noqa: F401
from app.db.base import Base
from app.db.repositories.source_repo import SourceRepository
from app.db.session import SessionLocal, engine
from app.main import app
from app.workers.tasks_fetch import _fetch_source


@app.post("/dev/run-source/{source_key}")
async def dev_run_source(source_key: str) -> dict[str, int | str]:
    if settings.app_env.lower() == "production":
        raise HTTPException(status_code=404, detail="not found")
    return await _fetch_source(source_key)


def seed_sources() -> None:
    Base.metadata.create_all(bind=engine)
    sources_file = load_sources()
    with SessionLocal() as session:
        repo = SourceRepository(session)
        for source in sources_file.sources.values():
            repo.upsert_from_config(source)
        session.commit()
    print(f"loaded={len(sources_file.sources)} enabled={len(sources_file.enabled_sources())}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=59134)
    args = parser.parse_args()
    seed_sources()
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
