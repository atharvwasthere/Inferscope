"""initial schema (baseline)

Revision ID: 0001
Revises:
Create Date: 2026-05-25

Consolidated baseline — the full data model for inferscope: conversations,
messages (with reversible-PII pii_map), inference_logs (OTel-style usage +
request_id idempotency + trace_id correlation), provider_models (pricing +
the catalog served by GET /models), and outbox_events (the Redis-Streams
fallback). Seeds the supported provider/model pricing rows.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')

    op.create_table(
        "conversations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("cancelled_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )

    op.create_table(
        "messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("pii_map", postgresql.JSONB(), nullable=True),  # reversible PII tokenization map
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.CheckConstraint("role IN ('user','assistant')", name="messages_role_check"),
    )

    op.create_table(
        "inference_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("trace_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("message_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("messages.id", ondelete="SET NULL"), nullable=True),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("latency_ms", sa.Numeric(), nullable=True),
        sa.Column("time_to_first_token_ms", sa.Numeric(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("cache_read_input_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("cache_creation_input_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("reasoning_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Numeric(10, 6), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("input_preview", sa.Text(), nullable=True),
        sa.Column("output_preview", sa.Text(), nullable=True),
        sa.Column("raw_usage", postgresql.JSONB(), nullable=True),
        sa.Column("attributes", postgresql.JSONB(), nullable=True),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.CheckConstraint("status IN ('success','error')", name="logs_status_check"),
        sa.UniqueConstraint("request_id", name="uq_inference_logs_request_id"),  # idempotency key
    )

    op.create_table(
        "provider_models",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("cost_per_input_token", sa.Numeric(12, 8), nullable=False),
        sa.Column("cost_per_output_token", sa.Numeric(12, 8), nullable=False),
        sa.UniqueConstraint("provider", "model", name="provider_models_unique"),
    )

    op.create_table(
        "outbox_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("request_id", sa.Text(), nullable=False),
        sa.Column("trace_id", sa.Text(), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("processed", sa.Boolean(), nullable=False, server_default=sa.text("FALSE")),
        sa.Column("processed_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )

    # Hot-path indexes (dashboard scans recent-first; message/outbox lookups are filtered).
    op.create_index("idx_logs_created_at", "inference_logs", [sa.text("created_at DESC")])
    op.create_index("idx_logs_status", "inference_logs", ["status", sa.text("created_at DESC")])
    op.create_index("idx_logs_provider", "inference_logs", ["provider", sa.text("created_at DESC")])
    op.create_index("idx_logs_trace_id", "inference_logs", ["trace_id"])
    op.create_index("idx_messages_conversation", "messages", ["conversation_id", sa.text("created_at ASC")])
    op.create_index("idx_conversations_created_at", "conversations", [sa.text("created_at DESC")])
    op.create_index(
        "idx_messages_pii", "messages", ["pii_map"],
        postgresql_using="gin", postgresql_where=sa.text("pii_map IS NOT NULL"),
    )
    op.create_index(
        "idx_outbox_unprocessed", "outbox_events", ["created_at"],
        postgresql_where=sa.text("processed = FALSE"),
    )

    # Provider/model catalog + pricing (per-token = published $/1M ÷ 1e6). Groq rates from
    # groq.com/pricing. gpt-oss-20b input (7.5e-8) rounds to 8 dp under Numeric(12,8) — negligible.
    # gemini-3.5-flash and gemini-3.1-flash-lite use PLACEHOLDER rates (gemini-2.5-flash) — TODO real pricing.
    op.execute(
        """
        INSERT INTO provider_models (provider, model, cost_per_input_token, cost_per_output_token) VALUES
          ('bedrock', 'anthropic.claude-sonnet-4-6', 0.000003,    0.000015),
          ('bedrock', 'anthropic.claude-haiku-4-5',  0.00000025,  0.00000125),
          ('gemini',  'gemini-2.5-flash',            0.00000015,  0.0000006),
          ('gemini',  'gemini-3.1-pro',              0.000002,    0.000012),
          ('gemini',  'gemini-3.5-flash',            0.00000015,  0.0000006),
          ('gemini',  'gemini-3.1-flash-lite',       0.00000015,  0.0000006),
          ('groq',    'llama-3.3-70b-versatile',     0.00000059,  0.00000079),
          ('groq',    'llama-3.1-8b-instant',        0.00000005,  0.00000008),
          ('groq',    'qwen/qwen3-32b',              0.00000029,  0.00000059),
          ('groq',    'openai/gpt-oss-20b',          0.000000075, 0.0000003),
          ('groq',    'openai/gpt-oss-120b',         0.00000015,  0.0000006)
        """
    )


def downgrade() -> None:
    op.drop_table("outbox_events")
    op.drop_table("inference_logs")
    op.drop_table("messages")
    op.drop_table("provider_models")
    op.drop_table("conversations")
