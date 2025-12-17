from __future__ import annotations

import json
import os
import random
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests

from notifier import notify_serverchan, notify_wecom_text


@dataclass(frozen=True)
class UrlCheck:
    url: str
    ok: bool
    http_status: Optional[int]
    detail: str


@dataclass(frozen=True)
class AppCheckResult:
    name: str
    app_id: str
    store_url: str
    listed_at: Optional[datetime]
    check: UrlCheck

    @property
    def ok(self) -> bool:
        return self.check.ok

    def summary_line(self) -> str:
        ok_str = "✅ 正常" if self.ok else "❌ 异常"
        dur = ""
        if (not self.ok) and self.listed_at is not None:
            now = datetime.now(timezone.utc)
            delta = now - self.listed_at.astimezone(timezone.utc)
            dur = f"（{format_timedelta(delta.total_seconds())}）"
        return f"{ok_str} {self.name}{dur}"


def _env(name: str, default: str = "") -> str:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip()


def _ensure_parent_dir(path: str) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)


def load_state(state_file: str) -> Dict[str, Any]:
    if not state_file:
        return {}
    try:
        with open(state_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
        return {}
    except FileNotFoundError:
        return {}
    except Exception:
        return {}


def save_state(state_file: str, state: Dict[str, Any]) -> None:
    if not state_file:
        return
    _ensure_parent_dir(state_file)
    tmp = state_file + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, state_file)


def _now_iso(tz_name: str) -> str:
    if tz_name in ("Asia/Shanghai", "Asia/Chongqing", "Asia/Beijing", "Asia/Urumqi"):
        offset = 8 * 3600
    else:
        offset = 0
    dt = datetime.now(timezone.utc).astimezone(timezone(timedelta(seconds=offset)))
    if offset == 8 * 3600:
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    return dt.strftime("%Y-%m-%d %H:%M:%S %z")


def _sleep_jitter(base_seconds: float) -> None:
    time.sleep(base_seconds + random.random() * 0.25)


def load_apps(apps_file: str) -> List[Dict[str, Any]]:
    with open(apps_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError("apps.json 顶层必须是数组")

    normalized: List[Dict[str, Any]] = []
    for idx, raw in enumerate(data):
        if not isinstance(raw, dict):
            raise ValueError(f"apps[{idx}] 必须是对象")
        name = str(raw.get("name", "")).strip()
        app_id = str(raw.get("app_id", "")).strip()
        store_url = str(raw.get("store_url", "")).strip()
        listed_at_raw = raw.get("listed_at", None)

        if not name:
            raise ValueError(f"apps[{idx}].name 不能为空")
        if not app_id.isdigit():
            raise ValueError(f"apps[{idx}].app_id 必须是数字字符串，例如 6756509310")
        if not store_url:
            raise ValueError(f"apps[{idx}].store_url 不能为空")
        if not (store_url.startswith("https://") or store_url.startswith("http://")):
            raise ValueError(f"apps[{idx}].store_url 必须是 http(s) URL")

        listed_at = parse_listed_at(listed_at_raw)

        normalized.append(
            {
                "name": name,
                "app_id": app_id,
                "store_url": store_url,
                "listed_at": listed_at,
            }
        )

    return normalized


def parse_listed_at(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None

    tz_name = _env("TZ_NAME", "Asia/Shanghai")
    if tz_name in ("Asia/Shanghai", "Asia/Chongqing", "Asia/Beijing", "Asia/Urumqi"):
        tz = timezone(timedelta(hours=8))
    else:
        tz = timezone.utc

    fmts = [
        "%Y-%m-%d %H:%M:%S %z",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ]
    for fmt in fmts:
        try:
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=tz)
            return dt
        except Exception:
            continue
    raise ValueError(f"listed_at 格式不支持: {s!r}，建议用 'YYYY-MM-DD HH:MM:SS +0800' 或 'YYYY-MM-DD'")


def format_timedelta(total_seconds: float) -> str:
    seconds = int(total_seconds)
    if seconds < 0:
        seconds = 0
    days = seconds // 86400
    if days <= 0:
        return "不足1天"
    return f"{days}天"


def format_reason(http_status: Optional[int], detail: str) -> str:
    if http_status is None:
        d = (detail or "").strip()
        if d:
            return f"网络异常/超时（{d}）"
        return "网络异常/超时"

    if http_status == 404:
        return "404（强信号：可能下架/该区不可用）"
    if http_status == 410:
        return "410（强信号：资源已移除/下架）"
    if http_status == 403:
        return "403（可能触发风控/需要验证/地区限制）"
    if http_status == 429:
        return "429（请求过多：被限流）"
    if 500 <= http_status <= 599:
        return f"{http_status}（App Store 服务器异常/临时故障）"
    if 400 <= http_status <= 499:
        return f"{http_status}（客户端错误：页面不可用/跳转异常）"
    return f"{http_status}（页面不可访问）"


def store_page_probe(url: str, timeout_seconds: int = 10) -> Tuple[bool, Optional[int], str]:
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Safari/605.1.15",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=timeout_seconds, allow_redirects=True)
        http_status = resp.status_code
        if http_status != 200:
            if http_status in (404, 410):
                return False, http_status, f"页面 HTTP {http_status}（强信号：可能下架/该区不可用）"
            return False, http_status, f"页面 HTTP {http_status}（可能是临时网络/限流/风控）"
        return True, http_status, "页面可访问"
    except Exception as e:
        return False, None, f"页面探测异常: {e}"


def check_one_app(app: Dict[str, Any], retries: int, retry_backoff_seconds: float, timeout_seconds: int) -> AppCheckResult:
    name = app["name"]
    app_id = app["app_id"]
    store_url = app["store_url"]
    listed_at = app.get("listed_at", None)

    last: Optional[UrlCheck] = None
    for attempt in range(retries + 1):
        ok, http_status, detail = store_page_probe(store_url, timeout_seconds=timeout_seconds)
        last = UrlCheck(url=store_url, ok=ok, http_status=http_status, detail=detail)
        if ok:
            break
        if attempt < retries:
            _sleep_jitter(retry_backoff_seconds * (attempt + 1))

    return AppCheckResult(
        name=name,
        app_id=app_id,
        store_url=store_url,
        listed_at=listed_at,
        check=last if last is not None else UrlCheck(url=store_url, ok=False, http_status=None, detail="未知错误"),
    )


def format_report(results: List[AppCheckResult], tz_name: str) -> str:
    lines: List[str] = []
    total = len(results)
    bad = [r for r in results if not r.ok]
    ok_cnt = total - len(bad)
    lines.append(f"App Store 上架状态监控 - {_now_iso(tz_name)}")
    lines.append(f"汇总：总数 {total}，正常 {ok_cnt}，异常 {len(bad)}")
    lines.append("")
    for r in results:
        lines.append(r.summary_line())
        c = r.check
        status = "OK" if c.ok else "FAIL"
        hs = "" if c.http_status is None else f" http={c.http_status}"
        lines.append(f"- {status}{hs} {c.detail}")
        lines.append(f"- URL {r.store_url}")
        if r.listed_at is not None:
            lines.append(f"- 上架时间 listed_at: {r.listed_at.strftime('%Y-%m-%d %H:%M:%S %z')}")
        lines.append("")
    return "\n".join(lines).strip()


def format_bad_only_report(results: List[AppCheckResult], tz_name: str) -> str:
    bad = [r for r in results if not r.ok]
    lines: List[str] = []
    lines.append(f"App Store 上架状态监控 - {_now_iso(tz_name)}")
    lines.append(f"异常 {len(bad)} / 总数 {len(results)}")
    lines.append("")
    for r in bad:
        c = r.check
        dur = ""
        if r.listed_at is not None:
            now = datetime.now(timezone.utc)
            delta = now - r.listed_at.astimezone(timezone.utc)
            dur = f"（{format_timedelta(delta.total_seconds())}）"
        lines.append(f"❌ 异常 {r.name}{dur}")
        lines.append(f"- 地址：{r.store_url}")
        if r.listed_at is not None:
            lines.append(f"- 上架时间：{r.listed_at.strftime('%Y-%m-%d')}")
        lines.append(f"- 原因：{format_reason(c.http_status, c.detail)}")
        lines.append("")
    return "\n".join(lines).strip()


def _app_state_key(app_id: str, store_url: str) -> str:
    return f"{app_id}|{store_url}"


def format_new_bad_only_report(
    results: List[AppCheckResult],
    tz_name: str,
    prev_state: Dict[str, Any],
) -> Tuple[str, List[AppCheckResult], List[AppCheckResult]]:
    prev_apps = prev_state.get("apps", {})
    if not isinstance(prev_apps, dict):
        prev_apps = {}

    bad = [r for r in results if not r.ok]
    new_bad: List[AppCheckResult] = []
    for r in bad:
        key = _app_state_key(r.app_id, r.store_url)
        prev = prev_apps.get(key, {})
        prev_ok = True
        if isinstance(prev, dict):
            prev_ok = bool(prev.get("ok", True))
        if prev_ok:
            new_bad.append(r)

    lines: List[str] = []
    lines.append(f"App Store 上架状态监控 - {_now_iso(tz_name)}")
    lines.append(f"新增异常 {len(new_bad)} / 当前异常 {len(bad)} / 总数 {len(results)}")
    lines.append("")

    if not new_bad:
        lines.append("本次没有新增异常（异常项可能已在之前的运行中告警过）。")
        if bad:
            lines.append("")
            lines.append("当前仍异常的 App：")
            for r in bad:
                c = r.check
                lines.append(f"- {r.name}：{format_reason(c.http_status, c.detail)}")
        lines.append("")
        return "\n".join(lines).strip(), new_bad, bad

    for r in new_bad:
        c = r.check
        dur = ""
        if r.listed_at is not None:
            now = datetime.now(timezone.utc)
            delta = now - r.listed_at.astimezone(timezone.utc)
            dur = f"（{format_timedelta(delta.total_seconds())}）"
        lines.append(f"❌ 异常 {r.name}{dur}")
        lines.append(f"- 地址：{r.store_url}")
        if r.listed_at is not None:
            lines.append(f"- 上架时间：{r.listed_at.strftime('%Y-%m-%d')}")
        lines.append(f"- 原因：{format_reason(c.http_status, c.detail)}")
        lines.append("")

    return "\n".join(lines).strip(), new_bad, bad


def build_state(results: List[AppCheckResult]) -> Dict[str, Any]:
    apps: Dict[str, Any] = {}
    for r in results:
        key = _app_state_key(r.app_id, r.store_url)
        apps[key] = {
            "name": r.name,
            "app_id": r.app_id,
            "store_url": r.store_url,
            "ok": r.ok,
            "http_status": r.check.http_status,
            "detail": r.check.detail,
        }
    return {
        "updated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S %z"),
        "apps": apps,
    }


def notify(report: str) -> Tuple[bool, str]:
    channel = _env("NOTIFY_CHANNEL", "wecom").lower()
    if channel == "wecom":
        webhook = _env("WECOM_WEBHOOK_URL")
        res = notify_wecom_text(webhook_url=webhook, content=report)
        return res.ok, res.detail
    if channel == "serverchan":
        sendkey = _env("SERVERCHAN_SENDKEY")
        title = _env("SERVERCHAN_TITLE", "App Store 监控告警")
        res = notify_serverchan(sendkey=sendkey, title=title, desp=report)
        return res.ok, res.detail
    return False, f"未知 NOTIFY_CHANNEL: {channel}"


def main() -> int:
    apps_file = _env("APPS_FILE", "monitor/apps.json")
    retries = int(_env("RETRIES", "2"))
    retry_backoff_seconds = float(_env("RETRY_BACKOFF_SECONDS", "1.0"))
    timeout_seconds = int(_env("TIMEOUT_SECONDS", "10"))
    tz_name = _env("TZ_NAME", "Asia/Shanghai")
    alert_mode = _env("ALERT_MODE", "always").lower()  # always | transition
    state_file = _env("STATE_FILE", "")

    apps = load_apps(apps_file)
    results: List[AppCheckResult] = []
    for app in apps:
        results.append(
            check_one_app(
                app=app,
                retries=retries,
                retry_backoff_seconds=retry_backoff_seconds,
                timeout_seconds=timeout_seconds,
            )
        )

    report = format_report(results, tz_name=tz_name)
    bad = [r for r in results if not r.ok]

    if bad:
        if alert_mode == "transition":
            prev_state = load_state(state_file)
            send_report, new_bad, _all_bad = format_new_bad_only_report(results, tz_name=tz_name, prev_state=prev_state)
            if new_bad:
                ok, detail = notify(send_report)
                save_state(state_file, build_state(results))
                print(send_report)
                print("")
                print(f"notify: ok={ok} detail={detail}")
                return 0 if ok else 2
            ok, detail = True, "skip notify (no new bad)"
            print(send_report)
        else:
            bad_report = format_bad_only_report(results, tz_name=tz_name)
            ok, detail = notify(bad_report)
            print(bad_report)
        print("")
        print(f"notify: ok={ok} detail={detail}")
        save_state(state_file, build_state(results))
        return 0 if alert_mode == "transition" else 2

    print(report)
    save_state(state_file, build_state(results))
    return 0


if __name__ == "__main__":
    sys.exit(main())


