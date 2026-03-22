"""add_personal_memory_chunks_table

Revision ID: 9c6f2a1b3d4e
Revises: d4d2d70d3faa
Create Date: 2026-03-15 16:40:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import HALFVEC

# revision identifiers, used by Alembic.
revision: str = "9c6f2a1b3d4e"
down_revision: Union[str, Sequence[str], None] = "d4d2d70d3faa"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "personal_memory_chunks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "discord_id",
            sa.String(),
            nullable=False,
            comment="成员的Discord数字ID（字符串）",
        ),
        sa.Column(
            "memory_type",
            sa.String(),
            nullable=False,
            server_default="unknown",
            comment="记忆类型：long_term/recent/unknown",
        ),
        sa.Column("memory_text", sa.Text(), nullable=False, comment="单条记忆文本"),
        sa.Column(
            "embedding",
            HALFVEC(3072),
            nullable=False,
            comment="此记忆条目的半精度嵌入向量",
        ),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True
        ),
        sa.PrimaryKeyConstraint("id"),
        schema="community",
    )

    op.create_index(
        "idx_pm_discord_id",
        "personal_memory_chunks",
        ["discord_id"],
        unique=False,
        schema="community",
    )

    op.create_index(
        "idx_pm_embedding_hnsw",
        "personal_memory_chunks",
        ["embedding"],
        unique=False,
        schema="community",
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"embedding": "halfvec_cosine_ops"},
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "idx_pm_embedding_hnsw",
        table_name="personal_memory_chunks",
        schema="community",
    )
    op.drop_index(
        "idx_pm_discord_id",
        table_name="personal_memory_chunks",
        schema="community",
    )
    op.drop_table("personal_memory_chunks", schema="community")