from __future__ import annotations

from . import state
from .db_queries import ensure_table_columns


async def ensure_mirror_rules_columns():
    await ensure_table_columns(
        "mirror_rules",
        {
            "enabled": "BOOLEAN NOT NULL DEFAULT TRUE",
            "fail_count": "INT NOT NULL DEFAULT 0",
            "last_error": "TEXT",
            "last_mirror_ts": "BIGINT",
            "last_mirror_msg": "TEXT",
            "last_disabled_at": "BIGINT NOT NULL DEFAULT 0",
        },
    )


async def ensure_watchers_columns():
    await ensure_table_columns(
        "watchers",
        {
            "threshold": "INT NOT NULL DEFAULT 0",
            "current": "INT NOT NULL DEFAULT 0",
            "text": "TEXT",
            "updated_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP",
        },
    )


async def ensure_users_permissions_columns():
    await ensure_table_columns(
        "users_permissions",
        {
            "note": "TEXT",
            "updated_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP",
        },
    )


async def ensure_mirrors_columns():
    await ensure_table_columns(
        "mirrors",
        {
            "enabled": "BOOLEAN NOT NULL DEFAULT TRUE",
            "updated_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP",
        },
    )


async def ensure_mirror_messages_columns():
    await ensure_table_columns(
        "mirror_messages",
        {
            "mirror_id": "VARCHAR(96) NOT NULL",
            "src_guild": "BIGINT NOT NULL",
            "src_channel": "BIGINT NOT NULL",
            "src_msg": "BIGINT NOT NULL",
            "dst_msg": "BIGINT NOT NULL",
            "author_id": "BIGINT NOT NULL",
            "created_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
        },
    )


async def ensure_dm_bridges_columns():
    await ensure_table_columns(
        "dm_bridges",
        {
            "channel_id": "BIGINT NOT NULL",
            "active": "BOOLEAN NOT NULL DEFAULT TRUE",
            "last_activity": "BIGINT",
            "updated_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP",
        },
    )


async def ensure_audit_logs_columns():
    await ensure_table_columns(
        "audit_logs",
        {
            "action": "TEXT",
            "meta": "JSON",
            "created_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
        },
    )


async def db_calibrate():
    if not state.POOL:
        return
    await ensure_users_permissions_columns()
    await ensure_mirrors_columns()
    await ensure_mirror_rules_columns()
    await ensure_mirror_messages_columns()
    await ensure_watchers_columns()
    await ensure_dm_bridges_columns()
    await ensure_audit_logs_columns()

