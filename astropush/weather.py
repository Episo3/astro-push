"""天气数据：Open-Meteo（云量分层/降水/能见度/温湿风）+ 7Timer（seeing/透明度）。

Open-Meteo 免费、无需 key、有逐小时与 7 天预报；7Timer 的 astro 产品是少数
公开给「视宁度(seeing)/透明度(transparency)」的接口，只覆盖 3 天，用来做
「今晚适合多大焦比」的判断。
"""

from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

UA = "astro-push/1.0 (personal night-sky planner)"

OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
SEVENTIMER = "https://www.7timer.info/bin/astro.php"


def _get(url: str, params: dict[str, Any] | None = None,
         timeout: float = 25.0, retries: int = 3) -> bytes:
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as exc:
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"请求失败 {url[:120]}: {last}")


def get_json(url: str, params: dict[str, Any] | None = None,
             timeout: float = 25.0, retries: int = 3) -> Any:
    return json.loads(_get(url, params, timeout, retries).decode("utf-8"))


# --------------------------------------------------------------------------- #
# Open-Meteo
# --------------------------------------------------------------------------- #
@dataclass
class WeatherHour:
    time: datetime                # 带时区
    cloud_total: float
    cloud_low: float
    cloud_mid: float
    cloud_high: float
    precip_prob: float
    temp_c: float
    dewpoint_c: float
    rh: float
    visibility_m: float
    wind_kmh: float
    wind_gust_kmh: float
    pressure: float = 1013.0

    @property
    def eff_cloud(self) -> float:
        """对天文观测真正有害的云量：低云最致命，中云次之，薄高云杀伤力较小。"""
        return min(
            100.0,
            1.00 * self.cloud_low + 0.75 * self.cloud_mid + 0.35 * self.cloud_high,
        )

    @property
    def dew_spread(self) -> float:
        return self.temp_c - self.dewpoint_c

    @property
    def fog_risk(self) -> bool:
        return self.visibility_m < 5000 or (self.rh > 95 and self.dew_spread < 1.0)


@dataclass
class Weather:
    hours: list[WeatherHour] = field(default_factory=list)
    daily_sunrise: list[datetime] = field(default_factory=list)
    daily_sunset: list[datetime] = field(default_factory=list)
    source_error: str | None = None

    def at(self, when: datetime) -> WeatherHour | None:
        """取最近整点的天气（Open-Meteo 是逐小时，线性插值意义不大）。"""
        if not self.hours:
            return None
        target = when.astimezone(self.hours[0].time.tzinfo)
        best = min(self.hours, key=lambda h: abs((h.time - target).total_seconds()))
        if abs((best.time - target).total_seconds()) > 5400:
            return None
        return best

    def hours_between(self, start: datetime, end: datetime) -> list[WeatherHour]:
        return [
            h for h in self.hours
            if start.astimezone(h.time.tzinfo) <= h.time <= end.astimezone(h.time.tzinfo)
        ]


def fetch_open_meteo(lat: float, lon: float, tz: str, days: int = 7) -> Weather:
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": (
            "temperature_2m,relative_humidity_2m,dew_point_2m,precipitation_probability,"
            "cloud_cover,cloud_cover_low,cloud_cover_mid,cloud_cover_high,visibility,"
            "wind_speed_10m,wind_gusts_10m,surface_pressure"
        ),
        "daily": "sunrise,sunset",
        "timezone": tz,
        "forecast_days": days,
        "wind_speed_unit": "kmh",
    }
    try:
        data = get_json(OPEN_METEO, params)
    except Exception as exc:  # 网络问题不该让整份报告挂掉
        return Weather(source_error=f"Open-Meteo: {exc}")

    h = data.get("hourly", {})
    times = h.get("time", [])
    from zoneinfo import ZoneInfo  # noqa: PLC0415

    tzinfo = ZoneInfo(tz)
    hours: list[WeatherHour] = []
    for i, t in enumerate(times):
        dt = datetime.fromisoformat(t).replace(tzinfo=tzinfo)

        def val(key: str, default: float = 0.0) -> float:
            arr = h.get(key) or []
            v = arr[i] if i < len(arr) else None
            return float(default if v is None else v)

        hours.append(
            WeatherHour(
                time=dt,
                cloud_total=val("cloud_cover"),
                cloud_low=val("cloud_cover_low"),
                cloud_mid=val("cloud_cover_mid"),
                cloud_high=val("cloud_cover_high"),
                precip_prob=val("precipitation_probability"),
                temp_c=val("temperature_2m"),
                dewpoint_c=val("dew_point_2m"),
                rh=val("relative_humidity_2m"),
                visibility_m=val("visibility", 20000),
                wind_kmh=val("wind_speed_10m"),
                wind_gust_kmh=val("wind_gusts_10m"),
                pressure=val("surface_pressure", 1013),
            )
        )

    daily = data.get("daily", {})
    sunrise = [datetime.fromisoformat(x).replace(tzinfo=tzinfo)
               for x in daily.get("sunrise", [])]
    sunset = [datetime.fromisoformat(x).replace(tzinfo=tzinfo)
              for x in daily.get("sunset", [])]
    return Weather(hours=hours, daily_sunrise=sunrise, daily_sunset=sunset)


# --------------------------------------------------------------------------- #
# 7Timer astro
# --------------------------------------------------------------------------- #
@dataclass
class SeeingHour:
    time: datetime
    cloudcover: int      # 1(晴) - 9(阴)
    seeing: int          # 1(<0.5") - 8(>2.5")
    transparency: int    # 1(极差) - 8(极好)
    rh: int
    temp: int


@dataclass
class Seeing:
    hours: list[SeeingHour] = field(default_factory=list)
    source_error: str | None = None

    def at(self, when: datetime) -> SeeingHour | None:
        if not self.hours:
            return None
        target = when.astimezone(timezone.utc)
        best = min(self.hours, key=lambda h: abs((h.time - target).total_seconds()))
        if abs((best.time - target).total_seconds()) > 5400:
            return None
        return best


def fetch_7timer(lat: float, lon: float, tz_offset_hours: float) -> Seeing:
    """7Timer astro：3 天、3 小时步长，含 seeing / transparency。"""
    params = {
        "lon": round(lon, 3),
        "lat": round(lat, 3),
        "ac": 0,
        "unit": "metric",
        "output": "json",
        "tzshift": tz_offset_hours,
    }
    try:
        data = get_json(SEVENTIMER, params)
    except Exception as exc:
        return Seeing(source_error=f"7Timer: {exc}")

    init = str(data.get("init", ""))
    try:
        init_dt = datetime.strptime(init, "%Y%m%d%H").replace(tzinfo=timezone.utc)
    except ValueError:
        return Seeing(source_error="7Timer: init 字段异常")

    hours: list[SeeingHour] = []
    for row in data.get("dataseries", []):
        try:
            tp = int(row.get("timepoint", 0))
            hours.append(
                SeeingHour(
                    time=init_dt + timedelta(hours=tp),
                    cloudcover=int(row.get("cloudcover", 5)),
                    seeing=int(row.get("seeing", 5)),
                    transparency=int(row.get("transparency", 5)),
                    rh=int(row.get("rh2m", 60)),
                    temp=int(row.get("temp2m", 10)),
                )
            )
        except (TypeError, ValueError):
            continue
    return Seeing(hours=hours)
