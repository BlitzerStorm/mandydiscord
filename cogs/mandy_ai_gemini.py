from __future__ import annotations

import asyncio
import re
from typing import Any, Optional, Tuple

try:
    from google import genai
    from google.genai import types as genai_types
except Exception:
    genai = None
    genai_types = None

class GeminiRateLimitError(Exception):
    def __init__(self, message: str, retry_after: Optional[float] = None):
        super().__init__(message)
        self.retry_after = retry_after

class GeminiClient:
    def __init__(self, api_key: Optional[str]):
        self.api_key = api_key or ""
        self.available = bool(self.api_key and genai is not None)
        self._client = genai.Client(api_key=self.api_key) if self.available else None

    def _build_contents(
        self,
        system_prompt: str,
        user_prompt: str,
        audio_bytes: Optional[bytes] = None,
        audio_mime: Optional[str] = None,
    ):
        prompt = (system_prompt or "").strip() + "\n\nUSER:\n" + (user_prompt or "").strip()
        if audio_bytes and genai_types:
            part = genai_types.Part.from_bytes(data=audio_bytes, mime_type=audio_mime or "audio/wav")
            return [part, prompt]
        return prompt

    def _extract_text(self, resp: Any) -> str:
        if resp is None:
            return ""
        text = getattr(resp, "text", None)
        if isinstance(text, str) and text.strip():
            return text
        try:
            cands = resp.candidates or []
            if not cands:
                return ""
            parts = cands[0].content.parts or []
            out = []
            for part in parts:
                t = getattr(part, "text", None)
                if isinstance(t, str):
                    out.append(t)
            return "".join(out).strip()
        except Exception:
            return ""

    def _is_rate_limit_error(self, exc: Exception) -> Tuple[bool, Optional[float]]:
        msg = str(exc).lower()
        is_rate = "429" in msg or "rate limit" in msg or "quota" in msg or "resource exhausted" in msg
        retry_after = self._extract_retry_after(exc, msg)
        return is_rate, retry_after

    def _extract_retry_after(self, exc: Exception, msg: str) -> Optional[float]:
        retry_after = getattr(exc, "retry_after", None)
        if isinstance(retry_after, (int, float)) and retry_after > 0:
            return float(retry_after)
        response = getattr(exc, "response", None)
        headers = getattr(response, "headers", None) if response else None
        if headers:
            raw = headers.get("Retry-After") or headers.get("retry-after")
            try:
                return float(raw)
            except Exception:
                pass
        match = re.search(r"retry-?after[:=]\s*(\d+)", msg)
        if match:
            try:
                return float(match.group(1))
            except Exception:
                return None
        return None

    def _generate_sync(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str,
        response_format: Optional[str],
        audio_bytes: Optional[bytes],
        audio_mime: Optional[str],
    ):
        contents = self._build_contents(system_prompt, user_prompt, audio_bytes, audio_mime)
        if genai_types:
            mime = "application/json" if response_format == "json" else "text/plain"
            config = genai_types.GenerateContentConfig(response_mime_type=mime, temperature=0.2)
            return self._client.models.generate_content(model=model, contents=contents, config=config)
        return self._client.models.generate_content(model=model, contents=contents)

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str,
        response_format: Optional[str] = None,
        audio_bytes: Optional[bytes] = None,
        audio_mime: Optional[str] = None,
        timeout: float = 60.0,
        retries: int = 2,
    ) -> str:
        if not self.available:
            raise RuntimeError("Gemini SDK not available or API key missing")
        loop = asyncio.get_running_loop()
        last_exc: Optional[Exception] = None
        for attempt in range(retries + 1):
            try:
                resp = await asyncio.wait_for(
                    loop.run_in_executor(
                        None,
                        self._generate_sync,
                        system_prompt,
                        user_prompt,
                        model,
                        response_format,
                        audio_bytes,
                        audio_mime,
                    ),
                    timeout=timeout,
                )
                text = self._extract_text(resp)
                if not text:
                    raise RuntimeError("Empty response from Gemini")
                return text
            except Exception as exc:
                is_rate, retry_after = self._is_rate_limit_error(exc)
                if is_rate:
                    raise GeminiRateLimitError(str(exc), retry_after=retry_after)
                last_exc = exc
                if attempt < retries:
                    await asyncio.sleep(0.4 * (attempt + 1))
        raise last_exc or RuntimeError("Gemini request failed")

