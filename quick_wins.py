"""
Quick-win implementations for Mandy AI improvements.
Start with these to enable both novice-friendly AND wizard-ready features.
"""

import asyncio
import discord
from discord.ext import commands
from typing import Dict, Any, List, Tuple, Optional, Callable
import time
import re
from difflib import get_close_matches
import json


# ============================================================
# 1. FRIENDLY ERROR FORMATTER (Easy - Immediate Impact)
# ============================================================

class FriendlyErrorFormatter:
    """Convert technical errors into human-friendly guidance with recovery steps."""
    
    def format_error(
        self, 
        error_type: str, 
        error_message: str, 
        context: Dict[str, Any]
    ) -> str:
        """
        Transform raw errors into helpful messages.
        
        Args:
            error_type: "user_not_found", "missing_args", "rate_limit", "validation", etc.
            error_message: Raw error text
            context: Dict with relevant info (query, tool, guild, etc.)
        
        Returns:
            Formatted user-friendly error message
        """
        
        formatters = {
            "user_not_found": self._user_not_found,
            "missing_args": self._missing_args,
            "rate_limit": self._rate_limit,
            "validation": self._validation_error,
            "permission_denied": self._permission_denied,
            "tool_not_found": self._tool_not_found,
        }
        
        formatter = formatters.get(error_type, self._generic_error)
        return formatter(error_message, context)
    
    def _user_not_found(self, msg: str, ctx: Dict) -> str:
        query = ctx.get("query", "user")
        return (
            f"❌ I couldn't find the user: **{query}**\n\n"
            f"**Try one of these:**\n"
            f"- Use @mention: `@{query}`\n"
            f"- Type exact username\n"
            f"- Reply `list` to see all users\n"
            f"- Reply `help` for command examples\n"
        )
    
    def _missing_args(self, msg: str, ctx: Dict) -> str:
        tool = ctx.get("tool", "command")
        missing = ctx.get("missing_args", [])
        example = ctx.get("example_usage", "")
        
        result = (
            f"❌ Missing information for **{tool}**\n\n"
            f"**Need:** {', '.join(missing)}\n"
        )
        
        if example:
            result += f"\n**Example:** `{example}`"
        
        result += f"\n\nReply `help {tool}` for more details."
        return result
    
    def _rate_limit(self, msg: str, ctx: Dict) -> str:
        wait = ctx.get("wait_seconds", 60)
        return (
            f"⏳ I'm busy right now (rate limited).\n\n"
            f"**Try again in:** {wait} seconds\n"
            f"**While you wait:** Check `queue` or `health` status\n"
        )
    
    def _validation_error(self, msg: str, ctx: Dict) -> str:
        field = ctx.get("field", "input")
        reason = ctx.get("reason", "invalid value")
        allowed = ctx.get("allowed_values")
        
        result = f"❌ Invalid value for **{field}**: {reason}\n"
        
        if allowed:
            result += f"\n**Allowed:** {', '.join(str(v) for v in allowed)}\n"
        
        result += f"\nReply `help {ctx.get('tool', 'command')}` for valid inputs."
        return result
    
    def _permission_denied(self, msg: str, ctx: Dict) -> str:
        required_level = ctx.get("required_level", "GOD")
        return (
            f"❌ You don't have permission for this action.\n\n"
            f"**Required:** {required_level} level\n"
            f"**Your level:** {ctx.get('your_level', 'Guest')}\n"
            f"\nContact a staff member to escalate your access."
        )
    
    def _tool_not_found(self, msg: str, ctx: Dict) -> str:
        tool = ctx.get("tool", "unknown")
        suggestions = ctx.get("suggestions", [])
        
        result = f"❌ Tool not found: **{tool}**\n\n"
        
        if suggestions:
            result += f"**Did you mean?**\n"
            for suggestion in suggestions[:3]:
                result += f"- `{suggestion}`\n"
        
        result += f"\nUse `tools` to see all available commands."
        return result
    
    def _generic_error(self, msg: str, ctx: Dict) -> str:
        return (
            f"⚠️ Something went wrong:\n"
            f"```\n{msg[:200]}\n```\n\n"
            f"**Next steps:**\n"
            f"1. Reply `help` for command examples\n"
            f"2. Try `health` to check bot status\n"
            f"3. Ask staff if the problem persists"
        )


# ============================================================
# 2. SKILL LEVEL TRACKING (Medium - Enables Learning Paths)
# ============================================================

class UserProfile:
    """Track user skill level and adapt help/suggestions accordingly."""
    
    LEVELS = {"beginner": 1, "intermediate": 2, "advanced": 3}
    
    def __init__(self):
        self.profiles: Dict[int, Dict[str, Any]] = {}
    
    def get_level(self, user_id: int) -> Tuple[int, str]:
        """Get user skill level (1-3) and name."""
        if user_id not in self.profiles:
            self.profiles[user_id] = self._init_profile()
        
        level = self.profiles[user_id].get("level", 1)
        level_name = ["beginner", "intermediate", "advanced"][level - 1]
        return level, level_name
    
    def record_action(self, user_id: int, action: str, success: bool):
        """Learn from user actions. Auto-promote on progression."""
        if user_id not in self.profiles:
            self.profiles[user_id] = self._init_profile()
        
        profile = self.profiles[user_id]
        profile["actions"].append({
            "action": action,
            "success": success,
            "ts": time.time()
        })
        
        # Keep only recent 50 actions
        profile["actions"] = profile["actions"][-50:]
        
        # Auto-level logic
        self._update_level(profile)
    
    def _update_level(self, profile: Dict):
        """Auto-promote user based on successful actions."""
        current_level = profile.get("level", 1)
        
        if current_level < 3:  # Can still rank up
            recent = [
                a for a in profile["actions"][-20:] 
                if time.time() - a.get("ts", 0) < 86400 * 7  # Last 7 days
            ]
            
            # Count successful "advanced" actions
            advanced = sum(1 for a in recent if "advanced" in a["action"] and a["success"])
            
            # Promotion thresholds
            if current_level == 1 and advanced >= 5:
                profile["level"] = 2
                profile["level_up_ts"] = time.time()
            
            elif current_level == 2 and advanced >= 10:
                profile["level"] = 3
                profile["level_up_ts"] = time.time()
    
    def suggest_help_level(self, user_id: int, topic: str) -> str:
        """Get help at appropriate complexity."""
        level, level_name = self.get_level(user_id)
        
        help_db = {
            "dm": {
                "beginner": "DM one user: `dm @user \"message\"`",
                "intermediate": "DM multiple: `dm @user1 and @user2 \"message\"`",
                "advanced": "Template: `dm @user \"Hi {{user.name}}, check {{guild.name}}!\"` - variables auto-fill"
            },
            "watchers": {
                "beginner": "`watcher add @user after 5 say Hello`",
                "intermediate": "Add watcher: `watcher add @user <count> <message>`",
                "advanced": "Watchers hook into message events. Combine with mirrors for automation."
            },
            "mirrors": {
                "beginner": "Mirrors auto-copy messages between channels.",
                "intermediate": "`mirror create <source> <dest> [filter]`",
                "advanced": "Mirrors support regex filters, cost limits, conditional rules. See schema."
            }
        }
        
        return help_db.get(topic, {}).get(level_name, f"Unknown topic: {topic}")
    
    def _init_profile(self) -> Dict[str, Any]:
        return {
            "level": 1,
            "joined_ts": time.time(),
            "actions": [],
            "level_up_ts": None,
            "preferences": {
                "show_tips": True,
                "help_level": "auto",
                "max_wizard_steps": 10,
            }
        }


# ============================================================
# 3. SMART SUGGESTIONS (Medium - Increases Engagement)
# ============================================================

class SmartSuggestions:
    """Suggest next steps based on user actions."""
    
    ACTION_SUGGESTIONS = {
        "dm": [
            "💡 Use `watcher add` to auto-message users in the future",
            "💡 Check `show stats daily` to see DM impact",
        ],
        "watcher add": [
            "💡 List watchers: `show watchers`",
            "💡 Remove later: `watcher remove @user`",
        ],
        "mirror create": [
            "💡 Check mirror status: `queue`",
            "💡 See all rules: `mirrors`",
        ],
        "tools": [
            "💡 Learn each tool: `help <tool_name>`",
            "💡 Interactive menu: `!menu` (try it!)",
        ],
        "show stats": [
            "💡 Export stats: `stats export csv`",
            "💡 See trends: `stats analyze 30d`",
        ],
    }
    
    def get_suggestions(self, last_action: str, user_level: int) -> List[str]:
        """Get suggestions for next steps."""
        suggestions = self.ACTION_SUGGESTIONS.get(last_action, [])
        
        # Show 1-2 suggestions based on user level
        if user_level == 1:
            return suggestions[:1]  # Beginner: simpler suggestions
        elif user_level == 2:
            return suggestions[:2]  # Intermediate: more options
        else:
            return suggestions  # Advanced: all suggestions


# ============================================================
# 4. COMMAND BUILDER WIZARD (Hard - Game Changer for Novices)
# ============================================================

class WizardStep:
    """One step in a command builder."""
    
    def __init__(
        self,
        title: str,
        description: str,
        input_type: str,
        validation_fn: Optional[Callable[[str], Tuple[bool, str]]] = None,
        examples: Optional[List[str]] = None,
    ):
        self.title = title
        self.description = description
        self.input_type = input_type  # "text", "number", "mention", "choice"
        self.validation_fn = validation_fn
        self.examples = examples or []
        self.value = None


class CommandWizard:
    """Build Discord commands step-by-step without typing syntax."""
    
    def __init__(self, bot: commands.Bot, command_name: str):
        self.bot = bot
        self.command_name = command_name
        self.steps: List[WizardStep] = []
        self.current_step = 0
        self.result = {}
        self.user_id = None
        self.channel = None
    
    async def run(
        self, 
        user_id: int, 
        channel: discord.abc.Messageable
    ) -> Optional[Dict[str, str]]:
        """Run interactive wizard."""
        self.user_id = user_id
        self.channel = channel
        
        await self._send_intro()
        
        while self.current_step < len(self.steps):
            step = self.steps[self.current_step]
            
            # Show step
            embed = self._build_step_embed(step)
            await channel.send(embed=embed)
            
            # Get user input
            try:
                response = await self.bot.wait_for(
                    "message",
                    check=lambda m: m.author.id == user_id and m.channel == channel,
                    timeout=300
                )
            except asyncio.TimeoutError:
                await channel.send("⏱️ Wizard timed out. Start over with `!menu`.")
                return None
            
            content = response.content.strip()
            
            # Handle special commands
            if content.lower() == "cancel":
                await channel.send("Wizard cancelled.")
                return None
            
            if content.lower() == "back":
                if self.current_step > 0:
                    self.current_step -= 1
                    continue
                else:
                    await channel.send("Already at first step!")
                    continue
            
            # Validate input
            if step.validation_fn:
                is_valid, error = step.validation_fn(content)
                if not is_valid:
                    await channel.send(f"❌ {error}\n\nTry again (or type `cancel` to quit):")
                    continue
            
            # Store result
            self.result[step.title] = content
            self.current_step += 1
            
            # Show progress
            progress = f"{self.current_step}/{len(self.steps)}"
            await channel.send(f"✅ Step {progress} complete.")
        
        # Review results
        return await self._confirm_and_execute()
    
    def _build_step_embed(self, step: WizardStep) -> discord.Embed:
        """Create rich embed for a wizard step."""
        progress = f"{self.current_step + 1}/{len(self.steps)}"
        
        embed = discord.Embed(
            title=f"{self.command_name} - Step {progress}",
            description=step.description,
            color=discord.Color.blue()
        )
        
        if step.examples:
            embed.add_field(
                name="Examples:",
                value="\n".join(f"• {ex}" for ex in step.examples),
                inline=False
            )
        
        embed.set_footer(text="Type 'cancel' to quit, 'back' to go back")
        return embed
    
    async def _send_intro(self):
        """Send introduction."""
        embed = discord.Embed(
            title=f"🧙 {self.command_name} Wizard",
            description=f"Let's build this command together! {len(self.steps)} steps.",
            color=discord.Color.gold()
        )
        embed.add_field(name="Cancel", value="Type `cancel` at any time", inline=False)
        await self.channel.send(embed=embed)
    
    async def _confirm_and_execute(self) -> Dict[str, str]:
        """Show summary and ask for confirmation."""
        summary = "\n".join(f"**{k}:** {v}" for k, v in self.result.items())
        
        embed = discord.Embed(
            title="✨ Command Ready",
            description=summary,
            color=discord.Color.green()
        )
        embed.set_footer(text="Reply: confirm, or type 'back' to edit")
        
        await self.channel.send(embed=embed)
        
        try:
            response = await self.bot.wait_for(
                "message",
                check=lambda m: m.author.id == self.user_id and m.channel == self.channel,
                timeout=60
            )
        except asyncio.TimeoutError:
            await self.channel.send("Confirmation timed out.")
            return None
        
        if response.content.lower() == "confirm":
            return self.result
        elif response.content.lower() == "back":
            self.current_step = max(0, self.current_step - 1)
            await self.run(self.user_id, self.channel)
        else:
            await self.channel.send("Cancelled.")
            return None


# ============================================================
# 5. BETTER SUGGESTION (Easy - Reduces Typos)
# ============================================================

def suggest_tool_or_command(
    query: str, 
    available_tools: List[str],
    cutoff: float = 0.6
) -> Optional[str]:
    """Find close match to mistyped command."""
    matches = get_close_matches(query, available_tools, n=1, cutoff=cutoff)
    return matches[0] if matches else None


# ============================================================
# 6. STRUCTURED LOGGING (Medium - Essential for Debugging)
# ============================================================

class StructuredLogging:
    """Rich logs for debugging, auditing, and analytics."""
    
    def __init__(self):
        self.logs: List[Dict[str, Any]] = []
        self.MAX_LOGS = 5000
    
    async def log_event(
        self,
        event_type: str,
        severity: str,
        user_id: int,
        details: Dict[str, Any],
        channel_id: Optional[int] = None,
        guild_id: Optional[int] = None,
    ):
        """
        Log a structured event.
        
        event_type: "command_executed", "error", "rate_limit", "tool_call"
        severity: "info", "warning", "error", "critical"
        """
        
        entry = {
            "ts": time.time(),
            "type": event_type,
            "severity": severity,
            "user_id": user_id,
            "channel_id": channel_id,
            "guild_id": guild_id,
            "details": details,
        }
        
        self.logs.append(entry)
        
        # Trim old logs
        if len(self.logs) > self.MAX_LOGS:
            self.logs = self.logs[-self.MAX_LOGS:]
    
    def get_logs_for_user(self, user_id: int, limit: int = 50) -> List[Dict]:
        """Get recent logs for a specific user."""
        return [
            log for log in self.logs[-limit:]
            if log["user_id"] == user_id
        ]
    
    def get_errors(self, limit: int = 20) -> List[Dict]:
        """Get recent errors."""
        return [
            log for log in self.logs[-limit:]
            if log["severity"] in ("error", "critical")
        ]


# ============================================================
# 7. TEMPLATE ENGINE (Advanced - For Bulk Operations)
# ============================================================

class TemplateRenderer:
    """Simple template rendering for bulk operations."""
    
    SAFE_VARS = {
        "user": ["id", "name", "display_name", "mention"],
        "guild": ["id", "name", "member_count"],
        "channel": ["id", "name", "topic"],
    }
    
    def render(self, template: str, **context) -> str:
        """
        Render template with context.
        
        Example:
            template = "Hi {{user.name}}, welcome to {{guild.name}}!"
            result = renderer.render(template, user=user, guild=guild)
        """
        result = template
        
        # Replace {{obj.attr}} patterns
        import re
        pattern = r"\{\{(\w+)\.(\w+)\}\}"
        
        def replace_var(match):
            obj_name = match.group(1)
            attr_name = match.group(2)
            
            if obj_name not in context:
                return match.group(0)  # Keep original
            
            obj = context[obj_name]
            if not hasattr(obj, attr_name):
                return match.group(0)
            
            return str(getattr(obj, attr_name))
        
        result = re.sub(pattern, replace_var, result)
        return result
    
    def validate_template(self, template: str) -> Tuple[bool, str]:
        """Check template for invalid variables."""
        pattern = r"\{\{(\w+)\.(\w+)\}\}"
        matches = re.findall(pattern, template)
        
        for obj_name, attr_name in matches:
            if obj_name not in self.SAFE_VARS:
                return False, f"Unknown object: {obj_name}"
            
            if attr_name not in self.SAFE_VARS[obj_name]:
                return False, f"Unknown attribute: {obj_name}.{attr_name}"
        
        return True, ""

