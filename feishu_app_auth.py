#!/usr/bin/env python3
"""Feishu app auth helper for tenant access token."""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import requests


class FeishuAppAuth:
    """Fetch and cache tenant_access_token for Feishu Open API."""

    def __init__(self):
        self.app_id = os.getenv("FEISHU_APP_ID", "").strip()
        self.app_secret = os.getenv("FEISHU_APP_SECRET", "").strip()
        self.token_url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"

        default_token_file = "/tmp/feishu_app_token.json"
        self.token_file = Path(os.getenv("FEISHU_TOKEN_CACHE_FILE", default_token_file))

    def get_token(self) -> Optional[str]:
        """Return valid tenant_access_token, using cache when possible."""
        if not self.app_id or not self.app_secret:
            print("[AppAuth] FEISHU_APP_ID 或 FEISHU_APP_SECRET 未配置")
            return None

        cached = self._load_cached()
        if cached and self._is_valid(cached):
            print("[AppAuth] 使用缓存 token")
            return cached.get("tenant_access_token")

        return self._fetch_new_token()

    def _fetch_new_token(self) -> Optional[str]:
        payload = {"app_id": self.app_id, "app_secret": self.app_secret}
        headers = {"Content-Type": "application/json"}

        try:
            response = requests.post(self.token_url, headers=headers, json=payload, timeout=15)
            result = response.json()
            if result.get("code") != 0:
                print(f"[AppAuth] 获取 token 失败: {result}")
                return None

            token_info = {
                "tenant_access_token": result.get("tenant_access_token"),
                "expire": result.get("expire", 7200),
                "obtained_at": datetime.now().isoformat(),
            }
            self._save_cached(token_info)
            print("[AppAuth] 获取新 token 成功")
            return token_info["tenant_access_token"]
        except Exception as exc:
            print(f"[AppAuth] 请求异常: {exc}")
            return None

    def _load_cached(self) -> Optional[dict]:
        try:
            if self.token_file.exists():
                return json.loads(self.token_file.read_text(encoding="utf-8"))
        except Exception:
            return None
        return None

    def _save_cached(self, token_info: dict) -> None:
        try:
            self.token_file.write_text(json.dumps(token_info, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:
            print(f"[AppAuth] 缓存 token 失败: {exc}")

    @staticmethod
    def _is_valid(cached: dict) -> bool:
        try:
            obtained_at = datetime.fromisoformat(cached.get("obtained_at", ""))
            expire_seconds = int(cached.get("expire", 7200))
            safety_buffer = 300
            return datetime.now() < obtained_at + timedelta(seconds=max(0, expire_seconds - safety_buffer))
        except Exception:
            return False


def main() -> None:
    auth = FeishuAppAuth()
    token = auth.get_token()
    if token:
        print(f"✅ token ok: {token[:24]}...")
    else:
        print("❌ 获取 token 失败")


if __name__ == "__main__":
    main()
