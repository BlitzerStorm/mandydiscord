from typing import Any, Dict

import aiomysql

from . import config, state


async def db_init():
    if not config.MYSQL_ENABLED:
        return
    state.POOL = await aiomysql.create_pool(
        host=config.MYSQL_HOST,
        user=config.MYSQL_USER,
        password=config.MYSQL_PASS or "",
        db=config.MYSQL_DB,
        autocommit=True,
        minsize=1,
        maxsize=10,
        charset="utf8mb4",
    )


async def db_exec(sql: str, args: tuple = ()):
    if not state.POOL:
        return
    async with state.POOL.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql, args)


async def db_one(sql: str, args: tuple = ()):
    if not state.POOL:
        return None
    async with state.POOL.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(sql, args)
            return await cur.fetchone()


async def db_all(sql: str, args: tuple = ()):
    if not state.POOL:
        return []
    async with state.POOL.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(sql, args)
            return await cur.fetchall()


async def ensure_table_columns(table: str, cols: Dict[str, str]):
    if not state.POOL:
        return
    for col, ddl in cols.items():
        if await db_column_exists(table, col):
            continue
        await db_exec(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")


async def db_column_exists(table: str, column: str) -> bool:
    if not state.POOL:
        return False
    row = await db_one(
        "SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND COLUMN_NAME=%s",
        (table, column),
    )
    return bool(row)


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


async def db_purge_all(keep_watchers: bool = False):
    if not state.POOL:
        return False
    tables = [
        "mirror_messages",
        "mirror_rules",
        "mirrors",
        "dm_bridges",
        "audit_logs",
        "users_permissions",
    ]
    if not keep_watchers:
        tables.append("watchers")
    for table in tables:
        try:
            await db_exec(f"TRUNCATE TABLE {table}")
        except Exception:
            try:
                await db_exec(f"DELETE FROM {table}")
            except Exception as e:
                from .logging import setup_log

                await setup_log(f"MySQL purge failed for {table}: {e}")
    await db_bootstrap()
    return True


async def db_bootstrap():
    if not state.POOL:
        return

    await db_exec(
        """
    CREATE TABLE IF NOT EXISTS users_permissions (
      user_id BIGINT PRIMARY KEY,
      level INT NOT NULL,
      note TEXT,
      updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """
    )

    await db_exec(
        """
    CREATE TABLE IF NOT EXISTS mirrors (
      mirror_id VARCHAR(64) PRIMARY KEY,
      source_guild BIGINT NOT NULL,
      source_channel BIGINT NOT NULL,
      target_channel BIGINT NOT NULL,
      enabled BOOLEAN NOT NULL DEFAULT TRUE,
      updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """
    )

    await db_exec(
        """
    CREATE TABLE IF NOT EXISTS mirror_rules (
      rule_id VARCHAR(96) PRIMARY KEY,
      scope VARCHAR(16) NOT NULL,
      source_guild BIGINT NOT NULL,
      source_id BIGINT NOT NULL,
      target_channel BIGINT NOT NULL,
      enabled BOOLEAN NOT NULL DEFAULT TRUE,
      fail_count INT NOT NULL DEFAULT 0,
      last_error TEXT,
      last_mirror_ts BIGINT,
      last_mirror_msg TEXT,
      last_disabled_at BIGINT NOT NULL DEFAULT 0,
      updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """
    )

    await db_exec(
        """
    CREATE TABLE IF NOT EXISTS mirror_messages (
      id BIGINT AUTO_INCREMENT PRIMARY KEY,
      mirror_id VARCHAR(96) NOT NULL,
      src_guild BIGINT NOT NULL,
      src_channel BIGINT NOT NULL,
      src_msg BIGINT NOT NULL,
      dst_msg BIGINT NOT NULL,
      author_id BIGINT NOT NULL,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      INDEX (mirror_id),
      INDEX (dst_msg),
      INDEX (src_msg)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """
    )

    await db_exec(
        """
    CREATE TABLE IF NOT EXISTS watchers (
      user_id BIGINT PRIMARY KEY,
      threshold INT NOT NULL,
      current INT NOT NULL DEFAULT 0,
      text TEXT NOT NULL,
      updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """
    )

    await db_exec(
        """
    CREATE TABLE IF NOT EXISTS dm_bridges (
      user_id BIGINT PRIMARY KEY,
      channel_id BIGINT NOT NULL,
      active BOOLEAN NOT NULL DEFAULT TRUE,
      last_activity BIGINT,
      updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """
    )

    await db_exec(
        """
    CREATE TABLE IF NOT EXISTS audit_logs (
      id BIGINT AUTO_INCREMENT PRIMARY KEY,
      actor_id BIGINT NOT NULL,
      action TEXT NOT NULL,
      meta JSON,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """
    )
    await db_calibrate()

    await db_exec(
        """
    INSERT INTO users_permissions (user_id, level, note)
    VALUES (%s, %s, %s)
    ON DUPLICATE KEY UPDATE level=GREATEST(level, VALUES(level));
    """,
        (config.SUPER_USER_ID, 100, "Immutable SUPERUSER"),
    )

    row = await db_one("SELECT user_id FROM users_permissions WHERE user_id=%s", (config.AUTO_GOD_ID,))
    if not row:
        await db_exec(
            """
        INSERT INTO users_permissions (user_id, level, note)
        VALUES (%s,%s,%s)
        """,
            (config.AUTO_GOD_ID, 90, "Auto-added GOD"),
        )
