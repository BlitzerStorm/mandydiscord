import time
from typing import Any, Dict, Optional


class CooldownStore:
    def __init__(self, store: Any, root_key: str = "mandy", field: str = "mention_dm_cooldowns"):
        self.store = store
        self.root_key = root_key
        self.field = field

    def _bucket(self) -> Dict[str, Any]:
        root = self.store.data.setdefault(self.root_key, {})
        return root.setdefault(self.field, {})

    async def should_notify(self, user_id: int, cooldown_seconds: int, now: Optional[float] = None) -> bool:
        if user_id <= 0:
            return False
        now_ts = float(now if now is not None else time.time())
        bucket = self._bucket()
        key = str(int(user_id))
        until = float(bucket.get(key, 0))
        if now_ts < until:
            return False
        bucket[key] = now_ts + float(cooldown_seconds)
        if hasattr(self.store, "mark_dirty"):
            await self.store.mark_dirty()
        return True
