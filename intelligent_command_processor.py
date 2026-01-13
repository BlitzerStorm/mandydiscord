"""
Intelligent Command Processor - Production Ready Code
Handles natural language commands without requiring users to learn syntax.
"""

import re
import asyncio
import discord
from typing import Optional, Dict, List, Tuple, Any
from difflib import SequenceMatcher
import time


class IntentRecognizer:
    """Recognize user intent from raw input text."""
    
    INTENT_PATTERNS = {
        "send_dm": [
            r"^(?:dm|message|msg|send|tell|write)\s+(.+?)\s+(?:that\s+)?(.+)$",
            r"^(?:send|give)\s+(.+?)\s+(?:the\s+)?message\s+(.+)$",
            r"^(?:tell|message|write|ping)\s+(.+?)\s+(.+)$",
            r"^(.+?)\s+(?:dm|message|msg)\s+(.+)$",
        ],
        "add_watcher": [
            r"^(?:watch|add\s+watcher)\s+(.+?)\s+after\s+(\d+)\s+(?:message|msg)s?\s+(?:say\s+)?(.+)$",
            r"^(?:monitor|watch)\s+(.+?)\s+and\s+(?:say|tell|message)\s+(.+?)\s+every\s+(\d+)(?:\s+message|msg)?s?$",
            r"^(?:add\s+)?(?:watch|watcher)\s+(.+?),?\s+(\d+),?\s+(?:say\s+)?(.+)$",
            r"^(?:watch|monitor)\s+(.+?)\s+say\s+(.+)$",
        ],
        "remove_watcher": [
            r"^(?:remove|stop)\s+(?:watch|watcher)(?:\s+on)?\s+(.+)$",
            r"^(?:unwatch|stop\s+watching)\s+(.+)$",
            r"^(?:delete|clear)\s+(?:watcher\s+)?(.+)$",
        ],
        "list_watchers": [
            r"^(?:show|list|what\s+are|check|display)\s+(?:my\s+|all\s+)?watchers?$",
            r"^watchers?\s+(?:list|show)?$",
            r"^(?:get|show)\s+watchers?$",
        ],
        "mirror": [
            r"^mirror\s+(.+?)\s+(?:to|→|->|and)\s+(.+)$",
            r"^(?:copy|link|sync)\s+(.+?)\s+(?:to|and)\s+(.+)$",
            r"^(?:link|sync|connect)\s+(.+?)\s+(?:with|and)\s+(.+)$",
        ],
        "show_stats": [
            r"^(?:show|get|display|check)\s+(?:me\s+)?(?:the\s+)?(?:stats?|statistics?)(?:\s+(?:for|of)\s+(.+?))?(?:\s+(today|daily|this\s+week|weekly|this\s+month|monthly))?$",
            r"^stats?\s+(?:for\s+)?(.+)?(?:\s+(daily|weekly|monthly))?$",
            r"^(?:how\s+)?(?:much|many)\s+(?:did|has|have)\s+(.+?)\s+(?:chat|talk|message|send)(?:\s+(today|this\s+week))?$",
        ],
        "show_health": [
            r"^(?:health|status|check\s+status)$",
            r"^(?:how|what)\s+(?:is\s+)?(?:the\s+)?(?:bot\s+)?status$",
        ],
        "show_queue": [
            r"^(?:queue|jobs|pending)$",
            r"^(?:show|check)\s+(?:queue|jobs)$",
        ],
    }
    
    async def recognize(self, raw_input: str) -> Dict[str, Any]:
        """
        Recognize user intent from raw input.
        
        Returns:
            {
                "action": "send_dm",  # The action type
                "confidence": 0.95,    # Confidence score 0-1
                "matches": [           # Regex match groups
                    {"group": 1, "value": "@john"},
                    {"group": 2, "value": "hello"}
                ],
                "raw_input": "dm @john hello"
            }
        """
        text = raw_input.strip()
        
        for action, patterns in self.INTENT_PATTERNS.items():
            for pattern in patterns:
                match = re.match(pattern, text, re.IGNORECASE)
                if match:
                    # Calculate confidence based on pattern specificity
                    confidence = min(0.99, 0.70 + (len(pattern) * 0.01))
                    
                    return {
                        "action": action,
                        "confidence": confidence,
                        "matches": [
                            {"group": i + 1, "value": g}
                            for i, g in enumerate(match.groups())
                        ],
                        "raw_input": text,
                    }
        
        # If no pattern matches, return low confidence
        return {
            "action": None,
            "confidence": 0.0,
            "matches": [],
            "raw_input": text,
        }


class ArgumentExtractor:
    """Extract structured arguments from user input."""
    
    def __init__(self, bot: discord.Client):
        self.bot = bot
    
    async def extract_targets(
        self, 
        text: str, 
        guild: Optional[discord.Guild],
        context: Dict[str, Any] = None
    ) -> Tuple[List[int], List[str]]:
        """
        Extract user IDs and their display names.
        
        Returns:
            (user_ids: List[int], names: List[str], unresolved: List[str])
        
        Handles:
        - @mentions
        - usernames/nicknames
        - user IDs
        - pronouns from context (him, her, them)
        """
        context = context or {}
        resolved_ids: List[int] = []
        resolved_names: List[str] = []
        unresolved: List[str] = []
        
        # Extract all potential user references
        mention_ids = re.findall(r"<@!?(\d+)>", text)
        for user_id in mention_ids:
            resolved_ids.append(int(user_id))
            user = self.bot.get_user(int(user_id))
            if user:
                resolved_names.append(user.name)
        
        # Remove mentioned users from text to process remaining
        remaining_text = re.sub(r"<@!?(\d+)>", "", text)
        
        # Extract non-mention user references
        tokens = remaining_text.split()
        for token in tokens:
            token = token.strip("\"',")
            
            # Check if it's a pronoun from context
            if token.lower() in ("him", "her", "them"):
                if context.get("last_target"):
                    target_id = context["last_target"]
                    if target_id not in resolved_ids:
                        resolved_ids.append(target_id)
                        user = self.bot.get_user(target_id)
                        if user:
                            resolved_names.append(user.name)
                continue
            
            # Check if it's a user ID
            if token.isdigit():
                try:
                    user = self.bot.get_user(int(token))
                    if user and int(token) not in resolved_ids:
                        resolved_ids.append(int(token))
                        resolved_names.append(user.name)
                        continue
                except:
                    pass
            
            # Try to find by username in guild
            if guild:
                found = False
                for member in guild.members:
                    if (member.name.lower() == token.lower() or 
                        (member.display_name and member.display_name.lower() == token.lower())):
                        if member.id not in resolved_ids:
                            resolved_ids.append(member.id)
                            resolved_names.append(member.display_name or member.name)
                            found = True
                            break
                
                if not found:
                    unresolved.append(token)
            else:
                unresolved.append(token)
        
        return resolved_ids, resolved_names, unresolved
    
    async def extract_message(
        self, 
        text: str, 
        after_target: str
    ) -> Optional[str]:
        """
        Extract message text from input.
        
        Handles:
        - Quoted: "hello world"
        - Unquoted: hello world
        - None if empty
        """
        # Try quoted first
        quoted = re.search(r'["\'](.+?)["\']', after_target)
        if quoted:
            return quoted.group(1)
        
        # Try unquoted
        msg = after_target.strip()
        if msg and not msg.lower().startswith(('after', 'every', 'say')):
            return msg
        
        return None
    
    async def extract_number(
        self, 
        text: str, 
        field: str
    ) -> Optional[int]:
        """
        Extract numbers from text.
        
        field: "count", "delay", etc.
        """
        numbers = re.findall(r'\d+', text)
        if numbers:
            return int(numbers[0])
        return None


class ContextMemory:
    """Remember user context for intelligent inference."""
    
    def __init__(self):
        self.user_context: Dict[int, Dict[str, Any]] = {}
    
    async def record_action(
        self, 
        user_id: int, 
        action: str, 
        args: Dict[str, Any]
    ):
        """Record what user just did."""
        ctx = self.user_context.setdefault(user_id, {
            "last_action": None,
            "last_args": {},
            "last_target": None,
            "last_message": None,
            "history": [],
        })
        
        ctx["last_action"] = action
        ctx["last_args"] = args
        ctx["last_target"] = args.get("target_user_id") or args.get("targets", [None])[0]
        ctx["last_message"] = args.get("text") or args.get("message")
        
        # Keep history of last 20 actions
        ctx["history"].append({
            "action": action,
            "args": args,
            "timestamp": time.time()
        })
        ctx["history"] = ctx["history"][-20:]
    
    async def resolve_reference(
        self, 
        user_id: int, 
        reference: str
    ) -> Any:
        """Resolve pronouns and references."""
        ctx = self.user_context.get(user_id, {})
        
        reference_lower = reference.lower()
        
        if reference_lower in ("him", "her", "them", "he", "she"):
            return ctx.get("last_target")
        
        if reference_lower in ("again", "repeat"):
            return (ctx.get("last_action"), ctx.get("last_args"))
        
        if reference_lower in ("also", "and"):
            return ctx.get("last_action")
        
        return None
    
    def get_context(self, user_id: int) -> Dict[str, Any]:
        """Get user's current context."""
        return self.user_context.get(user_id, {})


class ClarificationHandler:
    """Handle ambiguous inputs by asking clarifying questions."""
    
    async def ask_which_user(
        self, 
        channel: discord.abc.Messageable, 
        candidates: List[Tuple[int, str]],
        action: str
    ) -> Optional[int]:
        """
        Ask user to pick from multiple candidates.
        
        Returns:
            user_id if picked, None if cancelled
        """
        if not candidates:
            return None
        
        if len(candidates) == 1:
            return candidates[0][0]
        
        # Build question
        options = "\n".join(
            f"{i+1}. @{name}"
            for i, (uid, name) in enumerate(candidates[:5])
        )
        
        msg = (
            f"Which user did you mean?\n"
            f"{options}\n\n"
            f"Reply with number, or type the name again"
        )
        
        await channel.send(msg)
        
        # Wait for response
        def check(m):
            return m.author.id == channel._user_id if hasattr(channel, '_user_id') else True
        
        try:
            response = await channel._bot.wait_for(
                "message",
                check=check,
                timeout=30
            )
        except asyncio.TimeoutError:
            await channel.send("Timed out. Try again.")
            return None
        
        # Parse response
        content = response.content.strip()
        if content.isdigit():
            idx = int(content) - 1
            if 0 <= idx < len(candidates):
                return candidates[idx][0]
        
        # Try to match by name
        for uid, name in candidates:
            if content.lower() in name.lower():
                return uid
        
        await channel.send("Couldn't understand. Try again with a clearer name.")
        return None
    
    async def ask_for_message(
        self, 
        channel: discord.abc.Messageable,
        target: str,
        bot: discord.Client
    ) -> Optional[str]:
        """Ask user for message content."""
        msg = f"What message should I send to {target}?"
        await channel.send(msg)
        
        def check(m):
            return m.author.id == channel._user_id if hasattr(channel, '_user_id') else True
        
        try:
            response = await bot.wait_for(
                "message",
                check=check,
                timeout=60
            )
            return response.content.strip()
        except asyncio.TimeoutError:
            await channel.send("Timed out. Try again.")
            return None
    
    async def ask_for_number(
        self, 
        channel: discord.abc.Messageable,
        question: str,
        bot: discord.Client,
        default: int = None
    ) -> Optional[int]:
        """Ask user for a number."""
        if default:
            question += f" (default: {default})"
        
        await channel.send(question)
        
        def check(m):
            return m.author.id == channel._user_id if hasattr(channel, '_user_id') else True
        
        try:
            response = await bot.wait_for(
                "message",
                check=check,
                timeout=30
            )
        except asyncio.TimeoutError:
            return default
        
        content = response.content.strip()
        if content.isdigit():
            return int(content)
        
        return default


class ConfirmationFormatter:
    """Format action results in natural language."""
    
    @staticmethod
    def format_dm_sent(targets: List[str], message: str) -> str:
        if len(targets) == 1:
            return f"✅ Sent DM to @{targets[0]}: '{message}'"
        else:
            names = ", ".join(f"@{t}" for t in targets)
            return f"✅ Sent DMs to {names}: '{message}'"
    
    @staticmethod
    def format_watcher_added(target: str, count: int, message: str) -> str:
        return f"✅ Watching @{target} - will say '{message}' after {count} messages"
    
    @staticmethod
    def format_watcher_removed(target: str) -> str:
        return f"✅ Stopped watching @{target}"
    
    @staticmethod
    def format_watchers_list(watchers: List[Dict]) -> str:
        if not watchers:
            return "No watchers configured."
        
        lines = ["**Active Watchers:**"]
        for w in watchers:
            lines.append(f"• @{w['user']}: Say '{w['message']}' after {w['count']} messages")
        
        return "\n".join(lines)
    
    @staticmethod
    def format_mirror_created(source: str, dest: str) -> str:
        return f"✅ Linked {source} ↔️ {dest} - messages will copy both ways"
    
    @staticmethod
    def format_stats(stats: Dict) -> str:
        """Format stats naturally."""
        lines = ["**Statistics:**"]
        for key, value in stats.items():
            lines.append(f"• {key}: {value}")
        return "\n".join(lines)


class IntelligentCommandProcessor:
    """
    Main processor: Raw input → Intent → Arguments → Execution
    """
    
    def __init__(self, bot: discord.Client):
        self.bot = bot
        self.recognizer = IntentRecognizer()
        self.extractor = ArgumentExtractor(bot)
        self.context = ContextMemory()
        self.clarifier = ClarificationHandler()
        self.formatter = ConfirmationFormatter()
    
    async def process(
        self,
        raw_input: str,
        user: discord.User,
        guild: Optional[discord.Guild],
        channel: discord.abc.Messageable,
        executor: Any  # The function that actually executes commands
    ) -> bool:
        """
        Process raw user input intelligently.
        
        Returns: True if processed, False if couldn't understand
        """
        
        # Store user ID in channel for clarification
        channel._user_id = user.id
        channel._bot = self.bot
        
        # Step 1: Recognize intent
        intent = await self.recognizer.recognize(raw_input)
        
        if intent["confidence"] < 0.6:
            await channel.send(
                f"ℹ️ I didn't understand that command. Try something like:\n"
                f"- `dm @user message`\n"
                f"- `watch @user after 5 say hello`\n"
                f"- `show stats daily`"
            )
            return False
        
        action = intent["action"]
        
        # Step 2: Route to appropriate handler
        if action == "send_dm":
            return await self._handle_send_dm(intent, user, guild, channel, executor)
        
        elif action == "add_watcher":
            return await self._handle_add_watcher(intent, user, guild, channel, executor)
        
        elif action == "remove_watcher":
            return await self._handle_remove_watcher(intent, user, guild, channel, executor)
        
        elif action == "list_watchers":
            return await self._handle_list_watchers(intent, user, guild, channel, executor)
        
        elif action == "mirror":
            return await self._handle_mirror(intent, user, guild, channel, executor)
        
        elif action == "show_stats":
            return await self._handle_show_stats(intent, user, guild, channel, executor)
        
        elif action == "show_health":
            return await self._handle_show_health(intent, user, guild, channel, executor)
        
        elif action == "show_queue":
            return await self._handle_show_queue(intent, user, guild, channel, executor)
        
        return False
    
    async def _handle_send_dm(self, intent, user, guild, channel, executor) -> bool:
        """Handle: dm @user message"""
        context = self.context.get_context(user.id)
        
        # Extract targets and message
        match_text = " ".join(str(m["value"]) for m in intent["matches"])
        targets, target_names, unresolved = await self.extractor.extract_targets(
            match_text, guild, context
        )
        
        # Ask if ambiguous
        if unresolved and not targets:
            await channel.send(f"ℹ️ Couldn't find user: {', '.join(unresolved)}")
            return False
        
        # Extract message
        if len(intent["matches"]) >= 2:
            message = intent["matches"][1]["value"]
        else:
            message = await self.clarifier.ask_for_message(
                channel,
                f"@{target_names[0]}" if target_names else "that user",
                self.bot
            )
            if not message:
                return False
        
        # Execute
        try:
            for target_id in targets:
                target_user = self.bot.get_user(target_id)
                if target_user:
                    await target_user.send(message)
            
            # Confirm
            confirmation = self.formatter.format_dm_sent(target_names, message)
            await channel.send(confirmation)
            
            # Record context
            await self.context.record_action(user.id, "send_dm", {
                "targets": targets,
                "message": message
            })
            
            return True
        except Exception as e:
            await channel.send(f"❌ Failed to send DM: {str(e)}")
            return False
    
    async def _handle_add_watcher(self, intent, user, guild, channel, executor) -> bool:
        """Handle: watch @user after 5 say message"""
        context = self.context.get_context(user.id)
        
        # Extract components
        matches = intent["matches"]
        
        # Get target user
        if matches:
            target_text = matches[0]["value"]
            targets, target_names, _ = await self.extractor.extract_targets(
                target_text, guild, context
            )
            
            if not targets:
                await channel.send(f"ℹ️ Couldn't find user: {target_text}")
                return False
            
            target_id = targets[0]
            target_name = target_names[0]
        else:
            await channel.send("ℹ️ Who should I watch?")
            return False
        
        # Get count
        count = None
        if len(matches) > 1:
            try:
                count = int(matches[1]["value"])
            except:
                pass
        
        if not count:
            count = await self.clarifier.ask_for_number(
                channel,
                "After how many messages?",
                self.bot,
                default=5
            )
        
        # Get message
        message = None
        if len(matches) > 2:
            message = matches[2]["value"]
        
        if not message:
            message = await self.clarifier.ask_for_message(
                channel,
                f"@{target_name}",
                self.bot
            )
            if not message:
                return False
        
        # Execute
        try:
            # Call executor's add_watcher
            result = await executor.add_watcher(target_id, count, message, user.id)
            
            confirmation = self.formatter.format_watcher_added(target_name, count, message)
            await channel.send(confirmation)
            
            await self.context.record_action(user.id, "add_watcher", {
                "target_user_id": target_id,
                "count": count,
                "message": message
            })
            
            return True
        except Exception as e:
            await channel.send(f"❌ Failed to add watcher: {str(e)}")
            return False
    
    async def _handle_remove_watcher(self, intent, user, guild, channel, executor) -> bool:
        """Handle: remove watcher @user"""
        context = self.context.get_context(user.id)
        
        matches = intent["matches"]
        if not matches:
            return False
        
        target_text = matches[0]["value"]
        targets, target_names, _ = await self.extractor.extract_targets(
            target_text, guild, context
        )
        
        if not targets:
            await channel.send(f"ℹ️ Couldn't find user: {target_text}")
            return False
        
        try:
            target_id = targets[0]
            target_name = target_names[0]
            
            await executor.remove_watcher(target_id, user.id)
            
            confirmation = self.formatter.format_watcher_removed(target_name)
            await channel.send(confirmation)
            
            return True
        except Exception as e:
            await channel.send(f"❌ Failed to remove watcher: {str(e)}")
            return False
    
    async def _handle_list_watchers(self, intent, user, guild, channel, executor) -> bool:
        """Handle: show watchers"""
        try:
            watchers = await executor.list_watchers()
            response = self.formatter.format_watchers_list(watchers)
            await channel.send(response)
            return True
        except Exception as e:
            await channel.send(f"❌ Failed to list watchers: {str(e)}")
            return False
    
    async def _handle_mirror(self, intent, user, guild, channel, executor) -> bool:
        """Handle: mirror #source to #dest"""
        matches = intent["matches"]
        if len(matches) < 2:
            await channel.send("ℹ️ Mirror which channels? `mirror #source to #dest`")
            return False
        
        source = matches[0]["value"]
        dest = matches[1]["value"]
        
        try:
            await executor.create_mirror(source, dest, user.id)
            confirmation = self.formatter.format_mirror_created(source, dest)
            await channel.send(confirmation)
            return True
        except Exception as e:
            await channel.send(f"❌ Failed to create mirror: {str(e)}")
            return False
    
    async def _handle_show_stats(self, intent, user, guild, channel, executor) -> bool:
        """Handle: show stats [user] [period]"""
        matches = intent["matches"]
        
        target_user = None
        period = "daily"
        
        # Extract user and period from matches
        if matches:
            for match in matches:
                value = match["value"]
                if value:
                    if value.lower() in ("daily", "weekly", "monthly"):
                        period = value.lower()
                    else:
                        # Assume it's a user
                        targets, target_names, _ = await self.extractor.extract_targets(
                            value, guild, self.context.get_context(user.id)
                        )
                        if targets:
                            target_user = targets[0]
        
        try:
            stats = await executor.show_stats(period, target_user)
            response = self.formatter.format_stats(stats)
            await channel.send(response)
            return True
        except Exception as e:
            await channel.send(f"❌ Failed to show stats: {str(e)}")
            return False
    
    async def _handle_show_health(self, intent, user, guild, channel, executor) -> bool:
        """Handle: health"""
        try:
            health = await executor.show_health()
            await channel.send(f"```\n{health}\n```")
            return True
        except Exception as e:
            await channel.send(f"❌ Failed to get health: {str(e)}")
            return False
    
    async def _handle_show_queue(self, intent, user, guild, channel, executor) -> bool:
        """Handle: queue"""
        try:
            queue_info = await executor.show_queue()
            await channel.send(f"```\n{queue_info}\n```")
            return True
        except Exception as e:
            await channel.send(f"❌ Failed to get queue: {str(e)}")
            return False

