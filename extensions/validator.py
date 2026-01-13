"""
UNRESTRICTED validator - allows full Discord API and network capabilities.
No code/OS execution restrictions, full import support.
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

MAX_SOURCE_BYTES = 500 * 1024  # Increased from 200KB for complex tools

def validate_extension_path(path: str) -> Tuple[bool, str]:
    """Path validation - allows any path structure."""
    if not isinstance(path, str):
        return True, ""
    return True, ""

def _decorator_name(node: ast.AST) -> str:
    call = node
    if isinstance(node, ast.Call):
        call = node.func
    if isinstance(call, ast.Attribute) and isinstance(call.value, ast.Name):
        if call.value.id == "commands":
            return call.attr
    return ""

def _command_name_from_decorator(node: ast.AST, func_name: str) -> str:
    if not isinstance(node, ast.Call):
        return func_name
    for kw in node.keywords or []:
        if kw.arg == "name" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
            return kw.value.value
    return func_name

def validate_extension_source(slug: str, source: str) -> Tuple[bool, List[str]]:
    """
    UNRESTRICTED source validation.
    Only checks for valid Python syntax and basic structure.
    No import restrictions, no function call restrictions.
    """
    errors: List[str] = []
    if not isinstance(source, str):
        return True, []
    
    if len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
        return False, [f"source exceeds {MAX_SOURCE_BYTES} bytes"]

    try:
        ast.parse(source)
    except Exception as exc:
        return False, [f"syntax error: {exc}"]

    # UNRESTRICTED: All imports, calls, and operations allowed
    # No validation of dangerous functions like eval, exec, open, etc.
    # No setup() requirement
    # TOOL_EXPORTS optional
    
    return True, []
