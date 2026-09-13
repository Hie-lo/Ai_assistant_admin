"""Phase 5: platform connections + publications (Telegram V1)

Revision ID: e9f0a1b2c3d4
Revises: d8e9f0a1b2c3
Create Date: 2026-09-15

Adds the platform adapter domain (PLATFORM_ADAPTER_SPECIFICATION_V1,
POST_PUBLICATION_DOMAIN_SPECIFICATION_V1 sections 3, 11-21, 25):

- ``platform_connections``: a business's connection to a platform target.
  Shared organization bot (owner decision 2026-09-15) — the credential is
  platform-level, so NO secret is stored per business.
- ``posts`` / ``post_versions``: the logical, platform-independent content
  object and its immutable rendered snapshots (content + media
  fingerprints; historical content is reproducible).
- ``publications``: a platform-specific remote instance of a PostVersion
  with an explicit state machine and a deterministic ``idempotency_key``
  (effectively-once, spec section 14).
- ``publication_attempts``: durable per-operation attempt records
  (spec section 30).

DB backstop (PostgreSQL partial unique index): at most ONE ``PUBLISHED``
publication per (connection, product) — the application state machine
enforces the same invariant, the index makes it durable.

Additive only; rollback drops the new tables in dependency order.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e9f0a1b2c3d4"
down_revision: str | None = "d8e9f0a1b2c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- platform_connections ---
    op.create_table(
        "platform_connections",
        sa.Column("connection_id", sa.Uuid(), nullable=False),
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column(
            "platform",
            sa.Enum("TELEGRAM", "BALE", "EITAA", "RUBIKA", name="platform", native_enum=False),
            nullable=False,
        ),
        sa.Column("target_name", sa.String(200), nullable=False),
        sa.Column("platform_target_id", sa.String(128), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING_VERIFICATION",
                "VERIFIED",
                "PERMISSION_LOST",
                "DISCONNECTED",
                name="platformconnectionstatus",
                native_enum=False,
            ),
            nullable=False,
            server_default="PENDING_VERIFICATION",
        ),
        sa.Column("control_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(48), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("connection_id"),
        sa.UniqueConstraint(
            "business_id", "platform", "platform_target_id", name="uq_platform_conn_target"
        ),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.business_id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.user_id"]),
    )
    op.create_index(
        "ix_platform_connections_business_id", "platform_connections", ["business_id"]
    )

    # --- posts ---
    op.create_table(
        "posts",
        sa.Column("post_id", sa.Uuid(), nullable=False),
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "PUBLISHED",
                "UPDATE_REQUIRED",
                "REPOST_REQUIRED",
                "DELETE_REQUIRED",
                "ARCHIVED",
                name="poststatus",
                native_enum=False,
            ),
            nullable=False,
            server_default="PUBLISHED",
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("post_id"),
        sa.UniqueConstraint("business_id", "product_id", name="uq_posts_business_product"),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.business_id"]),
        sa.ForeignKeyConstraint(["product_id"], ["products.product_id"]),
    )
    op.create_index("ix_posts_business_id", "posts", ["business_id"])
    op.create_index("ix_posts_product_id", "posts", ["product_id"])

    # --- post_versions ---
    op.create_table(
        "post_versions",
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("post_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("product_version_id", sa.Uuid(), nullable=True),
        sa.Column("preset_version_id", sa.Uuid(), nullable=True),
        sa.Column("ai_artifact_refs", sa.JSON(), nullable=False),
        sa.Column("content_fingerprint", sa.String(64), nullable=False),
        sa.Column("media_fingerprint", sa.String(64), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("media_urls", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("version_id"),
        sa.UniqueConstraint("post_id", "version", name="uq_post_versions_no"),
        sa.ForeignKeyConstraint(["post_id"], ["posts.post_id"]),
        sa.ForeignKeyConstraint(["product_version_id"], ["product_versions.version_id"]),
        sa.ForeignKeyConstraint(["preset_version_id"], ["preset_versions.version_id"]),
    )
    op.create_index("ix_post_versions_post_id", "post_versions", ["post_id"])

    # --- publications ---
    op.create_table(
        "publications",
        sa.Column("publication_id", sa.Uuid(), nullable=False),
        sa.Column("post_id", sa.Uuid(), nullable=False),
        sa.Column("post_version_id", sa.Uuid(), nullable=False),
        sa.Column("connection_id", sa.Uuid(), nullable=False),
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "NOT_PUBLISHED",
                "QUEUED",
                "PUBLISHING",
                "PUBLISHED",
                "UPDATE_PENDING",
                "UPDATING",
                "REPOST_PENDING",
                "REPOSTING",
                "DELETE_PENDING",
                "DELETING",
                "FAILED_RETRYABLE",
                "FAILED_FINAL",
                "UNKNOWN_REMOTE_STATE",
                "RECONCILING",
                "REMOTE_DELETED",
                "PERMISSION_LOST",
                "DISCONNECTED",
                name="publicationstatus",
                native_enum=False,
            ),
            nullable=False,
            server_default="NOT_PUBLISHED",
        ),
        sa.Column("remote_message_id", sa.String(64), nullable=True),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("remote_fingerprint", sa.String(64), nullable=True),
        sa.Column("remote_modified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("error_code", sa.String(48), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("publication_id"),
        sa.UniqueConstraint("idempotency_key", name="uq_publications_idempotency"),
        sa.ForeignKeyConstraint(["post_id"], ["posts.post_id"]),
        sa.ForeignKeyConstraint(["post_version_id"], ["post_versions.version_id"]),
        sa.ForeignKeyConstraint(["connection_id"], ["platform_connections.connection_id"]),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.business_id"]),
        sa.ForeignKeyConstraint(["product_id"], ["products.product_id"]),
    )
    op.create_index("ix_publications_post_id", "publications", ["post_id"])
    op.create_index("ix_publications_connection_id", "publications", ["connection_id"])
    op.create_index("ix_publications_business_id", "publications", ["business_id"])
    op.create_index("ix_publications_product_id", "publications", ["product_id"])
    # Durable backstop for the application invariant: at most ONE PUBLISHED
    # publication per (connection, product). Ignored on SQLite.
    op.create_index(
        "uq_publications_one_live_per_target",
        "publications",
        ["connection_id", "product_id"],
        unique=True,
        postgresql_where=sa.text("status = 'PUBLISHED'"),
    )

    # --- publication_attempts ---
    op.create_table(
        "publication_attempts",
        sa.Column("attempt_id", sa.Uuid(), nullable=False),
        sa.Column("publication_id", sa.Uuid(), nullable=False),
        sa.Column(
            "operation",
            sa.Enum(
                "PUBLISH",
                "EDIT",
                "REPOST",
                "DELETE",
                "RECONCILE",
                "VERIFY_CONNECTION",
                name="publicationoperation",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum("SUCCESS", "FAILED", "UNKNOWN", name="attemptstatus", native_enum=False),
            nullable=False,
        ),
        sa.Column("error_code", sa.String(48), nullable=True),
        sa.Column("error_detail", sa.String(300), nullable=True),
        sa.Column("remote_message_id", sa.String(64), nullable=True),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("attempt_id"),
        sa.ForeignKeyConstraint(["publication_id"], ["publications.publication_id"]),
    )
    op.create_index(
        "ix_publication_attempts_publication_id", "publication_attempts", ["publication_id"]
    )


def downgrade() -> None:
    op.drop_table("publication_attempts")
    op.drop_table("publications")
    op.drop_table("post_versions")
    op.drop_table("posts")
    op.drop_table("platform_connections")
