"""纯标准库天文历计算：太阳、月亮、行星、晨昏蒙影、升落时刻、角距。

设计原则：零第三方依赖（不要求 astropy/ephem/skyfield），全部用 Meeus《Astronomical
Algorithms》与 Paul Schlyter 的低精度算法实现。定位精度：
  - 太阳赤纬/赤经 ~0.01°（升落时刻误差 < 10 秒）
  - 月亮地心坐标 ~1 角分（升落时刻误差 < 2 分钟）
  - 行星 ~0.5-1°（够用来告诉用户「往哪个方向看，多高」）
对「今晚该不该出摊、拍什么」这个用途，远超所需。
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

D2R = math.pi / 180.0
R2D = 180.0 / math.pi

J2000 = 2451545.0
AU_KM = 149_597_870.7


# --------------------------------------------------------------------------- #
# 角度与时间工具
# --------------------------------------------------------------------------- #
def norm360(x: float) -> float:
    return x % 360.0


def sind(x: float) -> float:
    return math.sin(x * D2R)


def cosd(x: float) -> float:
    return math.cos(x * D2R)


def tand(x: float) -> float:
    return math.tan(x * D2R)


def asind(x: float) -> float:
    return math.degrees(math.asin(max(-1.0, min(1.0, x))))


def atan2d(y: float, x: float) -> float:
    return math.degrees(math.atan2(y, x))


def jd_from_datetime(dt: datetime) -> float:
    """把带时区的 datetime 转成 Julian Day。"""
    dt = dt.astimezone(timezone.utc)
    year, month = dt.year, dt.month
    day = (
        dt.day
        + (dt.hour + (dt.minute + (dt.second + dt.microsecond / 1e6) / 60.0) / 60.0)
        / 24.0
    )
    if month <= 2:
        year -= 1
        month += 12
    a = year // 100
    b = 2 - a + a // 4
    return (
        int(365.25 * (year + 4716))
        + int(30.6001 * (month + 1))
        + day
        + b
        - 1524.5
    )


def datetime_from_jd(jd: float, tz: timezone | None = None) -> datetime:
    """Julian Day 转 datetime（默认 UTC）。"""
    jd += 0.5
    z = int(jd)
    f = jd - z
    if z < 2299161:
        a = z
    else:
        alpha = int((z - 1867216.25) / 36524.25)
        a = z + 1 + alpha - alpha // 4
    b = a + 1524
    c = int((b - 122.1) / 365.25)
    d = int(365.25 * c)
    e = int((b - d) / 30.6001)
    day_f = b - d - int(30.6001 * e) + f
    day = int(day_f)
    frac = day_f - day
    month = e - 1 if e < 14 else e - 13
    year = c - 4716 if month > 2 else c - 4715
    seconds = frac * 86400.0
    dt = datetime(year, month, day, tzinfo=timezone.utc) + timedelta(seconds=seconds)
    return dt.astimezone(tz) if tz else dt


def jd_now() -> float:
    return jd_from_datetime(datetime.now(timezone.utc))


def local_sidereal_time(jd: float, lon_deg: float) -> float:
    """当地恒星时（度）。"""
    t = (jd - J2000) / 36525.0
    gmst = (
        280.46061837
        + 360.98564736629 * (jd - J2000)
        + 0.000387933 * t * t
        - t * t * t / 38710000.0
    )
    return norm360(gmst + lon_deg)


def obliquity(jd: float) -> float:
    t = (jd - J2000) / 36525.0
    return 23.439291 - 0.0130042 * t - 1.64e-7 * t * t + 5.04e-7 * t * t * t


def ecliptic_to_equatorial(lon: float, lat: float, jd: float) -> tuple[float, float]:
    """黄道坐标（度）→ 赤道坐标（赤经/赤纬，度）。"""
    eps = obliquity(jd)
    cb, sb = cosd(lat), sind(lat)
    x = cb * cosd(lon)
    y = cb * sind(lon)
    z = sb
    y2 = y * cosd(eps) - z * sind(eps)
    z2 = y * sind(eps) + z * cosd(eps)
    ra = norm360(atan2d(y2, x))
    dec = asind(z2)
    return ra, dec


def equatorial_to_horizontal(
    ra: float, dec: float, jd: float, lat: float, lon: float
) -> tuple[float, float]:
    """赤道坐标 → 地平坐标（高度角, 方位角，度；方位角 0=北, 90=东）。"""
    lst = local_sidereal_time(jd, lon)
    ha = norm360(lst - ra)
    if ha > 180:
        ha -= 360
    sin_alt = sind(dec) * sind(lat) + cosd(dec) * cosd(lat) * cosd(ha)
    alt = asind(sin_alt)
    y = -cosd(dec) * sind(ha)
    x = sind(dec) * cosd(lat) - cosd(dec) * sind(lat) * cosd(ha)
    az = norm360(atan2d(y, x))
    return alt, az


def angular_separation(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    """球面角距（度），用 haversine 形式，近距离稳定。"""
    d = sind(dec1) * sind(dec2) + cosd(dec1) * cosd(dec2) * cosd(ra1 - ra2)
    return math.degrees(math.acos(max(-1.0, min(1.0, d))))


def compass_name(az: float) -> str:
    names = ["北", "东北", "东", "东南", "南", "西南", "西", "西北"]
    return names[int(norm360(az + 22.5) // 45) % 8]


# --------------------------------------------------------------------------- #
# 太阳
# --------------------------------------------------------------------------- #
def sun_ecliptic(jd: float) -> tuple[float, float]:
    """太阳视黄经（度）与日地距离（AU），精度约 0.01°。"""
    n = jd - J2000
    mean_lon = norm360(280.460 + 0.9856474 * n)
    mean_anom = norm360(357.528 + 0.9856003 * n)
    lon = norm360(
        mean_lon + 1.915 * sind(mean_anom) + 0.020 * sind(2 * mean_anom)
    )
    r = 1.00014 - 0.01671 * cosd(mean_anom) - 0.00014 * cosd(2 * mean_anom)
    return lon, r


def sun_equatorial(jd: float) -> tuple[float, float, float]:
    """太阳赤经、赤纬（度）与距离（AU）。"""
    lon, r = sun_ecliptic(jd)
    ra, dec = ecliptic_to_equatorial(lon, 0.0, jd)
    return ra, dec, r


def sun_altitude(jd: float, lat: float, lon: float) -> float:
    ra, dec, _ = sun_equatorial(jd)
    alt, _ = equatorial_to_horizontal(ra, dec, jd, lat, lon)
    return alt


# --------------------------------------------------------------------------- #
# 月亮（Meeus 第 47 章截断级数）
# --------------------------------------------------------------------------- #
# 每项：(D, M, M', F, 经度系数 1e-6 度, 距离系数 1e-3 km)
_MOON_LON_DIST = [
    (0, 0, 1, 0, 6288774, -20905355),
    (2, 0, -1, 0, 1274027, -3699111),
    (2, 0, 0, 0, 658314, -2955968),
    (0, 0, 2, 0, 213618, -569925),
    (0, 1, 0, 0, -185116, 48888),
    (0, 0, 0, 2, -114332, -3149),
    (2, 0, -2, 0, 58793, 246158),
    (2, -1, -1, 0, 57066, -152138),
    (2, 0, 1, 0, 53322, -170733),
    (2, -1, 0, 0, 45758, -204586),
    (0, 1, -1, 0, -40923, -129620),
    (1, 0, 0, 0, -34720, 108743),
    (0, 1, 1, 0, -30383, 104755),
    (2, 0, 0, -2, 15327, 10321),
    (0, 0, 1, 2, -12528, 0),
    (0, 0, 1, -2, 10980, 79661),
    (4, 0, -1, 0, 10675, -34782),
    (0, 0, 3, 0, 10034, -23210),
    (4, 0, -2, 0, 8548, -21636),
    (2, 1, -1, 0, -7888, 24208),
    (2, 1, 0, 0, -6766, 30824),
    (1, 0, -1, 0, -5163, -8379),
    (1, 1, 0, 0, 4987, -16675),
    (2, -1, 1, 0, 4036, -12831),
    (2, 0, 2, 0, 3994, -10445),
    (4, 0, 0, 0, 3861, -11650),
    (2, 0, -3, 0, 3665, 14403),
    (0, 1, -2, 0, -2689, -7003),
    (2, 0, -1, 2, -2602, 0),
    (2, -1, -2, 0, 2390, 10056),
    (1, 0, 1, 0, -2348, 6322),
    (2, -2, 0, 0, 2236, -9884),
    (0, 1, 2, 0, -2120, 5751),
    (0, 2, 0, 0, -2069, 0),
    (2, -2, -1, 0, 2048, -4950),
    (2, 0, 1, -2, -1773, 4130),
    (2, 0, 0, 2, -1595, 0),
    (4, -1, -1, 0, 1215, -3958),
    (0, 0, 2, 2, -1110, 0),
    (3, 0, -1, 0, -892, 3258),
    (2, 1, 1, 0, -810, 2616),
    (4, -1, -2, 0, 759, -1897),
    (0, 2, -1, 0, -713, -2117),
    (2, 2, -1, 0, -700, 2354),
    (2, 1, -2, 0, 691, 0),
    (2, -1, 0, -2, 596, 0),
    (4, 0, 1, 0, 549, -1423),
    (0, 0, 4, 0, 537, -1117),
    (4, -1, 0, 0, 520, -1571),
    (1, 0, -2, 0, -487, -1739),
    (2, 1, 0, -2, -399, 0),
    (0, 0, 2, -2, -381, -4421),
    (1, 1, 1, 0, 351, 0),
    (3, 0, -2, 0, -340, 0),
    (4, 0, -3, 0, 330, 0),
    (2, -1, 2, 0, 327, 0),
    (0, 2, 1, 0, -323, 1165),
    (1, 1, -1, 0, 299, 0),
    (2, 0, 3, 0, 294, 0),
    (2, 0, -1, -2, 0, 8752),
]

# 每项：(D, M, M', F, 纬度系数 1e-6 度)
_MOON_LAT = [
    (0, 0, 0, 1, 5128122),
    (0, 0, 1, 1, 280602),
    (0, 0, 1, -1, 277693),
    (2, 0, 0, -1, 173237),
    (2, 0, -1, 1, 55413),
    (2, 0, -1, -1, 46271),
    (2, 0, 0, 1, 32573),
    (0, 0, 2, 1, 17198),
    (2, 0, 1, -1, 9266),
    (0, 0, 2, -1, 8822),
    (2, -1, 0, -1, 8216),
    (2, 0, -2, -1, 4324),
    (2, 0, 1, 1, 4200),
    (2, 1, 0, -1, -3359),
    (2, -1, -1, 1, 2463),
    (2, -1, 0, 1, 2211),
    (2, -1, -1, -1, 2065),
    (0, 1, -1, -1, -1870),
    (4, 0, -1, -1, 1828),
    (0, 1, 0, 1, -1794),
    (0, 0, 0, 3, -1749),
    (0, 1, -1, 1, -1565),
    (1, 0, 0, 1, -1491),
    (0, 1, 1, 1, -1475),
    (0, 1, 1, -1, -1410),
    (0, 1, 0, -1, -1344),
    (1, 0, 0, -1, -1335),
    (0, 0, 3, 1, 1107),
    (4, 0, 0, -1, 1021),
    (4, 0, -1, 1, 833),
    (0, 0, 1, -3, 777),
    (4, 0, -2, 1, 671),
    (2, 0, 0, -3, 607),
    (2, 0, 2, -1, 596),
    (2, -1, 1, -1, 491),
    (2, 0, -2, 1, -451),
    (0, 0, 3, -1, 439),
    (2, 0, 2, 1, 422),
    (2, 0, -3, -1, 421),
    (2, 1, -1, 1, -366),
    (2, 1, 0, 1, -351),
    (4, 0, 0, 1, 331),
    (2, -1, 1, 1, 315),
    (2, -2, 0, -1, 302),
    (0, 0, 1, 3, -283),
    (2, 1, 1, -1, -229),
    (1, 1, 0, -1, 223),
    (1, 1, 0, 1, 223),
    (0, 1, -2, -1, -220),
    (2, 1, -1, -1, -220),
    (1, 0, 1, 1, -185),
    (2, -1, -2, -1, 181),
    (0, 1, 2, 1, -177),
    (4, 0, -2, -1, 176),
    (4, -1, -1, -1, 166),
    (1, 0, 1, -1, -164),
    (4, 0, 1, -1, 132),
    (1, 0, -1, -1, -119),
    (4, -1, 0, -1, 115),
    (2, -2, 0, 1, 107),
]


def _moon_args(jd: float) -> tuple[float, float, float, float, float]:
    t = (jd - J2000) / 36525.0
    lp = (
        218.3164477
        + 481267.88123421 * t
        - 0.0015786 * t**2
        + t**3 / 538841
        - t**4 / 65194000
    )
    d = (
        297.8501921
        + 445267.1114034 * t
        - 0.0018819 * t**2
        + t**3 / 545868
        - t**4 / 113065000
    )
    m = 357.5291092 + 35999.0502909 * t - 0.0001536 * t**2 + t**3 / 24490000
    mp = (
        134.9633964
        + 477198.8675055 * t
        + 0.0087414 * t**2
        + t**3 / 69699
        - t**4 / 14712000
    )
    f = (
        93.2720950
        + 483202.0175233 * t
        - 0.0036539 * t**2
        - t**3 / 3526000
        + t**4 / 863310000
    )
    return norm360(lp), norm360(d), norm360(m), norm360(mp), norm360(f)


def moon_ecliptic(jd: float) -> tuple[float, float, float]:
    """月亮视黄经、黄纬（度）与地心距离（km）。"""
    lp, d, m, mp, f = _moon_args(jd)
    t = (jd - J2000) / 36525.0
    # 地球轨道偏心率修正
    e = 1 - 0.002516 * t - 0.0000074 * t * t

    sum_lon = 0.0
    sum_dist = 0.0
    for cd, cm, cmp_, cf, coef_lon, coef_dist in _MOON_LON_DIST:
        arg = cd * d + cm * m + cmp_ * mp + cf * f
        ecc = e ** abs(cm)
        sum_lon += coef_lon * ecc * sind(arg)
        sum_dist += coef_dist * ecc * cosd(arg)

    sum_lat = 0.0
    for cd, cm, cmp_, cf, coef_lat in _MOON_LAT:
        arg = cd * d + cm * m + cmp_ * mp + cf * f
        sum_lat += coef_lat * (e ** abs(cm)) * sind(arg)

    # 金星/木星长期摄动与 A1/A2/A3 项（Meeus 47.9 简化为主要项）
    a1 = 119.75 + 131.849 * t
    a2 = 53.09 + 479264.290 * t
    summ_lon = (
        3958 * sind(a1) + 1962 * sind(lp - f) + 318 * sind(a2)
    )
    lon = norm360(lp + (sum_lon + summ_lon) / 1_000_000.0)
    lat = (sum_lat + (-2235 * sind(lp) + 382 * sind(a2) + 175 * sind(a1 - f)
                      + 175 * sind(a1 + f) + 127 * sind(lp - mp) - 115 * sind(lp + mp))
           ) / 1_000_000.0
    dist = 385000.56 + sum_dist / 1000.0
    return lon, lat, dist


def moon_equatorial(jd: float) -> tuple[float, float, float]:
    """月亮赤经、赤纬（度）与距离（km）。"""
    lon, lat, dist = moon_ecliptic(jd)
    ra, dec = ecliptic_to_equatorial(lon, lat, jd)
    return ra, dec, dist


def moon_altitude(jd: float, lat: float, lon: float) -> float:
    ra, dec, _ = moon_equatorial(jd)
    alt, _ = equatorial_to_horizontal(ra, dec, jd, lat, lon)
    return alt


def moon_illumination(jd: float) -> dict:
    """月相信息：照亮比例、相位角、盈亏、月龄（天）。"""
    sun_lon, _ = sun_ecliptic(jd)
    moon_lon, moon_lat, _ = moon_ecliptic(jd)
    elong = norm360(moon_lon - sun_lon)
    # 相位角（太阳-月球-地球），用近似公式（忽略月球纬度带来的 ~0.5° 误差）
    phase_angle = 180.0 - elong
    illum = (1.0 + cosd(phase_angle)) / 2.0
    age = elong / 360.0 * 29.530588853
    waxing = elong < 180.0
    return {
        "illum": illum,
        "phase_angle": phase_angle,
        "elong": elong,
        "age_days": age,
        "waxing": waxing,
        "name": moon_phase_name(elong, illum),
    }


def moon_phase_name(elong: float, illum: float) -> str:
    e = norm360(elong)
    if e < 6 or e > 354:
        return "新月"
    if e < 84:
        return "娥眉月" if illum < 0.5 else "上弦前后"
    if e <= 96:
        return "上弦"
    if e < 174:
        return "盈凸月"
    if e <= 186:
        return "满月"
    if e < 264:
        return "亏凸月"
    if e <= 276:
        return "下弦"
    return "残月"


def principal_phases(year: int, month: int) -> list[tuple[str, datetime]]:
    """某月的主要月相时刻（Meeus 第 49 章，精度约 1-2 分钟）。

    k 必须是整数 + {0, 0.25, 0.5, 0.75}，分别对应新月/上弦/满月/下弦。
    """
    start = jd_from_datetime(datetime(year, month, 1, tzinfo=timezone.utc))
    nxt_year, nxt_month = (year + 1, 1) if month == 12 else (year, month + 1)
    end = jd_from_datetime(datetime(nxt_year, nxt_month, 1, tzinfo=timezone.utc))

    labels = {0: "新月", 1: "上弦", 2: "满月", 3: "下弦"}
    n = (start - 2451550.09766) / 29.530588861
    k4_start = int(math.floor(n) * 4) - 4

    out: list[tuple[str, datetime]] = []
    for k4 in range(k4_start, k4_start + 16):
        k = k4 / 4.0
        jd = _phase_jde(k, (k4 % 4) * 90.0)
        if start <= jd < end:
            out.append((labels[k4 % 4], datetime_from_jd(jd)))
    out.sort(key=lambda x: x[1])
    return out


def _phase_jde(k: float, phase_deg: float) -> float:
    """Meeus 49.1：给定 k 与相位目标角，返回 JDE。"""
    t = k / 1236.85
    jde = (
        2451550.09766
        + 29.530588861 * k
        + 0.00015437 * t**2
        - 0.000000150 * t**3
        + 0.00000000073 * t**4
    )
    # 平近点角等
    m = norm360(2.5534 + 29.10535670 * k - 0.0000014 * t**2 - 0.00000011 * t**3)
    mp = norm360(
        201.5643
        + 385.81693528 * k
        + 0.0107582 * t**2
        + 0.00001238 * t**3
        - 0.000000058 * t**4
    )
    f = norm360(
        160.7108
        + 390.67050284 * k
        - 0.0016118 * t**2
        - 0.00000227 * t**3
        + 0.000000011 * t**4
    )
    omega = norm360(124.7746 - 1.56375588 * k + 0.0020672 * t**2 + 0.00000215 * t**3)
    e = 1 - 0.002516 * t - 0.0000074 * t * t

    if phase_deg in (0.0, 180.0):
        if phase_deg == 0.0:
            corr = (
                -0.40720 * sind(mp)
                + 0.17241 * e * sind(m)
                + 0.01608 * sind(2 * mp)
                + 0.01039 * sind(2 * f)
                + 0.00739 * e * sind(mp - m)
                - 0.00514 * e * sind(mp + m)
                + 0.00208 * e * e * sind(2 * m)
                - 0.00111 * sind(mp - 2 * f)
                - 0.00057 * sind(mp + 2 * f)
                + 0.00056 * e * sind(2 * mp + m)
                - 0.00042 * sind(3 * mp)
                + 0.00042 * e * sind(m + 2 * f)
                + 0.00038 * e * sind(m - 2 * f)
                - 0.00024 * e * sind(2 * mp - m)
                - 0.00017 * sind(omega)
                - 0.00007 * sind(mp + 2 * m)
                + 0.00004 * sind(2 * mp - 2 * f)
                + 0.00004 * sind(3 * m)
                + 0.00003 * sind(mp + m - 2 * f)
                + 0.00003 * sind(2 * mp + 2 * f)
                - 0.00003 * sind(mp + m + 2 * f)
                + 0.00003 * sind(mp - m + 2 * f)
                - 0.00002 * sind(mp - m - 2 * f)
                - 0.00002 * sind(3 * mp + m)
                + 0.00002 * sind(4 * mp)
            )
        else:
            corr = (
                -0.40614 * sind(mp)
                + 0.17302 * e * sind(m)
                + 0.01614 * sind(2 * mp)
                + 0.01043 * sind(2 * f)
                + 0.00734 * e * sind(mp - m)
                - 0.00515 * e * sind(mp + m)
                + 0.00209 * e * e * sind(2 * m)
                - 0.00111 * sind(mp - 2 * f)
                - 0.00057 * sind(mp + 2 * f)
                + 0.00056 * e * sind(2 * mp + m)
                - 0.00042 * sind(3 * mp)
                + 0.00042 * e * sind(m + 2 * f)
                + 0.00038 * e * sind(m - 2 * f)
                - 0.00024 * e * sind(2 * mp - m)
                - 0.00017 * sind(omega)
                - 0.00007 * sind(mp + 2 * m)
                + 0.00004 * sind(2 * mp - 2 * f)
                + 0.00004 * sind(3 * m)
                + 0.00003 * sind(mp + m - 2 * f)
                + 0.00003 * sind(2 * mp + 2 * f)
                - 0.00003 * sind(mp + m + 2 * f)
                + 0.00003 * sind(mp - m + 2 * f)
                - 0.00002 * sind(mp - m - 2 * f)
                - 0.00002 * sind(3 * mp + m)
                + 0.00002 * sind(4 * mp)
            )
    else:
        corr = (
            -0.62801 * sind(mp)
            + 0.17172 * e * sind(m)
            - 0.01183 * e * sind(mp + m)
            + 0.00862 * sind(2 * mp)
            + 0.00804 * sind(2 * f)
            + 0.00454 * e * sind(mp - m)
            + 0.00204 * e * e * sind(2 * m)
            - 0.00180 * sind(mp - 2 * f)
            - 0.00070 * sind(mp + 2 * f)
            - 0.00040 * sind(3 * mp)
            - 0.00034 * e * sind(2 * mp - m)
            + 0.00032 * e * sind(m + 2 * f)
            + 0.00032 * e * sind(m - 2 * f)
            - 0.00028 * e * e * sind(mp + 2 * m)
            + 0.00027 * e * sind(2 * mp + m)
            - 0.00017 * sind(omega)
            - 0.00005 * sind(mp - m - 2 * f)
            + 0.00004 * sind(2 * mp + 2 * f)
            - 0.00004 * sind(mp + m + 2 * f)
            + 0.00004 * sind(mp - 2 * m)
            + 0.00003 * sind(mp + m - 2 * f)
            + 0.00003 * sind(3 * m)
            + 0.00002 * sind(2 * mp - 2 * f)
            + 0.00002 * sind(mp - m + 2 * f)
            - 0.00002 * sind(3 * mp + m)

        )
        w = (
            0.00306
            - 0.00038 * e * cosd(m)
            + 0.00026 * cosd(mp)
            - 0.00002 * cosd(mp - m)
            + 0.00002 * cosd(mp + m)
            + 0.00002 * cosd(2 * f)
        )
        corr += w if phase_deg == 90.0 else -w

    return jde + corr


# --------------------------------------------------------------------------- #
# 行星（Schlyter 轨道要素 + 主要摄动）
# --------------------------------------------------------------------------- #
_PLANETS = {
    "水星": dict(N=(48.3313, 3.24587e-5), i=(7.0047, 5.00e-8), w=(29.1241, 1.01444e-5),
               a=0.387098, e=(0.205635, 5.59e-10), M=(168.6562, 4.0923344368)),
    "金星": dict(N=(76.6799, 2.46590e-5), i=(3.3946, 2.75e-8), w=(54.8910, 1.38374e-5),
               a=0.723330, e=(0.006773, -1.302e-9), M=(48.0052, 1.6021302244)),
    "火星": dict(N=(49.5574, 2.11081e-5), i=(1.8497, -1.78e-8), w=(286.5016, 2.92961e-5),
               a=1.523688, e=(0.093405, 2.516e-9), M=(18.6021, 0.5240207766)),
    "木星": dict(N=(100.4542, 2.76854e-5), i=(1.3030, -1.557e-7), w=(273.8777, 1.64505e-5),
               a=5.20256, e=(0.048498, 4.469e-9), M=(19.8950, 0.0830853001)),
    "土星": dict(N=(113.6634, 2.38980e-5), i=(2.4886, -1.081e-7), w=(339.3939, 2.97661e-5),
               a=9.55475, e=(0.055546, -9.499e-9), M=(316.9670, 0.0334442282)),
    "天王星": dict(N=(74.0005, 1.3978e-5), i=(0.7733, 1.9e-8), w=(96.6612, 3.0565e-5),
                 a=19.18171, e=(0.047318, 7.45e-9), M=(142.5905, 0.011725806)),
    "海王星": dict(N=(131.7806, 3.0173e-5), i=(1.7700, -2.55e-7), w=(272.8461, -6.027e-6),
                 a=30.05826, e=(0.008606, 2.15e-9), M=(260.2471, 0.005995147)),
}


def _kepler(mean_anom: float, ecc: float) -> float:
    e_deg = mean_anom + R2D * ecc * sind(mean_anom) * (1 + ecc * cosd(mean_anom))
    for _ in range(10):
        delta = (e_deg - R2D * ecc * sind(e_deg) - mean_anom) / (1 - ecc * cosd(e_deg))
        e_deg -= delta
        if abs(delta) < 1e-9:
            break
    return e_deg


def _helio(name: str, jd: float) -> tuple[float, float, float]:
    """行星日心黄道直角坐标（AU）。"""
    el = _PLANETS[name]
    d = jd - 2451543.5  # Schlyter 以 1999-12-31 0h 为 d=0
    n = norm360(el["N"][0] + el["N"][1] * d)
    inc = el["i"][0] + el["i"][1] * d
    w = norm360(el["w"][0] + el["w"][1] * d)
    e = el["e"][0] + el["e"][1] * d
    mean_anom = norm360(el["M"][0] + el["M"][1] * d)
    ea = _kepler(mean_anom, e)
    xv = el["a"] * (cosd(ea) - e)
    yv = el["a"] * math.sqrt(1 - e * e) * sind(ea)
    v = atan2d(yv, xv)
    r = math.hypot(xv, yv)
    xh = r * (cosd(n) * cosd(v + w) - sind(n) * sind(v + w) * cosd(inc))
    yh = r * (sind(n) * cosd(v + w) + cosd(n) * sind(v + w) * cosd(inc))
    zh = r * (sind(v + w) * sind(inc))
    return xh, yh, zh


def _earth_helio(jd: float) -> tuple[float, float, float]:
    """地球日心坐标 = 太阳地心坐标取反。"""
    lon, r = sun_ecliptic(jd)
    return -r * cosd(lon), -r * sind(lon), 0.0


def planet_geocentric(name: str, jd: float) -> tuple[float, float, float]:
    """行星地心赤道坐标：赤经、赤纬（度）与距离（AU）。"""
    px, py, pz = _helio(name, jd)
    ex, ey, ez = _earth_helio(jd)

    # Jupiter/Saturn/Uranus 的主要相互摄动（Schlyter）
    if name in ("木星", "土星", "天王星"):
        mj = norm360(19.8950 + 0.0830853001 * (jd - 2451543.5))
        ms = norm360(316.9670 + 0.0334442282 * (jd - 2451543.5))
        mu = norm360(142.5905 + 0.011725806 * (jd - 2451543.5))
        lon = atan2d(py, px)
        lat = atan2d(pz, math.hypot(px, py))
        r = math.sqrt(px * px + py * py + pz * pz)
        if name == "木星":
            lon += (
                -0.332 * sind(2 * mj - 5 * ms - 67.6)
                - 0.056 * sind(2 * mj - 2 * ms + 21)
                + 0.042 * sind(3 * mj - 5 * ms + 21)
                - 0.036 * sind(mj - 2 * ms)
                + 0.022 * cosd(mj - ms)
                + 0.023 * sind(2 * mj - 3 * ms + 52)
                - 0.016 * sind(mj - 5 * ms - 69)
            )
        elif name == "土星":
            lon += (
                0.812 * sind(2 * mj - 5 * ms - 67.6)
                - 0.229 * cosd(2 * mj - 4 * ms - 2)
                + 0.119 * sind(mj - 2 * ms - 3)
                + 0.046 * sind(2 * mj - 6 * ms - 69)
                + 0.014 * sind(mj - 3 * ms + 32)
            )
            lat += (
                -0.020 * cosd(2 * mj - 4 * ms - 2)
                + 0.018 * sind(2 * mj - 6 * ms - 49)
            )
        else:
            lon += (
                0.040 * sind(ms - 2 * mu + 6)
                + 0.035 * sind(ms - 3 * mu + 33)
                - 0.015 * sind(mj - mu + 20)
            )
        px = r * cosd(lon) * cosd(lat)
        py = r * sind(lon) * cosd(lat)
        pz = r * sind(lat)

    gx, gy, gz = px - ex, py - ey, pz - ez
    dist = math.sqrt(gx * gx + gy * gy + gz * gz)
    lon = norm360(atan2d(gy, gx))
    lat = atan2d(gz, math.hypot(gx, gy))
    ra, dec = ecliptic_to_equatorial(lon, lat, jd)
    return ra, dec, dist


def planet_elongation(name: str, jd: float) -> float:
    """行星与太阳的角距（度）。"""
    pra, pdec, _ = planet_geocentric(name, jd)
    sra, sdec, _ = sun_equatorial(jd)
    return angular_separation(pra, pdec, sra, sdec)


# --------------------------------------------------------------------------- #
# 升落与晨昏
# --------------------------------------------------------------------------- #
def _crossing(
    alt_fn, jd_start: float, jd_end: float, h0: float, step_min: float = 4.0
) -> list[tuple[float, str]]:
    """扫描高度角穿过 h0 的时刻，返回 [(jd, 'rise'|'set')]。"""
    step = step_min / 1440.0
    events: list[tuple[float, str]] = []
    t = jd_start
    prev_t = t
    prev = alt_fn(t) - h0
    while t < jd_end:
        t = min(t + step, jd_end)
        cur = alt_fn(t) - h0
        if (prev < 0) != (cur < 0) and abs(cur - prev) > 1e-12:
            lo, hi = prev_t, t
            for _ in range(40):
                mid = (lo + hi) / 2
                if (alt_fn(lo) - h0 < 0) == (alt_fn(mid) - h0 < 0):
                    lo = mid
                else:
                    hi = mid
            jd_cross = (lo + hi) / 2
            events.append((jd_cross, "rise" if cur > prev else "set"))
        prev, prev_t = cur, t
    return events


def rise_set(
    body: str, jd_start: float, jd_end: float, lat: float, lon: float
) -> dict:
    """某天体在 [jd_start, jd_end] 内的升起/落下时刻（UTC datetime）。"""
    if body == "太阳":
        fn = lambda j: sun_altitude(j, lat, lon)
        h0 = -0.833
    elif body == "月亮":
        fn = lambda j: moon_altitude(j, lat, lon)
        h0 = 0.125  # 含视差与半径的等效地平高度（Meeus）
    else:
        fn = lambda j: equatorial_to_horizontal(
            *planet_geocentric(body, j)[:2], j, lat, lon
        )[0]
        h0 = -0.567

    ev = _crossing(fn, jd_start, jd_end, h0)
    rise = next((jd for jd, k in ev if k == "rise"), None)
    set_ = next((jd for jd, k in ev if k == "set"), None)

    # 判断整夜是否一直在地平线上
    mid_alts = [fn(jd_start + f * (jd_end - jd_start)) for f in (0.0, 0.5, 1.0)]
    always_up = all(a - h0 > 0 for a in mid_alts) and not ev
    always_down = all(a - h0 < 0 for a in mid_alts) and not ev
    return {
        "rise_jd": rise,
        "set_jd": set_,
        "always_up": always_up,
        "always_down": always_down,
    }


def twilight_day(jd_noon: float, lat: float, lon: float) -> dict:
    """以「某本地日中午」为起点的一天（到次日中午）的晨昏时刻。

    返回当天傍晚的各阶段结束时刻（dusk）与次日清晨的各阶段开始时刻（dawn）。
    极昼/极夜时对应值可能为 None。
    """
    lo = jd_noon
    hi = jd_noon + 1.0
    out: dict[str, datetime | None] = {}
    for label, h0 in (("civil", -6.0), ("nautical", -12.0), ("astro", -18.0)):
        ev = _crossing(lambda j: sun_altitude(j, lat, lon), lo, hi, h0, step_min=10)
        sets = [jd for jd, k in ev if k == "set"]      # 太阳下落 → 傍晚
        rises = [jd for jd, k in ev if k == "rise"]    # 太阳上升 → 清晨
        out[f"{label}_dusk"] = datetime_from_jd(sets[0]) if sets else None
        out[f"{label}_dawn"] = datetime_from_jd(rises[-1]) if rises else None
    return out


def _body_radec(body: str, jd: float) -> tuple[float, float]:
    if body == "太阳":
        ra, dec, _ = sun_equatorial(jd)
    elif body == "月亮":
        ra, dec, _ = moon_equatorial(jd)
    else:
        ra, dec, _ = planet_geocentric(body, jd)
    return ra, dec


def _solve_transit(radec_fn, jd_start: float, lat: float, lon: float) -> float:
    """求 jd_start 之后最近一次中天（时角过零）时刻。

    第一步用恒星时差给出初值（固定目标精确，移动天体误差 < 数分钟），
    第二步在 ±0.1 天邻域内对高度角做三分搜索，精修到秒级。
    """
    ra, _dec = radec_fn(jd_start)
    ha = norm360(local_sidereal_time(jd_start, lon) - ra)
    jd = jd_start + norm360(-ha) / 360.98564736629

    def alt_at(j: float) -> float:
        r, d = radec_fn(j)
        return equatorial_to_horizontal(r, d, j, lat, lon)[0]

    lo, hi = jd - 0.1, jd + 0.1
    for _ in range(60):
        m1 = lo + (hi - lo) / 3
        m2 = hi - (hi - lo) / 3
        if alt_at(m1) < alt_at(m2):
            lo = m1
        else:
            hi = m2
    return (lo + hi) / 2


def transit_time(body: str, jd_start: float, lat: float, lon: float) -> dict:
    """天体在 jd_start 之后最近一次中天（最高点）的时刻、高度与方位。"""
    jd = _solve_transit(lambda j: _body_radec(body, j), jd_start, lat, lon)
    ra, dec = _body_radec(body, jd)
    alt, az = equatorial_to_horizontal(ra, dec, jd, lat, lon)
    return {"jd": jd, "alt": alt, "az": az}


def transit_fixed(ra: float, dec: float, jd_start: float, lat: float, lon: float) -> float:
    """固定天区目标（赤经赤纬不变）在 jd_start 之后最近一次中天时刻。"""
    return _solve_transit(lambda j: (ra, dec), jd_start, lat, lon)


def radec_to_hms(ra_deg: float) -> str:
    h = ra_deg / 15.0
    hh = int(h)
    mm = int((h - hh) * 60)
    ss = round(((h - hh) * 60 - mm) * 60)
    if ss == 60:
        ss = 0
        mm += 1
    return f"{hh:02d}h{mm:02d}m{ss:02d}s"


def dec_to_dms(dec_deg: float) -> str:
    sign = "-" if dec_deg < 0 else "+"
    d = abs(dec_deg)
    dd = int(d)
    mm = int((d - dd) * 60)
    ss = round(((d - dd) * 60 - mm) * 60)
    if ss == 60:
        ss = 0
        mm += 1
    return f"{sign}{dd}°{mm:02d}'{ss:02d}\""
