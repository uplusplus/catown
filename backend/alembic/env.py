from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from alembic import context

import os
import sys

# 将 backend 目录加入 path，以便 import 项目模块
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from models.database import Base
from config import settings

# Alembic Config
config = context.config

# 动态设置数据库 URL（优先环境变量，回退 .env 配置）
db_url = os.getenv("DATABASE_URL", settings.DATABASE_URL)
if db_url and not db_url.startswith("sqlite") and not db_url.startswith("postgresql"):
    # 相对路径 → SQLite URL
    db_url = f"sqlite:///{db_url}"
config.set_main_option("sqlalchemy.url", db_url)

# 日志
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Model metadata（供 autogenerate 使用）
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
