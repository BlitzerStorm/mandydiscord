from __future__ import annotations

from . import config, state
from .db_queries import db_exec, db_one
from .db_schema import db_calibrate


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

