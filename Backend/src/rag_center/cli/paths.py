from pathlib import Path

from alembic.config import Config


def migration_config() -> Config:
    """开发环境与 wheel 都从正式包加载迁移。"""
    root = Path(__file__).resolve().parents[1] / "migrations"
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root))
    return config
