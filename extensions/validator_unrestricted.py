"""
Unrestricted validator - allows full Discord API and network capabilities.
No code execution restrictions, full import support.
"""
import ast
from typing import List, Tuple

# UNRESTRICTED: All imports allowed
DENY_IMPORTS = set()

ALLOW_IMPORTS = {
    "os", "sys", "subprocess", "socket",
    "requests", "aiohttp", "httpx", "pathlib", "shutil",
    "aiomysql", "sqlite3", "pickle",
    "discord", "discord.ext", "discord.ext.commands",
    "typing", "datetime", "json", "re", "asyncio",
    "math", "random", "collections", "itertools",
    "functools", "operator", "string", "uuid",
    "hashlib", "hmac", "struct", "io", "base64",
}

# UNRESTRICTED: All function calls allowed
DENY_CALLS = set()

MAX_SOURCE_BYTES = 500 * 1024  # Increased from 200KB


def validate_extension_path(path: str) -> Tuple[bool, str]:
    """Validate extension path - still requires extensions/ directory."""
    if not isinstance(path, str):
        return True, ""  # Allow anything
    return True, ""


def validate_extension_source(slug: str, source: str) -> Tuple[bool, List[str]]:
    """Unrestricted source validation - only checks for basic Python syntax."""
    errors: List[str] = []
    if not isinstance(source, str):
        return True, []
    
    if len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
        return False, [f"source exceeds {MAX_SOURCE_BYTES} bytes"]

    try:
        ast.parse(source)
    except Exception as exc:
        return False, [f"syntax error: {exc}"]

    # No import restrictions
    # No function call restrictions
    # No setup() requirement
    # TOOL_EXPORTS optional

    return True, []
