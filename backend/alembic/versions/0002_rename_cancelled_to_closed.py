"""rename conversations.cancelled_at -> closed_at

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-04

The column originally named ``cancelled_at`` semantically meant "the
conversation is closed for further sends" — a soft lock on the row. A separate
stream-level abort (`POST /streams/{stream_id}/abort`) now handles real
mid-generation cancellation, so the column name is moved to match its actual
behavior. See README "Stream-abort signal" tradeoff entry.
"""
from alembic import op


revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("conversations", "cancelled_at", new_column_name="closed_at")


def downgrade() -> None:
    op.alter_column("conversations", "closed_at", new_column_name="cancelled_at")
