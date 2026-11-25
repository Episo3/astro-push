"""天文历回归测试。

所有期望值都来自独立数据源（JPL Horizons / MET Norway / Open-Meteo / 已知日食时刻），
不是从本实现里抄的。跑法：
    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import math
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from astropush import astro as A  # noqa: E402

UTC = timezone.utc
SZ = ZoneInfo("America/Chicago")
LAT, LON = 43.0731, -89.4012  # Madison, WI


def arcmin(a: float, b: float) -> float:
    """两个角度（度）之差，单位角分，考虑跨 0/360。"""
    d = abs((a - b + 180) % 360 - 180)
    return d * 60


class TestTime(unittest.TestCase):
    def test_jd_roundtrip(self):
        dt = datetime(2024, 4, 8, 18, 21, tzinfo=UTC)
        back = A.datetime_from_jd(A.jd_from_datetime(dt))
        self.assertLess(abs((back - dt).total_seconds()), 1.0)

    def test_known_jd(self):
        # 2000-01-01 12:00 UTC == JD 2451545.0
        self.assertAlmostEqual(A.jd_from_datetime(datetime(2000, 1, 1, 12, tzinfo=UTC)),
                               2451545.0, places=6)


class TestMoonPhases(unittest.TestCase):
    def _find(self, year, month, label):
        for name, dt in A.principal_phases(year, month):
            if name == label:
                return dt
        return None

    def test_new_moon_2024_eclipse(self):
        # 2024-04-08 18:21 UTC 新月（北美日全食，NASA 时刻）
        got = self._find(2024, 4, "新月")
        self.assertIsNotNone(got)
        delta = abs((got - datetime(2024, 4, 8, 18, 21, tzinfo=UTC)).total_seconds())
        self.assertLess(delta, 180, f"新月误差 {delta}s")

    def test_full_moon_2024(self):
        # 2024-03-25 07:00 UTC 满月
        got = self._find(2024, 3, "满月")
        delta = abs((got - datetime(2024, 3, 25, 7, 0, tzinfo=UTC)).total_seconds())
        self.assertLess(delta, 180, f"满月误差 {delta}s")

    def test_first_quarter_order(self):
        # 每月四个相位必须严格递增，且顺序正确
        phases = A.principal_phases(2026, 11)
        self.assertEqual([n for n, _ in phases], ["下弦", "新月", "上弦", "满月"])
        self.assertEqual([d for _, d in phases], sorted(d for _, d in phases))

    def test_illumination_extremes(self):
        new = A.moon_illumination(A.jd_from_datetime(datetime(2024, 4, 8, 18, 21, tzinfo=UTC)))
        full = A.moon_illumination(A.jd_from_datetime(datetime(2024, 3, 25, 7, 0, tzinfo=UTC)))
        self.assertLess(new["illum"], 0.02)
        self.assertGreater(full["illum"], 0.98)


class TestSun(unittest.TestCase):
    def test_solstice_declination(self):
        for dt, expect in (
            (datetime(2026, 6, 21, 8, 25, tzinfo=UTC), 23.44),
            (datetime(2026, 12, 21, 20, 50, tzinfo=UTC), -23.44),
        ):
            _, dec, _ = A.sun_equatorial(A.jd_from_datetime(dt))
            self.assertLess(abs(dec - expect), 0.05, f"{dt} 赤纬 {dec}")

    def test_sunrise_sunset_madison(self):
        # Open-Meteo: 2026-11-25 sunrise 07:02 / sunset 16:26 (CST)
        jnoon = A.jd_from_datetime(datetime(2026, 11, 25, 12, tzinfo=SZ))
        rs = A.rise_set("太阳", jnoon - 0.6, jnoon + 0.6, LAT, LON)
        rise = A.datetime_from_jd(rs["rise_jd"], SZ)
        set_ = A.datetime_from_jd(rs["set_jd"], SZ)
        self.assertLess(abs((rise - datetime(2026, 11, 25, 7, 2, tzinfo=SZ)).total_seconds()), 180)
        self.assertLess(abs((set_ - datetime(2026, 11, 25, 16, 26, tzinfo=SZ)).total_seconds()), 180)

    def test_astro_twilight_bounds(self):
        jnoon = A.jd_from_datetime(datetime(2026, 11, 25, 12, tzinfo=SZ))
        tw = A.twilight_day(jnoon, LAT, LON)
        self.assertIsNotNone(tw["astro_dusk"])
        self.assertIsNotNone(tw["astro_dawn"])
        # 冬季 Madison 天文暗夜约 11 小时
        hours = (tw["astro_dawn"] - tw["astro_dusk"]).total_seconds() / 3600
        self.assertGreater(hours, 10.0)
        self.assertLess(hours, 13.0)
        # 暗夜必须晚于日落、早于日出
        self.assertGreater(tw["astro_dusk"], tw["civil_dusk"])
        self.assertLess(tw["astro_dawn"], tw["civil_dawn"])


class TestMoonRiseSet(unittest.TestCase):
    def test_moonrise_madison(self):
        # MET Norway: 2026-11-25 moonrise 17:08 CST, moonset 08:36 CST
        jnoon = A.jd_from_datetime(datetime(2026, 11, 25, 12, tzinfo=SZ))
        rs = A.rise_set("月亮", jnoon - 0.75, jnoon + 0.75, LAT, LON)
        rise = A.datetime_from_jd(rs["rise_jd"], SZ)
        set_ = A.datetime_from_jd(rs["set_jd"], SZ)
        self.assertLess(abs((rise - datetime(2026, 11, 25, 17, 8, tzinfo=SZ)).total_seconds()), 300)
        self.assertLess(abs((set_ - datetime(2026, 11, 25, 8, 36, tzinfo=SZ)).total_seconds()), 300)


class TestPlanets(unittest.TestCase):
    """对照 JPL Horizons（地心视 RA/Dec，2026-11-25 00:00 UTC）。"""

    CASES = {
        "水星": (14 + 47 / 60 + 59.84 / 3600, -(13 + 56 / 60 + 26.2 / 3600)),
        "金星": (13 + 33 / 60 + 55.41 / 3600, -(9 + 12 / 60 + 45.7 / 3600)),
        "火星": (10 + 9 / 60 + 57.07 / 3600, 13 + 37 / 60 + 38.9 / 3600),
        "木星": (9 + 56 / 60 + 0.68 / 3600, 13 + 24 / 60 + 49.7 / 3600),
        "土星": (0 + 34 / 60 + 6.01 / 3600, 0 + 50 / 60 + 17.6 / 3600),
    }

    def test_against_horizons(self):
        jd = A.jd_from_datetime(datetime(2026, 11, 25, 0, 0, tzinfo=UTC))
        for name, (ra_h, dec) in self.CASES.items():
            ra, dec_got, _ = A.planet_geocentric(name, jd)
            self.assertLess(arcmin(ra, ra_h * 15), 10, f"{name} 赤经偏差")
            self.assertLess(arcmin(dec_got, dec), 10, f"{name} 赤纬偏差")


class TestHorizontal(unittest.TestCase):
    def test_polaris_altitude_equals_latitude(self):
        # 北极星（RA 2.53h, Dec 89.26°）高度角 ≈ 观测地纬度
        jd = A.jd_from_datetime(datetime(2026, 11, 25, 4, 0, tzinfo=UTC))
        alt, _ = A.equatorial_to_horizontal(2.5303 * 15, 89.264, jd, LAT, LON)
        self.assertLess(abs(alt - LAT), 1.0)

    def test_culmination_altitude(self):
        # 固定目标中天高度 = 90 - |lat - dec|
        ra_deg, dec = 10.68, 41.27  # M31
        jd = A.jd_from_datetime(datetime(2026, 11, 25, 18, tzinfo=SZ))
        tj = A.transit_fixed(ra_deg, dec, jd, LAT, LON)
        alt, _ = A.equatorial_to_horizontal(ra_deg, dec, tj, LAT, LON)
        expect = 90 - abs(LAT - dec)
        self.assertLess(abs(alt - expect), 1.0)

    def test_sun_transit_is_solar_noon(self):
        # MET Norway solarnoon: 2026-11-25 11:44 CST，高度 26.08°
        jd = A.jd_from_datetime(datetime(2026, 11, 25, 6, tzinfo=SZ))
        tr = A.transit_time("太阳", jd, LAT, LON)
        when = A.datetime_from_jd(tr["jd"], SZ)
        self.assertLess(abs((when - datetime(2026, 11, 25, 11, 44, tzinfo=SZ)).total_seconds()), 180)
        self.assertLess(abs(tr["alt"] - 26.08), 0.5)


class TestExceptions(unittest.TestCase):
    def test_polar_no_dark(self):
        # 特罗姆瑟 6 月：没有天文暗夜，不应抛异常
        jnoon = A.jd_from_datetime(datetime(2026, 6, 21, 12, tzinfo=timezone.utc))
        tw = A.twilight_day(jnoon, 69.65, 18.96)
        self.assertIsNone(tw["astro_dusk"])

    def test_rise_set_always_up(self):
        # 北极圈内夏季太阳永不落
        jd = A.jd_from_datetime(datetime(2026, 6, 21, 12, tzinfo=UTC))
        rs = A.rise_set("太阳", jd - 0.5, jd + 0.5, 78.0, 15.0)
        self.assertIsNone(rs["rise_jd"])
        self.assertIsNone(rs["set_jd"])
        self.assertTrue(rs["always_up"] or rs["always_down"])


if __name__ == "__main__":
    unittest.main()
