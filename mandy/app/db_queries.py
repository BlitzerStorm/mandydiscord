from __future__ import annotations

from typing import Dict

import aiomysql

from . import state


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


async def db_column_exists(table: str, column: str) -> bool:
    if not state.POOL:
        return False
    row = await db_one(
        "SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND COLUMN_NAME=%s",
        (table, column),
    )
    return bool(row)


async def ensure_table_columns(table: str, cols: Dict[str, str]):
    if not state.POOL:
        return
    for col, ddl in cols.items():
        if await db_column_exists(table, col):
            continue
        await db_exec(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")

