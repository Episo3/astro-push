"""配置加载：config.json + .env（密钥）。

优先级：环境变量 > .env 文件 > config.json 默认值。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

DEFAULT_CONFIG: dict[str, Any] = {
    "location": {
        "name": "Madison, WI",
        "lat": 43.0731,
        "lon": -89.4012,
        "tz": "America/Chicago",
        "bortle": 5,
        "elevation_m": 266,
    },
    "schedule": {"hour": 15, "minute": 30},
    "channels": ["file"],
    "webhook": {"type": "generic"},
    "thresholds": {
        "min_dark_hours": 0.5,
        "min_target_alt": 20.0,
        "moon_dark_illum": 0.35,
        "aurora_kp_min": 6.0,
        "grade_a": 85.0,
        "grade_b": 70.0,
        "grade_c": 50.0,
        "max_report_chars": 3800,
    },
    "dark_sites": [],
}


def load_dotenv(path: Path | None = None) -> dict[str, str]:
    """极简 .env 解析（不依赖 python-dotenv）。"""
    path = path or ROOT / ".env"
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:]
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
            v = v[1:-1]
        out[k.strip()] = v
    return out


@dataclass
class Config:
    raw: dict[str, Any]
    env: dict[str, str] = field(default_factory=dict)
    root: Path = ROOT

    # ---------------- 地点 ----------------
    @property
    def loc(self) -> dict[str, Any]:
        return self.raw["location"]

    @property
    def lat(self) -> float:
        return float(self.loc["lat"])

    @property
    def lon(self) -> float:
        return float(self.loc["lon"])

    @property
    def tz_name(self) -> str:
        return self.loc.get("tz", "UTC")

    @property
    def site_name(self) -> str:
        return self.loc.get("name", f"{self.lat},{self.lon}")

    def get(self, key: str, default: Any = None) -> Any:
        return self.raw.get(key, default)

    def threshold(self, key: str) -> float:
        return float(self.raw["thresholds"][key])

    # ---------------- 渠道 ----------------
    @property
    def channels(self) -> list[str]:
        return list(self.raw.get("channels", ["file"]))

    def env_val(self, key: str, default: str = "") -> str:
        return os.environ.get(key) or self.env.get(key) or default

    @property
    def webhook_type(self) -> str:
        return str(self.raw.get("webhook", {}).get("type", "generic")).lower()

    def secrets_present(self) -> dict[str, bool]:
        return {
            "telegram": bool(
                self.env_val("TELEGRAM_BOT_TOKEN") and self.env_val("TELEGRAM_CHAT_ID")
            ),
            "webhook": bool(
                self.env_val("FEISHU_WEBHOOK")
                or self.env_val("WECOM_WEBHOOK")
                or self.env_val("SERVERCHAN_KEY")
                or self.env_val("WEBHOOK_URL")
            ),
            "bark": bool(self.env_val("BARK_URL")),
        }


def load_config(path: Path | None = None) -> Config:
    path = path or ROOT / "config.json"
    raw = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
    if path.exists():
        user = json.loads(path.read_text(encoding="utf-8"))
        for k, v in user.items():
            if isinstance(v, dict) and isinstance(raw.get(k), dict):
                raw[k].update(v)
            else:
                raw[k] = v
    env = load_dotenv()
    # 环境变量可以覆盖地点
    if os.environ.get("SITE_LAT"):
        raw["location"]["lat"] = float(os.environ["SITE_LAT"])
    if os.environ.get("SITE_LON"):
        raw["location"]["lon"] = float(os.environ["SITE_LON"])
    if os.environ.get("SITE_NAME"):
        raw["location"]["name"] = os.environ["SITE_NAME"]
    if os.environ.get("SITE_TZ"):
        raw["location"]["tz"] = os.environ["SITE_TZ"]
    # CI / 服务器上可整体覆盖推送通道（避免提交带密钥的配置）
    if os.environ.get("PUSH_CHANNELS"):
        raw["channels"] = [c.strip() for c in os.environ["PUSH_CHANNELS"].split(",") if c.strip()]
    if os.environ.get("PUSH_WEBHOOK_TYPE"):
        raw.setdefault("webhook", {})["type"] = os.environ["PUSH_WEBHOOK_TYPE"]
    return Config(raw=raw, env=env)
