"""project-first event fields

Revision ID: 20260415_1028
Revises: 20260415_1015
Create Date: 2026-04-15 10:28:00
"""
from alembic import op
import sqlalchemy as sa


revision = "20260415_1028"
down_revision = "20260415_1015"
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
    _add_column_if_missing(bind, "events", "project_id", sa.Column("project_id", sa.Integer(), nullable=True))
    _add_column_if_missing(bind, "events", "stage_run_id", sa.Column("stage_run_id", sa.Integer(), nullable=True))
    _add_column_if_missing(bind, "events", "asset_id", sa.Column("asset_id", sa.Integer(), nullable=True))

    # SQLite cannot easily add FK constraints post-hoc, but indexes give the new read-model paths acceptable performance.
    with op.batch_alter_table("events") as batch_op:
        existing = _get_columns(bind, "events")
        if "project_id" in existing:
            batch_op.create_index("ix_events_project_id", ["project_id"], unique=False)
        if "stage_run_id" in existing:
            batch_op.create_index("ix_events_stage_run_id", ["stage_run_id"], unique=False)
        if "asset_id" in existing:
            batch_op.create_index("ix_events_asset_id", ["asset_id"], unique=False)


def downgrade() -> None:
    raise NotImplementedError("SQLite-safe downgrade is not implemented for this project-first event revision")
