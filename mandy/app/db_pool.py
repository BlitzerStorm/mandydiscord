from __future__ import annotations

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

