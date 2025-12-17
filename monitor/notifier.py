from __future__ import annotations

from dataclasses import dataclass

import requests


@dataclass(frozen=True)
class NotifyResult:
    ok: bool
    detail: str


def notify_wecom_text(webhook_url: str, content: str, timeout_seconds: int = 10) -> NotifyResult:
    if not webhook_url:
        return NotifyResult(ok=False, detail="WECOM_WEBHOOK_URL 为空")

    try:
        resp = requests.post(
            webhook_url,
            json={"msgtype": "text", "text": {"content": content}},
            timeout=timeout_seconds,
        )
        if resp.status_code != 200:
            return NotifyResult(ok=False, detail=f"企业微信返回状态码异常: {resp.status_code}, body={resp.text[:2000]}")
        try:
            data = resp.json()
        except Exception:
            data = {}
        if isinstance(data, dict) and data.get("errcode", 0) != 0:
            return NotifyResult(ok=False, detail=f"企业微信返回错误: {data}")
        return NotifyResult(ok=True, detail="企业微信发送成功")
    except Exception as e:
        return NotifyResult(ok=False, detail=f"企业微信发送失败: {e}")


def notify_serverchan(sendkey: str, title: str, desp: str, timeout_seconds: int = 10) -> NotifyResult:
    if not sendkey:
        return NotifyResult(ok=False, detail="SERVERCHAN_SENDKEY 为空")

    url = f"https://sctapi.ftqq.com/{sendkey}.send"
    try:
        resp = requests.post(url, data={"title": title, "desp": desp}, timeout=timeout_seconds)
        if resp.status_code != 200:
            return NotifyResult(ok=False, detail=f"Server酱返回状态码异常: {resp.status_code}, body={resp.text[:2000]}")
        try:
            data = resp.json()
        except Exception:
            data = {}
        if isinstance(data, dict) and data.get("code", 0) != 0:
            return NotifyResult(ok=False, detail=f"Server酱返回错误: {data}")
        return NotifyResult(ok=True, detail="Server酱发送成功")
    except Exception as e:
        return NotifyResult(ok=False, detail=f"Server酱发送失败: {e}")


