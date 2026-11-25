"""评分引擎：把天气、月相、天体位置揉成「今晚值不值得出摊、拍什么」。

评分分两层：
  sky_score   —— 纯天气质量（云量/降水/雾/透明度/视宁度）
  deep_sky    —— 再叠加月光影响（想拍深空时才关心）
因为「月亮」本身也是合法目标，两个分数都要保留。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from . import astro as A
from .config import Config
from .spaceweather import AuroraOutlook, SpaceWeather, aurora_outlook
from .targets import DEEP_SKY, Dso, active_showers, shower_intensity
from .weather import Seeing, SeeingHour, Weather, WeatherHour

# 银河中心（人马座 A*）
GALACTIC_CENTER = (266.417, -29.008)


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


# --------------------------------------------------------------------------- #
# 夜晚条件
# --------------------------------------------------------------------------- #
@dataclass
class Night:
    day: date
    dusk: datetime | None          # 暗夜开始
    dawn: datetime | None          # 暗夜结束
    dark_kind: str                 # 天文暗夜 / 航海暗夜 / 民用暗夜 / 无暗夜
    moon_illum: float
    moon_name: str
    moon_alt_mean: float
    moon_up_frac: float
    sky_score: float
    deep_sky_score: float
    cloud_mean: float
    transparency: float
    seeing: float
    dew_risk: bool
    wind_max: float
    worst_cloud: float
    best_window: tuple[datetime, datetime] | None
    moon_waxing: bool = True
    slots: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def grade(self) -> str:
        s = self.deep_sky_score
        if s >= 85:
            return "A"
        if s >= 70:
            return "B"
        if s >= 50:
            return "C"
        return "D"

    @property
    def grade_text(self) -> str:
        return {
            "A": "优秀 · 值得架设备",
            "B": "不错 · 有像样窗口",
            "C": "凑合 · 赌云缝/拍月亮",
            "D": "很差 · 建议不出摊",
        }[self.grade]

    @property
    def dark_hours(self) -> float:
        if not self.dusk or not self.dawn:
            return 0.0
        return (self.dawn - self.dusk).total_seconds() / 3600.0


def _moon_at(jd: float, lat: float, lon: float) -> tuple[float, float]:
    """返回（月亮高度角, 照亮比例）。"""
    ra, dec, _ = A.moon_equatorial(jd)
    alt, _ = A.equatorial_to_horizontal(ra, dec, jd, lat, lon)
    return alt, A.moon_illumination(jd)["illum"]


def _seeing_scores(seeing: Seeing | None, when: datetime) -> tuple[float, float]:
    """返回（透明度 0-1, 视宁度 0-1）。7Timer 1-8 分制，1 最差。"""
    if not seeing or not seeing.hours:
        return 0.5, 0.5
    sh = seeing.at(when)
    if not sh:
        return 0.5, 0.5
    return clamp((sh.transparency - 1) / 7.0), clamp((sh.seeing - 1) / 7.0)


def build_night(
    cfg: Config,
    day: date,
    weather: Weather,
    seeing: Seeing,
) -> Night:
    tz = ZoneInfo(cfg.tz_name)
    lat, lon = cfg.lat, cfg.lon
    notes: list[str] = []

    noon = datetime.combine(day, time(12, 0), tzinfo=tz)
    tw = A.twilight_day(A.jd_from_datetime(noon), lat, lon)

    dusk = dawn = None
    dark_kind = "无暗夜"
    for kind, key in (("天文暗夜", "astro"), ("航海暗夜", "nautical"), ("民用暗夜", "civil")):
        if tw.get(f"{key}_dusk") and tw.get(f"{key}_dawn"):
            dusk = tw[f"{key}_dusk"].astimezone(tz)
            dawn = tw[f"{key}_dawn"].astimezone(tz)
            dark_kind = kind
            break
    if dark_kind != "天文暗夜" and dusk:
        notes.append(f"本纬度今夜没有天文暗夜（太阳最低仍高于 -18°），按{dark_kind}算")

    if not dusk or not dawn or dawn <= dusk:
        return Night(
            day=day, dusk=dusk, dawn=dawn, dark_kind=dark_kind,
            moon_illum=0, moon_name="—", moon_alt_mean=0, moon_up_frac=0,
            sky_score=0, deep_sky_score=0, cloud_mean=100, transparency=0.5,
            seeing=0.5, dew_risk=True, wind_max=0, worst_cloud=100,
            best_window=None, notes=notes + ["无法确定暗夜窗口"],
        )

    # 30 分钟一个采样槽
    slots: list[dict] = []
    t = dusk
    while t < dawn:
        jd = A.jd_from_datetime(t)
        h = weather.at(t)
        alt_m, illum = _moon_at(jd, lat, lon)
        if h:
            clear = 1.0 - h.eff_cloud / 100.0
            if h.precip_prob >= 60:
                clear *= 0.2
            elif h.precip_prob >= 30:
                clear *= 0.6
            fog = h.fog_risk
            if fog:
                clear *= 0.45
            moon_pen = 0.62 * illum * clamp(alt_m / 25.0) ** 0.5 if alt_m > 0 else 0.0
            deep = clear * (1.0 - moon_pen)
            slots.append({
                "time": t, "cloud": h.eff_cloud, "clear": clear, "deep": deep,
                "moon_alt": alt_m, "rh": h.rh, "wind": h.wind_kmh,
                "dew": h.dew_spread, "precip": h.precip_prob, "fog": fog,
            })
        t += timedelta(minutes=30)

    if not slots:
        notes.append("天气数据未覆盖今晚（预报范围不足），仅给天象与月相")
        return Night(
            day=day, dusk=dusk, dawn=dawn, dark_kind=dark_kind,
            moon_illum=0, moon_name="—", moon_alt_mean=0, moon_up_frac=0,
            sky_score=0, deep_sky_score=0, cloud_mean=100, transparency=0.5,
            seeing=0.5, dew_risk=True, wind_max=0, worst_cloud=100,
            best_window=None, notes=notes,
        )

    mid = dusk + (dawn - dusk) / 2
    trans, see = _seeing_scores(seeing, mid)
    # 湿度补偿透明度：相对湿度高通常伴随水汽/薄霾
    rh_mean = sum(s["rh"] for s in slots) / len(slots)
    trans = clamp(0.65 * trans + 0.35 * clamp(1 - max(0.0, rh_mean - 55) / 55))
    see = clamp(0.8 * see + 0.2 * clamp(1 - max(0.0, sum(s["wind"] for s in slots) / len(slots) - 15) / 40))

    clear_mean = sum(s["clear"] for s in slots) / len(slots)
    deep_mean = sum(s["deep"] for s in slots) / len(slots)

    sky_score = 100 * clamp(0.66 * clear_mean + 0.20 * trans + 0.14 * see)
    deep_sky = 100 * clamp(0.74 * deep_mean + 0.15 * trans + 0.11 * see)

    # 最佳窗口：连续 deep>=0.55 且 clear>=0.5 的最长一段（裁到黎明）
    best = _best_window(slots)
    if best and best[1] > dawn:
        best = (best[0], dawn)

    moon_alts = [s["moon_alt"] for s in slots]
    illum_mid = A.moon_illumination(A.jd_from_datetime(mid))
    moon_alt_mean = sum(a for a in moon_alts if a > 0) / max(1, sum(1 for a in moon_alts if a > 0))
    moon_up_frac = sum(1 for a in moon_alts if a > 0) / len(moon_alts)

    dew_risk = sum(1 for s in slots if s["dew"] < 2.0) > len(slots) * 0.4
    if dew_risk:
        notes.append("露水风险高（露点差 <2°C）：带上加热带/除露方案")

    cloud_mean = sum(s["cloud"] for s in slots) / len(slots)
    worst = max(s["cloud"] for s in slots)
    wind_max = max(s["wind"] for s in slots)

    if cloud_mean > 70:
        notes.append("今晚以中低云为主，别指望深空")
    if any(s["fog"] for s in slots):
        notes.append("有雾/能见度差时段，注意镜面结露与光学镀膜")
    if wind_max > 35:
        notes.append(f"阵风可达 {wind_max:.0f} km/h，长焦会抖，注意三脚架配重")

    return Night(
        day=day, dusk=dusk, dawn=dawn, dark_kind=dark_kind,
        moon_illum=illum_mid["illum"], moon_name=illum_mid["name"],
        moon_waxing=illum_mid["waxing"],
        moon_alt_mean=moon_alt_mean, moon_up_frac=moon_up_frac,
        sky_score=sky_score, deep_sky_score=deep_sky,
        cloud_mean=cloud_mean, transparency=trans, seeing=see,
        dew_risk=dew_risk, wind_max=wind_max, worst_cloud=worst,
        best_window=best, slots=slots, notes=notes,
    )


def _best_window(slots: list[dict]) -> tuple[datetime, datetime] | None:
    best: tuple[datetime, datetime] | None = None
    run_start: datetime | None = None
    run_end: datetime | None = None

    def flush() -> None:
        nonlocal best, run_start, run_end
        if run_start and run_end and (run_end - run_start) >= timedelta(minutes=59):
            if not best or (run_end - run_start) > (best[1] - best[0]):
                best = (run_start, run_end)

    for s in slots:
        ok = s["deep"] >= 0.55 and s["clear"] >= 0.5
        if ok:
            if run_start is None:
                run_start = s["time"]
            run_end = s["time"] + timedelta(minutes=30)
        else:
            flush()
            run_start = run_end = None
    flush()
    return best


# --------------------------------------------------------------------------- #
# 目标推荐
# --------------------------------------------------------------------------- #
@dataclass
class Suggestion:
    title: str
    kind: str            # 深空 / 行星 / 月亮 / 银河 / 流星雨 / 极光
    detail: str
    when: str = ""
    where: str = ""
    score: float = 0.0
    priority: int = 0


def _target_visibility(dso: Dso, night: Night, cfg: Config) -> dict:
    """目标在暗夜窗口内最高高度、中天时刻、与月亮的角距。"""
    lat, lon = cfg.lat, cfg.lon
    t = night.dusk
    best_alt, best_jd, best_az = -90.0, None, None
    while t <= night.dawn:
        jd = A.jd_from_datetime(t)
        alt, az = A.equatorial_to_horizontal(dso.ra, dso.dec, jd, lat, lon)
        if alt > best_alt:
            best_alt, best_jd, best_az = alt, jd, az
        t += timedelta(minutes=20)

    mid = night.dusk + (night.dawn - night.dusk) / 2
    jd_mid = A.jd_from_datetime(mid)
    moon_ra, moon_dec, _ = A.moon_equatorial(jd_mid)
    sep = A.angular_separation(dso.ra, dso.dec, moon_ra, moon_dec)
    transit_jd = A.transit_fixed(dso.ra, dso.dec, A.jd_from_datetime(night.dusk), lat, lon)
    return {
        "max_alt": best_alt,
        "best_jd": best_jd,
        "best_az": best_az,
        "moon_sep": sep,
        "transit_jd": transit_jd,
    }


def rank_deep_sky(night: Night, cfg: Config, limit: int = 6) -> list[tuple[Dso, dict, float]]:
    if not night.dusk or not night.dawn:
        return []
    if night.deep_sky_score < 40:  # 今晚连"凑合"都算不上，别硬推深空浪费时间
        return []
    lat, lon = cfg.lat, cfg.lon
    tz = ZoneInfo(cfg.tz_name)
    moon_illum = night.moon_illum
    out: list[tuple[Dso, dict, float]] = []

    for dso in DEEP_SKY:
        vis = _target_visibility(dso, night, cfg)
        if vis["max_alt"] < cfg.threshold("min_target_alt"):
            continue
        alt_score = clamp((vis["max_alt"] - 15) / 55)
        # 中天是否落在窗口内（更省事）
        if night.dusk <= A.datetime_from_jd(vis["transit_jd"], tz) <= night.dawn:
            alt_score = min(1.0, alt_score + 0.12)
        # 月光影响：分离度 + 亮度 + 目标波段
        moon_up = night.moon_alt_mean > 0
        sep = vis["moon_sep"]
        sep_factor = clamp(sep / 70.0) if moon_up else 1.0
        band_penalty = 1.0
        if moon_up:
            if dso.band == "宽带":
                band_penalty = 0.55 if moon_illum > 0.35 else 0.8
            elif dso.band == "窄带":
                band_penalty = 0.85 if moon_illum > 0.5 else 0.95
        moon_factor = (1 - 0.65 * moon_illum * (1 - sep_factor)) * band_penalty
        # 亮度/尺寸加成：面亮度高的目标更能扛薄云
        mag_score = clamp((12.0 - dso.mag) / 8.0)
        val = (0.55 * alt_score + 0.25 * mag_score + 0.20) * max(0.1, moon_factor)
        val *= night.deep_sky_score / 100.0
        out.append((dso, vis, val))

    out.sort(key=lambda x: -x[2])
    return out[:limit]


def rank_planets(night: Night, cfg: Config) -> list[dict]:
    if not night.dusk or not night.dawn:
        return []
    lat, lon = cfg.lat, cfg.lon
    tz = ZoneInfo(cfg.tz_name)
    res = []
    for name in ("木星", "土星", "火星", "金星", "水星"):
        jd_dusk = A.jd_from_datetime(night.dusk)
        tr = A.transit_time(name, jd_dusk, lat, lon)
        tr_local = A.datetime_from_jd(tr["jd"], tz)
        in_window = night.dusk <= tr_local <= night.dawn
        # 窗口内最高高度
        best_alt, best_t = -90.0, None
        t = night.dusk
        while t <= night.dawn:
            jd = A.jd_from_datetime(t)
            ra, dec, _ = A.planet_geocentric(name, jd)
            alt, az = A.equatorial_to_horizontal(ra, dec, jd, lat, lon)
            if alt > best_alt:
                best_alt, best_t = alt, t
            t += timedelta(minutes=30)
        if best_alt < 8:
            continue
        elong = A.planet_elongation(name, A.jd_from_datetime(night.dusk + (night.dawn - night.dusk) / 2))
        res.append({
            "name": name, "max_alt": best_alt, "best_time": best_t,
            "transit": tr_local, "in_window": in_window, "elong": elong,
        })
    res.sort(key=lambda x: -x["max_alt"])
    return res


def build_suggestions(night: Night, cfg: Config, sw: SpaceWeather,
                      aurora: AuroraOutlook) -> list[Suggestion]:
    tz = ZoneInfo(cfg.tz_name)
    lat, lon = cfg.lat, cfg.lon
    out: list[Suggestion] = []
    light_note = "" if cfg.loc.get("bortle", 5) <= 4 else "（市区光害，深空建议周末去暗夜点）"

    # 1) 极光
    if aurora.chance in ("相机可拍", "肉眼可辨", "强爆发"):
        out.append(Suggestion(
            title=f"极光：{aurora.chance}（Kp 峰值 {aurora.kp_max:.1f}）",
            kind="极光",
            detail=aurora.note,
            when=(A.datetime_from_jd(A.jd_from_datetime(aurora.kp_time), tz).strftime("%H:%M")
                  if aurora.kp_time else ""),
            where="北向无遮挡地平线",
            score=92 if aurora.chance != "相机可拍" else 78,
            priority=1,
        ))

    # 2) 流星雨
    for shower, inten in active_showers(night.day):
        if inten < 0.2:
            continue
        radiant_alt, radiant_az = None, None
        if night.dusk and night.dawn:
            best = (-90.0, None)
            t = night.dusk
            while t <= night.dawn:
                jd = A.jd_from_datetime(t)
                alt, az = A.equatorial_to_horizontal(shower.ra, shower.dec, jd, lat, lon)
                if alt > best[0]:
                    best = (alt, t)
                t += timedelta(minutes=30)
            radiant_alt, radiant_az = best[0], None
        if radiant_alt is None or radiant_alt < 15:
            continue
        moon_pen = 1 - 0.6 * night.moon_illum
        est_rate = shower.zhr * inten * moon_pen * clamp(radiant_alt / 60.0) * 0.6
        out.append(Suggestion(
            title=f"{shower.cname}：预计约 {est_rate:.0f} 颗/小时",
            kind="流星雨",
            detail=f"ZHR {shower.zhr}，辐射点最高 {radiant_alt:.0f}°，速度 {shower.speed:.0f} km/s"
                   + (f"；{shower.note}" if shower.note else ""),
            when=(f"辐射点最高约 {A.datetime_from_jd(A.jd_from_datetime(best[1]), tz).strftime('%H:%M')}"
                  if best[1] else ""),
            where=f"{A.compass_name(az if radiant_az else 0)}方低空找辐射点，别盯着辐射点看",
            score=60 + est_rate,
            priority=2,
        ))

    # 3) 深空
    dso_priority = 2 if night.deep_sky_score >= 55 else 3
    moon_priority = 3 if night.deep_sky_score >= 55 else 2
    for dso, vis, val in rank_deep_sky(night, cfg, limit=4):
        t_show = A.datetime_from_jd(vis["best_jd"], tz)
        alt_show, az_show = vis["max_alt"], vis["best_az"]
        alt_at_best = vis["max_alt"]
        out.append(Suggestion(
            title=f"{dso.name} {dso.cname}（{dso.kind}，{dso.mag:.1f} 等）",
            kind="深空",
            detail=(f"窗口内最高 {alt_at_best:.0f}°，月亮离它 {vis['moon_sep']:.0f}°，{dso.band}滤镜"
                    + (f"；{dso.note}" if dso.note else "") + light_note),
            when=t_show.strftime("%H:%M"),
            where=f"方位 {az_show:.0f}°（{A.compass_name(az_show)}），高度 {alt_show:.0f}°",
            score=val * 100,
            priority=dso_priority,
        ))

    # 4) 月亮
    if night.moon_alt_mean > 12 and night.moon_illum > 0.2 and night.dusk and night.dawn:
        terminator = 0.35 <= night.moon_illum <= 0.75
        out.append(Suggestion(
            title=f"月面：{night.moon_name} {night.moon_illum * 100:.0f}%",
            kind="月亮",
            detail=("晨昏线附近环形山立体感最强，长焦 + 视频帧叠加（lucky imaging）"
                    if terminator else
                    "满月前后适合拍「月出带地景」的低空满月，日月同框色彩更好"),
            when="月出/月落前后 1 小时",
            where=f"月亮平均高度 {night.moon_alt_mean:.0f}°",
            score=55 + (25 if terminator else 10),
            priority=moon_priority,
        ))

    # 5) 银河
    if night.dusk and night.dawn:
        best = (-90.0, None)
        t = night.dusk
        while t <= night.dawn:
            jd = A.jd_from_datetime(t)
            alt, az = A.equatorial_to_horizontal(*GALACTIC_CENTER, jd, lat, lon)
            if alt > best[0]:
                best = (alt, t, az)
            t += timedelta(minutes=30)
        if best[0] > 10:
            out.append(Suggestion(
                title=f"银河核心：最高 {best[0]:.0f}°",
                kind="银河",
                detail="核心过中天时抬头看人马座方向；广域 14-24mm，赤道仪跟踪可拉长曝",
                when=A.datetime_from_jd(A.jd_from_datetime(best[1]), tz).strftime("%H:%M"),
                where=f"方位 {best[2]:.0f}°（{A.compass_name(best[2])}）",
                score=50 + best[0] * (1 - night.moon_illum) * 1.2,
                priority=4,
            ))

    # 6) 行星
    for p in rank_planets(night, cfg)[:4]:
        note = ""
        if p["name"] in ("水星", "金星") and p["elong"] < 20:
            note = "离太阳很近，只在昏/晨光里短暂可见"
        elif p["name"] == "土星":
            note = "长焦 + 2.5x 巴洛，环形山带要拍出环缝"
        elif p["name"] == "木星":
            note = "连同四大卫星一起构图，短曝光视频叠加"
        out.append(Suggestion(
            title=f"{p['name']}：高度 {p['max_alt']:.0f}°",
            kind="行星",
            detail=note or f"与太阳角距 {p['elong']:.0f}°",
            when=p["best_time"].strftime("%H:%M") if p["best_time"] else "",
            where="",
            score=45 + p["max_alt"] * 0.4,
            priority=4,
        ))

    out.sort(key=lambda s: (s.priority, -s.score))
    return out


# --------------------------------------------------------------------------- #
# 未来天象日历
# --------------------------------------------------------------------------- #
@dataclass
class Event:
    when: datetime
    title: str
    detail: str
    kind: str


def phenomena_calendar(cfg: Config, start: datetime, days: int = 60) -> list[Event]:
    tz = ZoneInfo(cfg.tz_name)
    lat, lon = cfg.lat, cfg.lon
    events: list[Event] = []
    start_day = start.date()

    # 月相
    months = {(start_day.year, start_day.month)}
    for k in (1, 2):
        m = start_day.month + k
        y = start_day.year + (m - 1) // 12
        months.add((y, (m - 1) % 12 + 1))
    for y, m in sorted(months):
        for name, dt in A.principal_phases(y, m):
            if start <= dt <= start + timedelta(days=days):
                events.append(Event(dt, f"{name}", _phase_detail(name, dt, lat, lon), "月相"))

    # 流星雨峰值
    from .targets import upcoming_showers

    for shower, delta in upcoming_showers(start_day, days):
        peak = shower.peak_date(start_day.year)
        if peak < start_day - timedelta(days=15):
            peak = shower.peak_date(start_day.year + 1)
        when = datetime.combine(peak, time(2, 0), tzinfo=tz)
        if when < start:
            continue
        moon = A.moon_illumination(A.jd_from_datetime(when))
        events.append(Event(
            when,
            f"{shower.cname}极大（ZHR {shower.zhr}）",
            f"月相 {moon['name']} 照亮 {moon['illum'] * 100:.0f}%"
            + ("，月光干扰明显" if moon["illum"] > 0.5 else "，条件友好")
            + (f"；{shower.note}" if shower.note else ""),
            "流星雨",
        ))

    # 行星事件：冲日 / 大距 / 合月
    events.extend(_planet_events(start, days, tz, lat, lon))

    events.sort(key=lambda e: e.when)
    return [e for e in events if e.when >= start]


def _phase_detail(name: str, dt: datetime, lat: float, lon: float) -> str:
    if name == "满月":
        return "适合拍月出月落地景，深空基本放弃"
    if name == "新月":
        return "深空/银河最佳窗口，尽量安排这一天前后出摊"
    return "晨昏线细节好，适合月面高分辨率"


def _planet_events(start: datetime, days: int, tz, lat: float, lon: float) -> list[Event]:
    """扫描未来 N 天，找冲日、大距、合月。"""
    out: list[Event] = []
    n = days
    jd0 = A.jd_from_datetime(start)

    outer = ("火星", "木星", "土星")
    inner = ("水星", "金星")

    # 冲日：太阳-地球-行星 成一线（角距 180° 附近极大）
    for name in outer:
        best = (-1.0, None)
        for i in range(n + 1):
            jd = jd0 + i
            elong = A.planet_elongation(name, jd)
            if elong > best[0]:
                best = (elong, jd)
        if best[1] and best[0] > 168:
            when = A.datetime_from_jd(best[1], tz).replace(hour=22, minute=0)
            if start - timedelta(days=2) <= when <= start + timedelta(days=n):
                out.append(Event(when, f"{name}冲日", f"与太阳角距 {best[0]:.0f}°，整夜可见，全年最亮", "行星"))

    # 大距：水星/金星角距局部极大
    for name in inner:
        vals = []
        for i in range(n + 1):
            jd = jd0 + i
            vals.append((A.planet_elongation(name, jd), jd))
        for i in range(1, len(vals) - 1):
            if vals[i][0] > vals[i - 1][0] and vals[i][0] > vals[i + 1][0] and vals[i][0] > 15:
                when = A.datetime_from_jd(vals[i][1], tz)
                out.append(Event(when, f"{name}大距（{vals[i][0]:.0f}°）",
                                 "昏星/晨星最远时刻，最佳观测窗口", "行星"))

    # 合月：月亮与行星角距局部极小且 < 5°
    for name in ("木星", "土星", "火星", "金星"):
        seps = []
        for i in range(n + 1):
            jd = jd0 + i
            pra, pdec, _ = A.planet_geocentric(name, jd)
            mra, mdec, _ = A.moon_equatorial(jd)
            seps.append((A.angular_separation(pra, pdec, mra, mdec), jd))
        for i in range(1, len(seps) - 1):
            s, jd = seps[i]
            if s < 5.0 and s <= seps[i - 1][0] and s <= seps[i + 1][0]:
                when = A.datetime_from_jd(jd, tz)
                out.append(Event(when, f"{name}合月（{s:.1f}°）",
                                 "同框构图的好机会，注意曝光平衡：月亮过曝会失去细节", "合月"))
    return out
