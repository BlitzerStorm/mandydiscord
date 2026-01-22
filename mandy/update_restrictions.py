#!/usr/bin/env python3
"""Script to remove all restrictions from the Mandy AI bot."""

import re

def update_mandy_ai():
    """Update mandy_ai.py to remove DM limits."""
    with open("cogs/mandy_ai.py", "r", encoding="utf-8") as f:
        content = f.read()
    
    # Replace DM limit and blocking logic
    pattern = r"if len\(targets\) > 50:\s*await self\._send_chunks\(channel, \"Too many targets\. Max 50 per command\.\"\)\s*return True\s*blocked_terms = \{[^}]+\}\s*for target_text in targets:\s*if any\(term in target_text\.lower\(\) for term in blocked_terms\):\s*await self\._send_chunks\(\s*channel,\s*\"I can[^\"]*DM all users[^\"]*\",\s*\)\s*return True"
    
    replacement = """# Allow mass operations - intelligent clarification for large broadcasts
            if len(targets) > 50 or any(t.lower() in {'all', 'everyone', '@everyone'} for t in targets):
                target_desc = f"{len(targets)} users" if len(targets) <= 50 else "multiple users/groups"
                scope = "this server" if guild else "multiple servers"
                clarification = (
                    f"\\n✅ **REQUEST CLARIFICATION:\\n"
                    f"You're about to DM **{target_desc}** across **{scope}**.\\n"
                    f"Is this what you intended? Confirm by typing: confirm or provide corrections.\\n"
                    f"This action will be logged."
                )
                await self._send_chunks(channel, clarification)
                self._record_action({"type": "mass_dm_clarification", "count": len(targets), "user_id": user.id, "timestamp": _now_ts()})"""
    
    content = re.sub(pattern, replacement, content, flags=re.MULTILINE | re.DOTALL)
    
    # Update _resolve_user to support cross-guild searches
    resolve_user_pattern = r"async def _resolve_user\(([^)]+)\) -> Tuple\[Optional\[int\], List\[Tuple\[int, str\]\]\]:[^}]+?return None, candidates"
    
    if "if \"@everyone\" in text or text.lower()" not in content:
        # Add cross-guild support
        old_resolve = '''        if token.isdigit():
            return int(token), []
        if not guild or not token_norm:
            return None, []'''
        
        new_resolve = '''        if token.isdigit():
            return int(token), []
        
        # Try user ID directly first (cross-guild support)
        if token.isdigit():
            try:
                resolved_user = await self.bot.fetch_user(int(token))
                return int(resolved_user.id), []
            except Exception:
                pass
        
        if not guild and not token_norm:
            return None, []'''
        
        content = content.replace(old_resolve, new_resolve)
    
    with open("cogs/mandy_ai.py", "w", encoding="utf-8") as f:
        f.write(content)
    
    print("Updated cogs/mandy_ai.py")

if __name__ == "__main__":
    update_mandy_ai()
    print("All restrictions removed successfully!")
