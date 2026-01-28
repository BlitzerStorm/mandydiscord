from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from typing import Any, Optional, Tuple


class AgentRouterRateLimitError(Exception):
    def __init__(self, message: str, retry_after: Optional[float] = None):
        super().__init__(message)
        self.retry_after = retry_after


class AgentRouterClient:
    def __init__(self, api_key: Optional[str], base_url: Optional[str] = None):
        self.api_key = api_key or ""
        self.base_url = (base_url or "").rstrip("/") or "https://agentrouter.org/v1"
        self.available = bool(self.api_key)

    def _extract_text(self, payload: Any) -> str:
        try:
            choices = payload.get("choices") or []
            if not choices:
                return ""
            message = choices[0].get("message") or {}
            text = message.get("content") or ""
            return str(text).strip()
        except Exception:
            return ""

    def _extract_retry_after(self, headers: Any, body_text: str) -> Optional[float]:
        if headers:
            retry_val = headers.get("Retry-After") or headers.get("retry-after")
            if retry_val is not None:
                try:
                    return float(retry_val)
                except Exception:
                    pass
        if body_text:
            for key in ("retry_after", "retry-after"):
                if key in body_text.lower():
                    digits = "".join(ch for ch in body_text if ch.isdigit())
                    if digits:
                        try:
                            return float(digits)
                        except Exception:
                            pass
        return None

    def _post_sync(self, url: str, payload: dict, timeout: float) -> dict:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Authorization", f"Bearer {self.api_key}")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}

    def _build_payload(self, system_prompt: str, user_prompt: str, model: str, response_format: Optional[str]) -> dict:
        messages = [
            {"role": "system", "content": system_prompt or ""},
            {"role": "user", "content": user_prompt or ""},
        ]
        payload = {"model": model, "messages": messages, "temperature": 0.2}
        if response_format == "json":
            payload["response_format"] = {"type": "json_object"}
        return payload

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str,
        response_format: Optional[str] = None,
        timeout: float = 60.0,
        retries: int = 1,
    ) -> str:
        if not self.available:
            raise RuntimeError("AgentRouter API key missing")
        url = f"{self.base_url.rstrip('/')}/chat/completions"
        payload = self._build_payload(system_prompt, user_prompt, model, response_format)
        loop = asyncio.get_running_loop()
        last_exc: Optional[Exception] = None
        for attempt in range(retries + 1):
            try:
                resp = await asyncio.wait_for(
                    loop.run_in_executor(None, self._post_sync, url, payload, timeout),
                    timeout=timeout,
                )
                text = self._extract_text(resp)
                if not text:
                    raise RuntimeError("Empty response from AgentRouter")
                return text
            except urllib.error.HTTPError as exc:
                body = ""
                try:
                    body = exc.read().decode("utf-8")
                except Exception:
                    body = ""
                retry_after = self._extract_retry_after(getattr(exc, "headers", None), body)
                if exc.code == 429:
                    raise AgentRouterRateLimitError(body or "rate limited", retry_after=retry_after)
                last_exc = exc
            except Exception as exc:
                last_exc = exc
                if attempt < retries:
                    await asyncio.sleep(0.5 * (attempt + 1))
        raise last_exc or RuntimeError("AgentRouter request failed")
