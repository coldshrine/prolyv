from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, inspect

from alembic import command

ROOT = Path(__file__).resolve().parent.parent


def alembic_config() -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    return config


def test_migration_builds_the_schema_and_matches_the_models(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path}/migrated.db"
    monkeypatch.setenv("DATABASE_URL", url)
    config = alembic_config()

    command.upgrade(config, "head")
    inspector = inspect(create_engine(url))
    assert {"users", "identities", "auth_sessions", "teas", "tea_sessions", "steeps"} <= set(inspector.get_table_names())
    assert {c["name"] for c in inspector.get_columns("auth_sessions")} == {
        "id", "user_id", "token_hash", "expires_at", "created_at", "last_seen_at", "user_agent",
    }
    assert {c["name"] for c in inspector.get_columns("users")} == {
        "id", "handle", "display_name", "avatar_url", "bio", "created_at", "deleted_at", "banned_at",
    }
    uniques = {tuple(u["column_names"]) for u in inspector.get_unique_constraints("identities")}
    assert ("provider", "provider_user_id") in uniques

    command.check(config)  # autogenerate would add nothing: models and migrations agree

    command.downgrade(config, "base")
    assert "users" not in set(inspect(create_engine(url)).get_table_names())
