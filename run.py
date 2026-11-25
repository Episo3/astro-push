#!/usr/bin/env python3
"""每日天文摄影推送 —— 入口。

用法：
    python3 run.py                 # 正常生成并推送（按 config.json 的 channels）
    python3 run.py --dry-run       # 只生成、打印、存档，不真的发
    python3 run.py --night 1       # 看明晚
    python3 run.py --json          # 额外输出结构化 JSON（方便接别的脚本）
    python3 run.py --test-push     # 只发一条测试消息，验证 token/webhook
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))

from astropush import astro as A                      # noqa: E402
from astropush.config import ROOT, load_config        # noqa: E402
from astropush.notify import save_archive, send_all   # noqa: E402
from astropush.report import (                        # noqa: E402
    Report, build_markdown, build_telegram_html, build_text,
)
from astropush.score import (                         # noqa: E402
    Night, build_night, build_suggestions, phenomena_calendar,
)
from astropush.spaceweather import aurora_outlook, fetch_space_weather  # noqa: E402
from astropush.targets import active_showers, upcoming_showers  # noqa: E402
from astropush.weather import fetch_7timer, fetch_open_meteo    # noqa: E402


def _tz_offset_hours(tz_name: str) -> float:
    off = datetime.now(ZoneInfo(tz_name)).utcoffset() or timedelta(0)
    return off.total_seconds() / 3600.0


def collect(cfg, night_offset: int = 0) -> Report:
    tz = ZoneInfo(cfg.tz_name)
    today = datetime.now(tz).date()
    target_day = today + timedelta(days=night_offset)

    with ThreadPoolExecutor(max_workers=3) as pool:
        f_weather = pool.submit(fetch_open_meteo, cfg.lat, cfg.lon, cfg.tz_name, 7)
        f_seeing = pool.submit(fetch_7timer, cfg.lat, cfg.lon, _tz_offset_hours(cfg.tz_name))
        f_sw = pool.submit(fetch_space_weather)
        weather = f_weather.result()
        seeing = f_seeing.result()
        sw = f_sw.result()

    night = build_night(cfg, target_day, weather, seeing)

    # 未来 7 夜（含今晚）
    week: list[Night] = []
    for i in range(0, 7):
        d = today + timedelta(days=i)
        w = build_night(cfg, d, weather, seeing)
        if w.slots:
            week.append(w)

    window_start = night.dusk or datetime.now(tz)
    window_end = night.dawn or (window_start + timedelta(hours=8))
    aurora = aurora_outlook(sw, cfg.lat, cfg.lon, window_start, window_end)

    suggestions = build_suggestions(night, cfg, sw, aurora)
    events = phenomena_calendar(cfg, datetime.now(tz), days=60)

    alerts: list[str] = []
    if night.dew_risk:
        alerts.append("露水风险高，带加热带；镜头朝上时注意结露")
    if night.dark_kind != "天文暗夜" and night.dusk:
        alerts.append("今夜没有天文暗夜，拍深空会被晚霞/晨光影响")
    if night.moon_illum > 0.9 and night.moon_alt_mean > 10:
        alerts.append("满月整夜在天上，深空/银河基本没戏，改拍月面或地景月光")
    if night.wind_max > 35:
        alerts.append(f"阵风 {night.wind_max:.0f} km/h，长焦需要防抖/配重")
    if night.cloud_mean > 75:
        alerts.append("云量偏高，优先考虑出远门或拍月亮")
    if not night.best_window and night.slots:
        alerts.append("今晚没有连续 1 小时的好窗口，建议看实时卫星云图再决定")
    for shower, delta in upcoming_showers(today, 10):
        if 0 <= delta <= 10:
            alerts.append(f"{delta} 天后 {shower.cname} 极大（ZHR {shower.zhr}）")
    if weather.source_error:
        alerts.append(f"天气源异常：{weather.source_error}")
    if seeing.source_error:
        alerts.append(f"seeing 源异常：{seeing.source_error}")

    best_advice = ""
    if week:
        best = max(week, key=lambda w: w.deep_sky_score)
        if best.day != night.day and best.deep_sky_score > night.deep_sky_score + 12:
            win = (f"{best.best_window[0].strftime('%H:%M')}–{best.best_window[1].strftime('%H:%M')}"
                   if best.best_window else "无明显窗口")
            best_advice = (f"🗓 本周最佳出摊夜：{best.day.month}/{best.day.day} "
                           f"（{best.grade} {best.deep_sky_score:.0f} 分，窗口 {win}，"
                           f"月照 {best.moon_illum * 100:.0f}%）")

    src_notes = []
    if weather.source_error:
        src_notes.append(weather.source_error)
    if seeing.source_error:
        src_notes.append(seeing.source_error)
    if sw.source_error:
        src_notes.append(sw.source_error)

    return Report(
        cfg=cfg,
        night=night,
        suggestions=suggestions,
        aurora=aurora,
        week=week,
        events=events,
        sw=sw,
        generated_at=datetime.now(tz),
        alerts=alerts,
        source_notes=src_notes,
        best_advice=best_advice,
    )


def to_json(r: Report) -> dict:
    n = r.night
    return {
        "generated_at": r.generated_at.isoformat(),
        "site": r.cfg.site_name,
        "night": {
            "date": n.day.isoformat(),
            "grade": n.grade,
            "deep_sky_score": round(n.deep_sky_score, 1),
            "sky_score": round(n.sky_score, 1),
            "dark_window": [n.dusk.isoformat(), n.dawn.isoformat()] if n.dusk and n.dawn else None,
            "best_window": [n.best_window[0].isoformat(), n.best_window[1].isoformat()]
            if n.best_window else None,
            "moon": {
                "illum": round(n.moon_illum, 3),
                "name": n.moon_name,
                "mean_alt": round(n.moon_alt_mean, 1),
            },
            "cloud_mean": round(n.cloud_mean, 1),
            "worst_cloud": round(n.worst_cloud, 1),
            "transparency": round(n.transparency, 2),
            "seeing": round(n.seeing, 2),
            "wind_max": round(n.wind_max, 1),
            "dew_risk": n.dew_risk,
        },
        "suggestions": [
            {"title": s.title, "kind": s.kind, "when": s.when, "where": s.where,
             "detail": s.detail, "score": round(s.score, 1)}
            for s in r.suggestions
        ],
        "aurora": {
            "chance": r.aurora.chance,
            "kp_max": r.aurora.kp_max,
            "geomag_lat": round(r.aurora.mag_lat, 1),
            "camera_kp": round(r.aurora.camera_kp, 1),
        },
        "week": [
            {"date": w.day.isoformat(), "grade": w.grade,
             "score": round(w.deep_sky_score, 1),
             "moon_illum": round(w.moon_illum, 2)}
            for w in r.week
        ],
        "events": [
            {"when": e.when.isoformat(), "title": e.title, "detail": e.detail, "kind": e.kind}
            for e in r.events[:12]
        ],
        "alerts": r.alerts,
        "source_notes": r.source_notes,
    }


def is_due(cfg, tolerance_min: int = 40) -> tuple[bool, str]:
    """判断现在是否到了该推送的时间（用于 GitHub Actions 两个 cron 去重 + 夏令时）。"""
    now = datetime.now(ZoneInfo(cfg.tz_name))
    sch = cfg.get("schedule", {}) or {}
    target = int(sch.get("hour", 15)) * 60 + int(sch.get("minute", 30))
    delta = now.hour * 60 + now.minute - target
    return (0 <= delta <= tolerance_min), f"{now.strftime('%H:%M')} vs 计划 {target // 60:02d}:{target % 60:02d}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="每日天文摄影推送")
    ap.add_argument("--dry-run", action="store_true", help="不发送，只打印与存档")
    ap.add_argument("--night", type=int, default=0, help="0=今晚，1=明晚")
    ap.add_argument("--json", action="store_true", help="额外输出 JSON")
    ap.add_argument("--no-save", action="store_true", help="不写本地存档")
    ap.add_argument("--test-push", action="store_true", help="发送一条测试消息")
    ap.add_argument("--if-due", action="store_true",
                    help="只有到点才跑（给 GitHub Actions 的多个 cron 去重、自动处理夏令时）")
    ap.add_argument("--tolerance", type=int, default=40,
                    help="--if-due 允许的延迟分钟数（CI 排队会晚几分钟）")
    ap.add_argument("--config", default=None, help="配置文件路径")
    args = ap.parse_args(argv)

    cfg = load_config(Path(args.config) if args.config else None)

    if args.if_due and not args.test_push:
        due, info = is_due(cfg, args.tolerance)
        if not due:
            print(f"[跳过] 未到推送时间（{info}），本次不运行")
            return 0

    if args.test_push:
        results = send_all(
            cfg,
            text=f"✅ 测试消息\n{cfg.site_name} 的天文摄影推送已配置成功。\n"
                 f"推送时间：每天 {cfg.get('schedule', {}).get('hour', 15):02d}:"
                 f"{cfg.get('schedule', {}).get('minute', 30):02d}",
            text_html=f"✅ <b>测试消息</b>\n{cfg.site_name} 的天文摄影推送已配置成功。",
            title="天文推送测试",
            dry_run=args.dry_run,
        )
        for r in results:
            print(f"[{r.channel}] {'OK' if r.ok else 'FAIL'} {r.detail}")
        print("已启用的通道：", cfg.channels)
        for k, v in cfg.secrets_present().items():
            print(f"  密钥 {k}: {'已配置' if v else '未配置'}")
        return 0 if all(r.ok for r in results) else 1

    try:
        report = collect(cfg, args.night)
    except Exception:
        traceback.print_exc()
        return 2

    text = build_text(report)
    html_text = build_telegram_html(report)

    print("=" * 64)
    print(text)
    print("=" * 64)

    if args.json:
        print(json.dumps(to_json(report), ensure_ascii=False, indent=2))

    if not args.no_save:
        path = save_archive(ROOT, report.night.day.isoformat(), build_markdown(report))
        print(f"[存档] {path}")

    results = send_all(cfg, text, html_text, report.headline, dry_run=args.dry_run)
    if results:
        for r in results:
            print(f"[推送:{r.channel}] {'OK' if r.ok else 'FAIL'} {r.detail}")
    else:
        print("[推送] 未配置任何远程通道（channels 里只有 file），只做了本地存档")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
