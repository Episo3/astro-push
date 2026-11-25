"""空间天气：NOAA SWPC 的 Kp 预报与 GOES X 射线耀斑，推导极光机会。

数据源（均免 key）：
  - https://services.swpc.noaa.gov/products/noaa-planetary-k-index-forecast.json
  - https://services.swpc.noaa.gov/json/goes/primary/xrays-1-day.json
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .weather import get_json

KP_FORECAST = "https://services.swpc.noaa.gov/products/noaa-planetary-k-index-forecast.json"
XRAY_1DAY = "https://services.swpc.noaa.gov/json/goes/primary/xrays-1-day.json"
SOLAR_WIND = "https://services.swpc.noaa.gov/products/summary/solar-wind-speed.json"

POLE_MAG_LAT = 80.65   # 北磁极（约 2025 年）
POLE_MAG_LON = -72.68


def geomagnetic_latitude(lat: float, lon: float) -> float:
    """地磁纬度（偶极子近似，误差 ~1°）。"""
    phi, phi_p = math.radians(lat), math.radians(POLE_MAG_LAT)
    dl = math.radians(lon - POLE_MAG_LON)
    s = math.sin(phi) * math.sin(phi_p) + math.cos(phi) * math.cos(phi_p) * math.cos(dl)
    return math.degrees(math.asin(max(-1.0, min(1.0, s))))


@dataclass
class KpPoint:
    time: datetime
    kp: float
    observed: bool


@dataclass
class SpaceWeather:
    kp: list[KpPoint] = field(default_factory=list)
    flare_class: str = "—"
    flare_flux: float = 0.0
    solar_wind_kmh: float | None = None
    source_error: str | None = None

    def max_kp(self, start: datetime, end: datetime) -> tuple[float, datetime | None]:
        pts = [p for p in self.kp if start <= p.time <= end]
        if not pts:
            return 0.0, None
        best = max(pts, key=lambda p: p.kp)
        return best.kp, best.time

    def kp_series(self, start: datetime, end: datetime) -> list[KpPoint]:
        return [p for p in self.kp if start <= p.time <= end]


def _classify_flux(flux: float) -> str:
    if flux <= 0:
        return "—"
    for letter, base in (("X", 1e-4), ("M", 1e-5), ("C", 1e-6), ("B", 1e-7)):
        if flux >= base:
            return f"{letter}{flux / base:.1f}"
    return f"A{flux / 1e-8:.1f}"


def fetch_space_weather() -> SpaceWeather:
    sw = SpaceWeather()
    try:
        rows = get_json(KP_FORECAST)
        for row in rows:
            try:
                t = datetime.fromisoformat(str(row["time_tag"])).replace(tzinfo=timezone.utc)
                sw.kp.append(
                    KpPoint(time=t, kp=float(row["kp"]), observed=str(row.get("observed")) == "observed")
                )
            except (KeyError, TypeError, ValueError):
                continue
    except Exception as exc:
        sw.source_error = f"SWPC Kp: {exc}"

    try:
        rows = get_json(XRAY_1DAY)
        fluxes = [
            (r.get("time_tag"), float(r.get("observed_flux") or r.get("flux") or 0))
            for r in rows
            if r.get("flux") is not None or r.get("observed_flux") is not None
        ]
        if fluxes:
            fluxes.sort(key=lambda x: str(x[0]))
            sw.flare_flux = max(f for _, f in fluxes[-24:])  # 最近 2 小时峰值
            sw.flare_class = _classify_flux(fluxes[-1][1])
            if sw.flare_class == "—":
                sw.flare_class = _classify_flux(sw.flare_flux)
    except Exception as exc:
        sw.source_error = (sw.source_error or "") + f" XRAY: {exc}"

    try:
        data = get_json(SOLAR_WIND)
        if isinstance(data, list) and data:
            sw.solar_wind_kmh = float(data[0].get("proton_speed", 0)) * 3.6
    except Exception:
        pass

    sw.kp.sort(key=lambda p: p.time)
    return sw


@dataclass
class AuroraOutlook:
    kp_max: float
    kp_time: datetime | None
    mag_lat: float
    camera_kp: float     # 相机能拍到北天低空辉光所需的 Kp
    naked_eye_kp: float  # 肉眼能分辨结构所需的 Kp
    chance: str          # 无 / 很低 / 相机可拍 / 肉眼可辨 / 强爆发
    note: str


def aurora_outlook(sw: SpaceWeather, lat: float, lon: float,
                   window_start: datetime, window_end: datetime) -> AuroraOutlook:
    mag_lat = geomagnetic_latitude(lat, lon)
    kp_max, kp_time = sw.max_kp(window_start, window_end)
    # 经验关系：极光卵赤道侧边界 ≈ 66.5° - 3.2*Kp（地磁纬度）
    camera_kp = max(0.0, (66.5 - mag_lat - 2.0) / 3.2)
    naked_kp = max(0.0, (66.5 - mag_lat + 4.0) / 3.2)

    if kp_time is None:
        return AuroraOutlook(
            kp_max=kp_max, kp_time=None, mag_lat=mag_lat,
            camera_kp=camera_kp, naked_eye_kp=naked_kp,
            chance="无预报",
            note="NOAA 的 Kp 预报通常只覆盖 3 天，这一晚还没有数据",
        )
    if kp_max >= naked_kp + 2:
        chance, note = "强爆发", "极光卵压得很南，留意北天整片绿色光幕与红色顶部"
    elif kp_max >= naked_kp:
        chance, note = "肉眼可辨", "北天低空可能出现肉眼可见的光带，找北向无遮挡、无月光的方向"
    elif kp_max >= camera_kp:
        chance, note = "相机可拍", "肉眼未必看得出，但长曝光朝北能看到绿色/紫色辉光"
    elif kp_max >= camera_kp - 1:
        chance, note = "很低", "预报处于临界值，值得刷一下实时 Kp 再决定"
    else:
        chance, note = "无", "地磁平静，这次的极光卵够不到你的纬度"
    return AuroraOutlook(
        kp_max=kp_max,
        kp_time=kp_time,
        mag_lat=mag_lat,
        camera_kp=camera_kp,
        naked_eye_kp=naked_kp,
        chance=chance,
        note=note,
    )
