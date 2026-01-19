from discord.ext import commands

TOOL_EXPORTS = {
    "tool_ping": {
        "description": "Simple ping tool for plugin testing.",
        "args_schema": {},
        "side_effect": "read",
        "cost": "cheap",
        "handler": None,
    }
}


async def _tool_ping(ctx):
    return "OK"


TOOL_EXPORTS["tool_ping"]["handler"] = _tool_ping


async def setup(bot):
    return
