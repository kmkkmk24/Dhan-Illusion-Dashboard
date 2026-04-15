import json
import time
from typing import Any, Optional

import httpx

from backend.config import get_config


class TapetideMcpClient:
    def __init__(self):
        cfg = get_config().get("tapetide") or {}
        self.base_url = (cfg.get("base_url") or "https://mcp.tapetide.com").rstrip("/")
        self.refresh_token = cfg.get("token") or cfg.get("refresh_token")
        self.token_env = cfg.get("token_env") or "TAPETIDE_TOKEN"
        self.access_token: Optional[str] = None
        self.expires_at = 0.0
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    def _token_from_env(self) -> Optional[str]:
        import os

        return os.getenv(self.token_env)

    async def _refresh_access_token(self) -> str:
        token = self.refresh_token or self._token_from_env()
        if not token:
            raise RuntimeError("Tapetide token missing. Set TAPETIDE_TOKEN or tapetide.token in config.")

        client = await self._get_client()
        resp = await client.post(
            f"{self.base_url}/token",
            data={"grant_type": "refresh_token", "refresh_token": token},
        )
        resp.raise_for_status()
        data = resp.json()
        access = data.get("access_token")
        expires = data.get("expires_in") or 3600
        if not access:
            raise RuntimeError("Tapetide token refresh failed: missing access_token")

        self.access_token = access
        self.expires_at = time.time() + max(0, int(expires) - 300)
        return access

    async def _get_access_token(self) -> str:
        if not self.access_token or time.time() >= self.expires_at:
            return await self._refresh_access_token()
        return self.access_token

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        token = await self._get_access_token()
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }

        client = await self._get_client()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }

        resp = await client.post(f"{self.base_url}/mcp", headers=headers, content=json.dumps(payload))
        if resp.status_code == 401:
            token = await self._refresh_access_token()
            headers["Authorization"] = f"Bearer {token}"
            resp = await client.post(f"{self.base_url}/mcp", headers=headers, content=json.dumps(payload))
        resp.raise_for_status()

        raw_text = resp.text
        if "text/event-stream" in resp.headers.get("content-type", ""):
            raw_text = self._extract_json_from_sse(raw_text)

        outer = json.loads(raw_text)
        result = outer.get("result", {})
        if result.get("isError"):
            raise RuntimeError(result.get("content", [{}])[0].get("text", "Tapetide MCP error"))

        content = result.get("content") or []
        if not content:
            return {}

        text_blob = content[0].get("text", "")
        parsed = self._coerce_json(text_blob)
        if parsed is not None:
            return parsed
        return {"raw": text_blob}

    @staticmethod
    def _coerce_json(payload: Any) -> Optional[dict[str, Any]]:
        if isinstance(payload, dict):
            return payload
        if not isinstance(payload, str):
            return None
        text = payload.strip()
        if text.startswith("```"):
            text = text.strip("`").strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        # Fallback: extract first JSON object in the string
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                return None
        return None

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    @staticmethod
    def _extract_json_from_sse(sse: str) -> str:
        last_data = ""
        for line in sse.split("\n"):
            if line.startswith("data: "):
                last_data = line[6:]
            elif line.startswith("data:"):
                last_data = line[5:]
        return last_data or sse
