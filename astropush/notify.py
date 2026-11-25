"""推送通道：Telegram / 飞书 / 企业微信 / Server酱 / Bark / 通用 webhook / 本地文件。

所有通道都遵循「失败不影响其他通道」的原则，返回逐通道结果。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .config import Config

UA = "astro-push/1.0"


@dataclass
class SendResult:
    channel: str
    ok: bool
    detail: str


def _post_json(url: str, payload: dict, timeout: float = 20.0) -> tuple[int, str]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json; charset=utf-8", "User-Agent": UA},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def _post_form(url: str, fields: dict[str, str], timeout: float = 20.0) -> tuple[int, str]:
    data = urllib.parse.urlencode(fields).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": UA},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


# --------------------------------------------------------------------------- #
def send_telegram(cfg: Config, text_html: str, base: str = "https://api.telegram.org") -> SendResult:
    token = cfg.env_val("TELEGRAM_BOT_TOKEN")
    chat_id = cfg.env_val("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return SendResult("telegram", False, "缺少 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID")

    url = f"{base}/bot{token}/sendMessage"
    for attempt in range(3):
        status, body = _post_json(url, {
            "chat_id": chat_id,
            "text": text_html,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        })
        if status == 200:
            try:
                if json.loads(body).get("ok") is True:
                    return SendResult("telegram", True, "已发送")
            except Exception:
                pass
        if status == 429:  # 限流
            try:
                wait = int(json.loads(body).get("parameters", {}).get("retry_after", 3))
            except Exception:
                wait = 3
            time.sleep(min(wait, 10))
            continue
        return SendResult("telegram", False, f"HTTP {status}: {body[:160]}")
    return SendResult("telegram", False, "多次限流，放弃")


def serverchan_url(key: str) -> str:
    """兼容两代 Server酱：
      - Turbo（SCT 开头）      → https://sctapi.ftqq.com/<key>.send
      - Server酱3（sctp{uid}t…）→ https://{uid}.push.ft07.com/send/<key>.send
      - 直接给完整 URL          → 原样使用
    """
    key = key.strip()
    if key.startswith("http://") or key.startswith("https://"):
        return key
    import re
    m = re.match(r"^sctp(\d+)t(.+)$", key)
    if m:
        return f"https://{m.group(1)}.push.ft07.com/send/{key}.send"
    return f"https://sctapi.ftqq.com/{key}.send"


def send_webhook(cfg: Config, text: str, title: str, html_text: str = "") -> SendResult:
    kind = cfg.webhook_type
    if kind == "feishu":
        url = cfg.env_val("FEISHU_WEBHOOK")
        if not url:
            return SendResult("webhook", False, "缺少 FEISHU_WEBHOOK")
        status, body = _post_json(url, {
            "msg_type": "text",
            "content": {"text": text},
        })
        return _judge("飞书", status, body)
    if kind in ("wecom", "wecom_bot", "qywx"):
        url = cfg.env_val("WECOM_WEBHOOK")
        if not url:
            return SendResult("webhook", False, "缺少 WECOM_WEBHOOK")
        status, body = _post_json(url, {
            "msgtype": "markdown",
            "markdown": {"content": _limit_bytes(text, 3900)},
        })
        return _judge("企业微信", status, body)
    if kind == "serverchan":
        key = cfg.env_val("SERVERCHAN_KEY")
        if not key:
            return SendResult("webhook", False, "缺少 SERVERCHAN_KEY")
        status, body = _post_form(serverchan_url(key), {
            "title": title,
            "desp": text,
        })
        ok = False
        try:
            j = json.loads(body)
            ok = j.get("code") == 0 and status == 200
        except Exception:
            ok = status == 200
        return SendResult("Server酱", ok, f"HTTP {status} {body[:160]}")
    if kind == "bark":
        url = cfg.env_val("BARK_URL")
        if not url:
            return SendResult("webhook", False, "缺少 BARK_URL")
        base = url.rstrip("/")
        status, body = _post_json(base + "/push", {
            "title": title, "body": text, "group": "astro", "icon": "",
        })
        return _judge("Bark", status, body)

    # generic
    url = cfg.env_val("WEBHOOK_URL")
    if not url:
        return SendResult("webhook", False, "缺少 WEBHOOK_URL")
    status, body = _post_json(url, {
        "title": title, "text": text, "html": html_text, "kind": "astro-nightly",
    })
    return _judge("通用 webhook", status, body)


def _judge(name: str, status: int, body: str) -> SendResult:
    ok = 200 <= status < 300
    try:
        j = json.loads(body)
        if isinstance(j, dict):
            if "code" in j:
                ok = ok and j["code"] in (0, 200, "0")
            if "errcode" in j:
                ok = ok and j["errcode"] == 0
            if "StatusCode" in j:
                ok = ok and j["StatusCode"] == 0
    except Exception:
        pass
    return SendResult(name, ok, ("成功" if ok else f"HTTP {status}") + f" {body[:100]}")


def _limit_bytes(text: str, limit: int) -> str:
    raw = text.encode("utf-8")
    if len(raw) <= limit:
        return text
    return raw[: limit - 20].decode("utf-8", "ignore") + "\n…已截断"


def save_archive(root: Path, day_key: str, text: str) -> Path:
    outdir = root / "reports"
    outdir.mkdir(parents=True, exist_ok=True)
    path = outdir / f"{day_key}.md"
    path.write_text(text, encoding="utf-8")
    return path


def send_all(cfg: Config, text: str, text_html: str, title: str,
             dry_run: bool = False) -> list[SendResult]:
    results: list[SendResult] = []
    for ch in cfg.channels:
        if ch == "file":
            continue
        if dry_run:
            results.append(SendResult(ch, True, "dry-run 跳过实际发送"))
            continue
        try:
            if ch == "telegram":
                results.append(send_telegram(cfg, text_html))
            elif ch in ("webhook", "feishu", "wecom", "serverchan", "bark"):
                results.append(send_webhook(cfg, text, title, text_html))
            else:
                results.append(SendResult(ch, False, "未知通道"))
        except Exception as exc:  # 单通道异常不拖累其他
            results.append(SendResult(ch, False, f"异常: {exc}"))
    return results
