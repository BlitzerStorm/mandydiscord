from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    discord_token: str
    admin_guild_id: int
    god_user_id: int
    command_prefix: str
    store_path: Path
    alibaba_api_key: str
    alibaba_base_url: str
    alibaba_model: str

    @staticmethod
    def load() -> "Settings":
        values = _load_config_values(Path("passwords.txt"))
        token = _get_setting(values, "DISCORD_TOKEN", "").strip()
        admin_guild_id = _int_setting(values, "ADMIN_GUILD_ID", default=0)
        god_user_id = _int_setting(values, "GOD_USER_ID", default=741470965359443970)
        command_prefix = _get_setting(values, "COMMAND_PREFIX", "!").strip() or "!"
        store_path = Path(_get_setting(values, "STORE_PATH", "data/mandy_v1.msgpack"))
        alibaba_api_key = _get_setting(values, "ALIBABA_API_KEY", "").strip()
        alibaba_base_url = _get_setting(
            values,
            "ALIBABA_BASE_URL",
            "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        ).strip()
        alibaba_model = _get_setting(values, "ALIBABA_MODEL", "qwen-plus").strip() or "qwen-plus"
        if not token:
            raise RuntimeError("DISCORD_TOKEN is required in passwords.txt or the environment.")
        if not admin_guild_id:
            raise RuntimeError("ADMIN_GUILD_ID is required in passwords.txt or the environment.")
        return Settings(
            discord_token=token,
            admin_guild_id=admin_guild_id,
            god_user_id=god_user_id,
            command_prefix=command_prefix,
            store_path=store_path,
            alibaba_api_key=alibaba_api_key,
            alibaba_base_url=alibaba_base_url,
            alibaba_model=alibaba_model,
        )


def _load_config_values(path: Path) -> dict[str, str]:
    values = _parse_passwords_file(path) if path.exists() else {}
    for key in (
        "DISCORD_TOKEN",
        "ADMIN_GUILD_ID",
        "GOD_USER_ID",
        "COMMAND_PREFIX",
        "STORE_PATH",
        "ALIBABA_API_KEY",
        "ALIBABA_BASE_URL",
        "ALIBABA_MODEL",
    ):
        env_value = os.getenv(key)
        if env_value is not None:
            values[key] = env_value
    if not values:
        raise RuntimeError("No configuration found. Create passwords.txt or set environment variables.")
    return values


def _get_setting(values: dict[str, str], key: str, default: str) -> str:
    return str(values.get(key, default))


def _int_setting(values: dict[str, str], key: str, *, default: int) -> int:
    raw = _get_setting(values, key, str(default)).strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{key} must be an integer, got {raw!r}.") from exc
    if value < 0:
        raise RuntimeError(f"{key} must be zero or greater, got {value}.")
    return value


def _parse_passwords_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values
