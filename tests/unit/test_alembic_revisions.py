from __future__ import annotations

from alembic.config import Config
from alembic.script import ScriptDirectory


def test_alembic_revision_ids_fit_default_version_table() -> None:
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    too_long = [
        revision.revision
        for revision in script.walk_revisions()
        if len(str(revision.revision)) > 32
    ]

    assert too_long == []
