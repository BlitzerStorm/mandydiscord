from __future__ import annotations

import asyncio
from pathlib import Path

import msgpack

from mandy_v1.config import Settings
from mandy_v1.storage import MessagePackStore


def test_settings_loads_from_environment_without_passwords_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DISCORD_TOKEN", "env-token")
    monkeypatch.setenv("ADMIN_GUILD_ID", "987")
    monkeypatch.setenv("GOD_USER_ID", "654")
    monkeypatch.setenv("COMMAND_PREFIX", "?")
    monkeypatch.setenv("STORE_PATH", "state/runtime.msgpack")
    monkeypatch.setenv("ALIBABA_MODEL", "qwen-max")

    settings = Settings.load()

    assert settings.discord_token == "env-token"
    assert settings.admin_guild_id == 987
    assert settings.god_user_id == 654
    assert settings.command_prefix == "?"
    assert settings.store_path == Path("state/runtime.msgpack")
    assert settings.alibaba_model == "qwen-max"


def test_settings_environment_overrides_passwords_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "passwords.txt").write_text(
        "DISCORD_TOKEN=file-token\nADMIN_GUILD_ID=111\nCOMMAND_PREFIX=!\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DISCORD_TOKEN", "env-token")
    monkeypatch.setenv("ADMIN_GUILD_ID", "222")

    settings = Settings.load()

    assert settings.discord_token == "env-token"
    assert settings.admin_guild_id == 222
    assert settings.command_prefix == "!"


def test_settings_rejects_invalid_integer(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "passwords.txt").write_text(
        "DISCORD_TOKEN=token\nADMIN_GUILD_ID=not-a-number\n",
        encoding="utf-8",
    )

    try:
        Settings.load()
    except RuntimeError as exc:
        assert "ADMIN_GUILD_ID must be an integer" in str(exc)
    else:
        raise AssertionError("invalid ADMIN_GUILD_ID should fail")


def test_store_recursively_migrates_nested_defaults(tmp_path: Path) -> None:
    path = tmp_path / "state.msgpack"
    old_store = {
        "meta": {"version": 1},
        "soc": {"role_tiers": {"ACCESS:Guest": 1}},
        "ai": {
            "prompt_injection": {
                "master_prompt": "existing",
            },
        },
    }
    path.write_bytes(msgpack.packb(old_store, use_bin_type=True))
    store = MessagePackStore(path)

    asyncio.run(store.load())

    assert store.data["ai"]["prompt_injection"]["master_prompt"] == "existing"
    assert store.data["ai"]["prompt_injection"]["audit_log"] == []
    assert store.data["feature_requests"]["grants"]["once"] == {}
    assert store.data["shadow_league"]["pending_user_ids"] == []
    assert store._dirty is True


def test_store_preserves_corrupt_file_before_reset(tmp_path: Path) -> None:
    path = tmp_path / "state.msgpack"
    path.write_bytes(b"not messagepack")
    store = MessagePackStore(path)

    try:
        asyncio.run(store.load())
    except RuntimeError as exc:
        assert "Corrupt copy:" in str(exc)
    else:
        raise AssertionError("corrupt store should fail loudly after reset")

    backups = list(tmp_path.glob("state.msgpack.corrupt-*"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == b"not messagepack"
    reloaded = msgpack.unpackb(path.read_bytes(), raw=False)
    assert reloaded["meta"]["version"] == 1
