import json
import sys
import types
import unittest


def _install_discord_stubs():
    if "discord" in sys.modules:
        return
    discord = types.SimpleNamespace()

    class DummyView:
        def __init__(self, timeout=None):
            self.timeout = timeout
            self.children = []

        def add_item(self, item):
            self.children.append(item)

    class DummyButton:
        def __init__(self, label=None, style=None):
            self.label = label
            self.style = style
            self.callback = None

    def button(*args, **kwargs):
        def decorator(func):
            return func
        return decorator

    class DummyTextChannel:
        pass

    class DummyUser:
        def __init__(self, user_id):
            self.id = user_id

    class DummyMember(DummyUser):
        def __init__(self, user_id, name="user", display_name="user", global_name=None):
            super().__init__(user_id)
            self.name = name
            self.display_name = display_name
            self.global_name = global_name

    class DummyGuild:
        def __init__(self, guild_id=1, members=None, name="Guild"):
            self.id = guild_id
            self.members = members or []
            self.name = name

        def get_member(self, user_id):
            for member in self.members:
                if member.id == user_id:
                    return member
            return None

    class DummyInteraction:
        def __init__(self):
            self.user = DummyUser(0)
            self.channel = None
            self.guild = None
            self.response = types.SimpleNamespace(
                send_message=lambda *args, **kwargs: None,
                edit_message=lambda *args, **kwargs: None,
            )

    discord.ui = types.SimpleNamespace(View=DummyView, button=button, Button=DummyButton)
    discord.ButtonStyle = types.SimpleNamespace(primary=1, danger=2, success=3, secondary=4)
    discord.TextChannel = DummyTextChannel
    discord.abc = types.SimpleNamespace(Messageable=object)
    discord.User = DummyUser
    discord.Member = DummyMember
    discord.Guild = DummyGuild
    discord.Interaction = DummyInteraction

    commands = types.SimpleNamespace()
    commands.Cog = object

    def command(*args, **kwargs):
        def decorator(func):
            return func
        return decorator

    commands.command = command
    commands.Context = object

    discord_ext = types.SimpleNamespace(commands=commands)
    sys.modules["discord"] = discord
    sys.modules["discord.ext"] = discord_ext
    sys.modules["discord.ext.commands"] = commands


_install_discord_stubs()

from capability_registry import CapabilityRegistry
from cogs.mandy_ai import ConfirmView, MandyAI


class DummyStore:
    async def mark_dirty(self):
        return


class DummyChannel(sys.modules["discord"].TextChannel):
    def __init__(self):
        self.sent = []
        self.id = 123

    async def send(self, content=None, view=None):
        self.sent.append({"content": content, "view": view})


class DummyTools:
    def __init__(self):
        self.sent = []
        self.dynamic_tools = {}

    async def send_message(self, channel_id: int, text: str):
        self.sent.append((channel_id, text))
        return {"message_id": 42}

    async def get_recent_transcript(self, channel_id: int, limit: int = 50):
        return []

    async def show_stats(self, scope: str, user_id=None, guild_id=None):
        return f"User stats ({scope}) for {user_id}"

    async def send_dm(self, user_id: int, text: str):
        return {"message_id": 1}

    def list_dynamic_tools(self):
        return list(self.dynamic_tools.keys())

    def get_dynamic_tool(self, name: str):
        return self.dynamic_tools.get(name)


class DummyBot:
    def __init__(self):
        self.mandy_tools = DummyTools()
        self.mandy_registry = CapabilityRegistry(self.mandy_tools)
        self.mandy_get_ai_config = lambda: {
            "default_model": "gemini",
            "router_model": "gemini",
            "tts_model": "",
            "cooldown_seconds": 0,
            "limits": {},
            "queue": {},
            "installed_extensions": [],
        }
        self.mandy_cfg = lambda: {"logs": {}}
        self.mandy_store = DummyStore()
        self.mandy_log_to = None
        self.mandy_audit = None
        self.mandy_effective_level = None
        self.mandy_require_level_ctx = None
        self.mandy_api_key = "key"
        self.mandy_runtime = {"counters": {}, "last_actions": [], "last_rate_limit": None}
        self.extensions = {}

    def get_channel(self, channel_id):
        return None

    async def fetch_channel(self, channel_id):
        return None

    def get_user(self, user_id):
        return None

    async def fetch_user(self, user_id):
        return None


class FakeGemini:
    def __init__(self, responses):
        self.available = True
        self._responses = list(responses)

    async def generate(self, *args, **kwargs):
        return self._responses.pop(0)


class MandyAITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bot = DummyBot()
        self.mandy = MandyAI(self.bot)
        self.channel = DummyChannel()
        self.guild = sys.modules["discord"].Guild(1, members=[])
        self.user = sys.modules["discord"].User(999)

    async def test_action_executes_tool(self):
        payload = {
            "intent": "ACTION",
            "response": "ok",
            "actions": [{"tool": "send_message", "args": {"channel_id": 123, "text": "hi"}}],
        }
        self.mandy.client = FakeGemini([json.dumps(payload)])
        await self.mandy._process_request(self.user, self.channel, self.guild, 0, "send hi")
        self.assertTrue(self.bot.mandy_tools.sent)
        self.assertTrue(self.channel.sent)

    async def test_validation_rejects_unknown_tool(self):
        ok, _ = self.mandy._validate_action({"tool": "unknown_tool", "args": {}})
        self.assertFalse(ok)

    async def test_build_requires_confirmation(self):
        payload = {
            "intent": "BUILD_TOOL",
            "response": "build it",
            "build": {
                "slug": "demo",
                "files": [{"path": "extensions/demo.py", "content": "print('hi')"}],
            },
        }
        self.mandy.client = FakeGemini([json.dumps(payload), json.dumps(payload)])
        called = {"build": 0}

        async def fake_build(*args, **kwargs):
            called["build"] += 1
            return True, "ok"

        self.mandy._handle_build_tool = fake_build
        await self.mandy._process_request(self.user, self.channel, self.guild, 0, "build demo")
        self.assertEqual(called["build"], 0)
        self.assertTrue(self.channel.sent)
        self.assertIsInstance(self.channel.sent[-1]["view"], ConfirmView)

        await self.mandy._process_request(self.user, self.channel, self.guild, 0, "build demo", confirmed=True)
        self.assertEqual(called["build"], 1)

    async def test_design_tool_requires_confirmation(self):
        payload = {
            "intent": "DESIGN_TOOL",
            "response": "design",
            "tool_design": {
                "name": "tool_demo",
                "description": "demo tool",
                "args_schema": {"value": {"type": "str"}},
                "side_effect": "read",
                "cost": "cheap",
                "needs_confirmation": True,
                "example_calls": ["use tool_demo value=abc"],
            },
        }
        self.mandy.client = FakeGemini([json.dumps(payload)])
        await self.mandy._process_request(self.user, self.channel, self.guild, 0, "design demo")
        self.assertTrue(self.channel.sent)
        self.assertIsInstance(self.channel.sent[-1]["view"], ConfirmView)


if __name__ == "__main__":
    unittest.main()
