import sys
import types


def _install_discord_stubs():
    class _AttrFallback(types.SimpleNamespace):
        def __getattr__(self, name):
            dummy = type(name, (), {})
            setattr(self, name, dummy)
            return dummy

    discord = _AttrFallback()

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

    class DummySelectOption:
        def __init__(self, label=None, value=None, description=None, default=False):
            self.label = label
            self.value = value
            self.description = description
            self.default = default

    class DummySelect:
        def __init__(self, options=None, placeholder=None, min_values=1, max_values=1):
            self.options = options or []
            self.placeholder = placeholder
            self.min_values = min_values
            self.max_values = max_values
            self.values = []

        async def callback(self, interaction):
            return

    class DummyTextInput:
        def __init__(self, label=None, placeholder=None, required=False, max_length=None, default=None):
            self.label = label
            self.placeholder = placeholder
            self.required = required
            self.max_length = max_length
            self.value = default or ""

    class DummyModal:
        def __init__(self, *args, **kwargs):
            return

        def __init_subclass__(cls, **kwargs):
            return

    def button(*args, **kwargs):
        def decorator(func):
            return func
        return decorator

    class DummyTextChannel:
        pass

    class DummyPCMVolumeTransformer:
        def __init__(self, source):
            self.source = source

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

    discord.ui = types.SimpleNamespace(
        View=DummyView,
        button=button,
        Button=DummyButton,
        Select=DummySelect,
        Modal=DummyModal,
        TextInput=DummyTextInput,
    )
    discord.ButtonStyle = types.SimpleNamespace(
        primary=1,
        danger=2,
        success=3,
        secondary=4,
        green=3,
        red=2,
        blurple=1,
        gray=4,
    )
    discord.SelectOption = DummySelectOption
    discord.TextChannel = DummyTextChannel
    discord.PCMVolumeTransformer = DummyPCMVolumeTransformer
    discord.Client = object
    discord.abc = types.SimpleNamespace(Messageable=object)
    discord.User = DummyUser
    discord.Member = DummyMember
    discord.Guild = DummyGuild
    discord.Interaction = DummyInteraction

    commands = _AttrFallback()
    commands.Cog = object
    commands.Bot = object

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
