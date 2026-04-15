"""project-first projects schema baseline

Revision ID: 20260415_1015
Revises:
Create Date: 2026-04-15 10:15:00
"""
from alembic import op
import sqlalchemy as sa


revision = "20260415_1015"
down_revision = None
branch_labels = None
depends_on = None


def _get_columns(bind, table_name: str) -> set[str]:
    rows = bind.exec_driver_sql(f"PRAGMA table_info({table_name})").fetchall()
    return {row[1] for row in rows}


def _add_column_if_missing(bind, table_name: str, column_name: str, column) -> None:
    existing = _get_columns(bind, table_name)
    if column_name not in existing:
        op.add_column(table_name, column)


def upgrade() -> None:
    bind = op.get_bind()

    _add_column_if_missing(bind, "projects", "slug", sa.Column("slug", sa.String(), nullable=True))
    _add_column_if_missing(bind, "projects", "one_line_vision", sa.Column("one_line_vision", sa.Text(), nullable=True))
    _add_column_if_missing(bind, "projects", "target_users_json", sa.Column("target_users_json", sa.Text(), nullable=True, server_default="[]"))
    _add_column_if_missing(bind, "projects", "target_platforms_json", sa.Column("target_platforms_json", sa.Text(), nullable=True, server_default="[]"))
    _add_column_if_missing(bind, "projects", "primary_outcome", sa.Column("primary_outcome", sa.Text(), nullable=True))
    _add_column_if_missing(bind, "projects", "references_json", sa.Column("references_json", sa.Text(), nullable=True, server_default="[]"))
    _add_column_if_missing(bind, "projects", "current_stage", sa.Column("current_stage", sa.String(), nullable=True))
    _add_column_if_missing(bind, "projects", "execution_mode", sa.Column("execution_mode", sa.String(), nullable=True, server_default="autopilot"))
    _add_column_if_missing(bind, "projects", "health_status", sa.Column("health_status", sa.String(), nullable=True, server_default="healthy"))
    _add_column_if_missing(bind, "projects", "autopilot_enabled", sa.Column("autopilot_enabled", sa.Boolean(), nullable=True, server_default=sa.text("1")))
    _add_column_if_missing(bind, "projects", "current_focus", sa.Column("current_focus", sa.Text(), nullable=True))
    _add_column_if_missing(bind, "projects", "blocking_reason", sa.Column("blocking_reason", sa.Text(), nullable=True))
    _add_column_if_missing(bind, "projects", "latest_summary", sa.Column("latest_summary", sa.Text(), nullable=True))
    _add_column_if_missing(bind, "projects", "last_decision_id", sa.Column("last_decision_id", sa.Integer(), nullable=True))
    _add_column_if_missing(bind, "projects", "last_activity_at", sa.Column("last_activity_at", sa.DateTime(), nullable=True))
    _add_column_if_missing(bind, "projects", "released_at", sa.Column("released_at", sa.DateTime(), nullable=True))
    _add_column_if_missing(bind, "projects", "legacy_mode", sa.Column("legacy_mode", sa.Boolean(), nullable=True, server_default=sa.text("1")))
    _add_column_if_missing(bind, "projects", "updated_at", sa.Column("updated_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    raise NotImplementedError("SQLite-safe downgrade is not implemented for this baseline revision")
