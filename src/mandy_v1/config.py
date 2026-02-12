from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class Settings:
    discord_token: str
    admin_guild_id: int
    god_user_id: int
    command_prefix: str
    store_path: Path

    @staticmethod
    def load() -> "Settings":
        values = _parse_passwords_file(Path("passwords.txt"))
        token = values.get("DISCORD_TOKEN", "").strip()
        admin_guild_id = int(values.get("ADMIN_GUILD_ID", "0"))
        god_user_id = int(values.get("GOD_USER_ID", "741470965359443970"))
        command_prefix = values.get("COMMAND_PREFIX", "!")
        store_path = Path(values.get("STORE_PATH", "data/mandy_v1.msgpack"))
        if not token:
            raise RuntimeError("DISCORD_TOKEN is required in passwords.txt.")
        if not admin_guild_id:
            raise RuntimeError("ADMIN_GUILD_ID is required in passwords.txt.")
        return Settings(
            discord_token=token,
            admin_guild_id=admin_guild_id,
            god_user_id=god_user_id,
            command_prefix=command_prefix,
            store_path=store_path,
        )


def _parse_passwords_file(path: Path) -> dict[str, str]:
    if not path.exists():
        raise RuntimeError("passwords.txt not found. Copy passwords.example.txt to passwords.txt and fill values.")
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
