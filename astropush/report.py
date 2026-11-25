"""把分析结果渲染成推送文案。

同时产出：
  - Telegram HTML（<b> 等标签，长度 <= 4096）
  - 纯文本 / Markdown（飞书、企业微信、Server酱、邮件、本地存档通用）
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .config import Config
from .score import Event, Night, Suggestion
from .spaceweather import AuroraOutlook, SpaceWeather

WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
BAR = "▁▂▃▄▅▆▇█"


def _bar(score: float) -> str:
    idx = max(0, min(7, int(score / 100 * 8)))
    return BAR[idx]


def _moon_icon(illum: float, waxing: bool) -> str:
    if illum < 0.06:
        return "🌑"
    if illum < 0.44:
        return "🌒" if waxing else "🌘"
    if illum < 0.56:
        return "🌓" if waxing else "🌗"
    if illum < 0.94:
        return "🌔" if waxing else "🌖"
    return "🌕"


def pick_highlights(suggestions: list[Suggestion], n: int = 3) -> list[Suggestion]:
    """挑 3 条但不要全是同一类（别给 3 个星云）。"""
    picked: list[Suggestion] = []
    per_kind: dict[str, int] = {}
    for s in suggestions:
        if per_kind.get(s.kind, 0) >= 2:
            continue
        picked.append(s)
        per_kind[s.kind] = per_kind.get(s.kind, 0) + 1
        if len(picked) >= n:
            break
    return picked


def _hm(dt: datetime | None) -> str:
    return dt.strftime("%H:%M") if dt else "—"


@dataclass
class Report:
    cfg: Config
    night: Night
    suggestions: list[Suggestion]
    aurora: AuroraOutlook
    week: list[Night]
    events: list[Event]
    sw: SpaceWeather
    generated_at: datetime
    alerts: list[str] = field(default_factory=list)
    source_notes: list[str] = field(default_factory=list)
    best_advice: str = ""

    @property
    def tz(self):
        return ZoneInfo(self.cfg.tz_name)

    @property
    def headline(self) -> str:
        """完整标题，用于存档/邮件。"""
        n = self.night
        return f"今晚值得拍吗 · {self.cfg.site_name} · 评分 {n.grade}（{n.deep_sky_score:.0f}/100）"

    @property
    def push_title(self) -> str:
        """微信/Telegram 通知栏里那一行字：只放结论，控制在 30 字内。"""
        n = self.night
        day = f"{n.day.month}/{n.day.day}"
        if self.aurora.chance in ("肉眼可辨", "强爆发"):
            tail = f"有极光机会 Kp {self.aurora.kp_max:.0f}"
        elif n.grade in ("A", "B") and n.best_window:
            tail = f"{_hm(n.best_window[0])}–{_hm(n.best_window[1])} 晴朗"
        elif n.best_window:
            tail = f"只有 {_hm(n.best_window[0])}–{_hm(n.best_window[1])} 能拍"
        elif n.cloud_mean > 85:
            tail = f"云 {n.cloud_mean:.0f}%，别白跑"
        else:
            tail = "没有像样窗口"
        icons = {"A": "🟢", "B": "🟢", "C": "🟡", "D": "🔴"}
        return f"{day} 今晚{icons[n.grade]}{n.grade}级 · {tail}"


# --------------------------------------------------------------------------- #
# 纯文本
# --------------------------------------------------------------------------- #
def build_text(r: Report, max_chars: int = 3800) -> str:
    """纯文本版：给 Bark / 通用 webhook / 日志用。一行只放一个事实。"""
    n = r.night
    dow = WEEKDAYS[n.day.weekday()]
    L: list[str] = []

    L.append("🌌 今晚值得拍吗")
    L.append(f"{n.day.month}月{n.day.day}日 {dow} · {r.cfg.site_name}")
    L.append("")
    L.append(f"评分：{n.grade} 级（{n.deep_sky_score:.0f}/100）")
    L.append(f"结论：{n.grade_text}")
    if r.best_advice:
        L.append(r.best_advice)
    L.append("")

    L.append("⏱ 今晚的黑暗时间")
    if n.dusk and n.dawn:
        L.append(f"  天文暗夜  {_hm(n.dusk)} → {_hm(n.dawn)}")
        L.append(f"  持续      {n.dark_hours:.1f} 小时")
    else:
        L.append(f"  {n.dark_kind}，今夜没有真正暗夜")
    L.append(f"  最佳窗口  {_hm(n.best_window[0]) + '–' + _hm(n.best_window[1]) if n.best_window else '无'}")
    L.append("")

    L.append("🌙 月亮")
    L.append(f"  {n.moon_name}，照亮 {n.moon_illum * 100:.0f}%")
    L.append(f"  平均高度  {n.moon_alt_mean:.0f}°" if n.moon_alt_mean > 0 else "  今夜大部分时间在地平线下")
    L.append("")

    L.append("☁️ 天气")
    L.append(f"  云量均值  {n.cloud_mean:.0f}%（最差 {n.worst_cloud:.0f}%）")
    L.append(f"  透明度    {n.transparency * 100:.0f}%")
    L.append(f"  视宁度    {n.seeing * 100:.0f}%")
    if n.wind_max > 0:
        L.append(f"  最大风速  {n.wind_max:.0f} km/h")
    if n.dew_risk:
        L.append("  露水风险  高，记得带加热带")
    L.append("")

    L.append("🧲 极光")
    kp = f"{r.aurora.kp_max:.1f}" if r.aurora.kp_time else "—"
    L.append(f"  机会      {r.aurora.chance}")
    L.append(f"  Kp 预报   {kp}（本地相机可见需 ≥ {r.aurora.camera_kp:.1f}）")
    L.append(f"  太阳耀斑  {r.sw.flare_class}")
    L.append("")

    pool = [s for s in r.suggestions if s.kind in ("深空", "月亮", "银河", "行星", "流星雨")] or r.suggestions
    top = pick_highlights(pool, 3)
    L.append("📷 值得拍")
    if top:
        for i, s in enumerate(top, 1):
            L.append("")
            L.append(f"  {i}. {s.title}")
            if s.when:
                L.append(f"     时间：{s.when}")
            if s.where:
                L.append(f"     位置：{s.where}")
            if s.detail:
                L.append(f"     说明：{s.detail}")
    else:
        L.append("  今晚没有值得专门出门的目标")
    if any(s.kind == "深空" for s in top) and r.cfg.loc.get("bortle", 5) > 4:
        L.append("")
        L.append("  💡 市区光害（Bortle %s），深空建议周末去暗夜点" % r.cfg.loc.get("bortle"))
    L.append("")

    hi = [s for s in r.suggestions if s.kind in ("极光", "流星雨")]
    if hi or r.alerts:
        L.append("⚡ 提醒")
        for s in hi[:2]:
            L.append(f"  · {s.title}")
        for a in r.alerts[:3]:
            L.append(f"  · {a}")
        L.append("")

    if r.week:
        best = max(r.week, key=lambda w: w.deep_sky_score)
        L.append("📅 未来 7 夜")
        for w in r.week:
            star = "  ⭐本周最佳" if w.day == best.day else ""
            win = f"{_hm(w.best_window[0])}–{_hm(w.best_window[1])}" if w.best_window else "无窗口"
            L.append(
                f"  {w.day.month:02d}/{w.day.day:02d}  {w.grade} {w.deep_sky_score:3.0f}  "
                f"月照 {w.moon_illum * 100:2.0f}%  {win}{star}"
            )
        L.append("")

    if r.events:
        L.append("🔭 未来天象")
        for e in r.events[:8]:
            L.append(f"  {e.when.month:02d}/{e.when.day:02d}  {e.title}")
            if e.detail:
                L.append(f"            {e.detail}")
        L.append("")

    L.append(f"数据 Open-Meteo · 7Timer · NOAA SWPC")
    L.append(f"生成 {r.generated_at.strftime('%m-%d %H:%M')}")

    text = "\n".join(L)
    if len(text) > max_chars:
        text = text[: max_chars - 20].rstrip() + "\n…（已截断）"
    return text


# --------------------------------------------------------------------------- #
# Telegram HTML
# --------------------------------------------------------------------------- #
def build_telegram_html(r: Report) -> str:
    n = r.night
    e = html.escape
    dow = WEEKDAYS[n.day.weekday()]
    out: list[str] = []

    out.append(f"🌌 <b>{n.day.month}月{n.day.day}日 {dow} · {e(r.cfg.site_name)}</b>")
    out.append(
        f"今晚评分 <b>{n.grade}</b>（{n.grade_text}）"
        f"\n深空 {n.deep_sky_score:.0f} · 天气 {n.sky_score:.0f}"
    )

    if n.dusk and n.dawn:
        out.append(
            f"⏱ {e(n.dark_kind)} <b>{_hm(n.dusk)} → {_hm(n.dawn)}</b>"
            f"（{n.dark_hours:.1f}h）"
        )
    else:
        out.append(f"⏱ {e(n.dark_kind)}：今夜无真正暗夜")
    if n.best_window:
        out.append(f"✅ 最佳窗口 <b>{_hm(n.best_window[0])}–{_hm(n.best_window[1])}</b>")
    else:
        out.append("⚠️ 今晚没有「云少 + 月光可接受」的整点窗口")

    out.append(
        f"🌙 {e(n.moon_name)} {n.moon_illum * 100:.0f}%"
        + (f"・平均高度 {n.moon_alt_mean:.0f}°" if n.moon_alt_mean > 0 else "・大部时间在地平线下")
    )
    out.append(
        f"☁️ 云 {n.cloud_mean:.0f}%（最差 {n.worst_cloud:.0f}%）"
        f"・透 {n.transparency * 100:.0f}%・视宁 {n.seeing * 100:.0f}%"
    )
    if n.wind_max >= 25:
        out.append(f"💨 阵风 {n.wind_max:.0f} km/h")
    kp_txt = f"{r.aurora.kp_max:.1f}" if r.aurora.kp_time else "—"
    out.append(
        f"🧲 极光 {e(r.aurora.chance)}（Kp {kp_txt}，"
        f"本纬度相机需 ≥{r.aurora.camera_kp:.1f}）・耀斑 {e(r.sw.flare_class)}"
    )
    if r.best_advice:
        out.append(f"<b>{e(r.best_advice)}</b>")

    # 拍什么
    out.append("\n<b>📷 值得拍</b>")
    pool = [s for s in r.suggestions if s.kind in ("深空", "月亮", "银河", "行星", "流星雨")] \
        or r.suggestions
    top = pick_highlights(pool, 3)
    if top:
        for i, s in enumerate(top, 1):
            line = f"{i}. <b>{e(s.title)}</b>"
            meta = "｜".join(x for x in (s.when, s.where) if x)
            if meta:
                line += f"\n    {e(meta)}"
            if s.detail:
                line += f"\n    <i>{e(s.detail)}</i>"
            out.append(line)
    else:
        out.append("1. 今晚没有值得专门出门的目标，补觉或整理素材")

    hi = [s for s in r.suggestions if s.kind in ("极光", "流星雨")]
    if hi or r.alerts:
        out.append("\n<b>⚡ 提醒</b>")
        for s in hi[:2]:
            out.append(f"· <b>{e(s.title)}</b>" + (f" {e(s.detail)}" if s.detail else ""))
        for a in r.alerts[:3]:
            out.append(f"· {e(a)}")

    if r.week:
        out.append("\n<b>📅 未来 7 夜</b>")
        rows = []
        for w in r.week:
            win = f"{_hm(w.best_window[0])}–{_hm(w.best_window[1])}" if w.best_window else "无窗口"
            rows.append(
                f"{w.day.month:02d}/{w.day.day:02d} <code>{w.grade}{_bar(w.deep_sky_score)}"
                f"{w.deep_sky_score:3.0f}</code> {win}"
            )
        out.append("\n".join(rows))

    if r.events:
        out.append("\n<b>🔭 未来天象</b>")
        for ev in r.events[:8]:
            line = f"{ev.when.month:02d}/{ev.when.day:02d} <b>{e(ev.title)}</b>"
            if ev.detail:
                line += f"\n    <i>{e(ev.detail)}</i>"
            out.append(line)

    out.append(
        f"\n<i>数据 Open-Meteo·7Timer·NOAA SWPC｜{r.generated_at.strftime('%m-%d %H:%M')}</i>"
    )

    text = "\n".join(out)
    if len(text) > 4000:
        text = text[:3960].rsplit("\n", 1)[0] + "\n<i>…已截断</i>"
    return text


def build_markdown(r: Report, max_chars: int = 3900) -> str:
    """Server酱 / 飞书 / 企业微信 的 Markdown 版。

    微信详情页很窄，所以：小节之间空行、每个事实单独一行、目标用编号 + 子项。
    """
    n = r.night
    dow = WEEKDAYS[n.day.weekday()]
    L: list[str] = []

    L.append("## 🌌 今晚值得拍吗")
    L.append("")
    L.append(f"**{n.day.month}月{n.day.day}日 {dow} · {r.cfg.site_name}**")
    L.append("")
    L.append(f"**评分：{n.grade} 级（{n.deep_sky_score:.0f}/100）**")
    L.append("")
    L.append(f"{n.grade_text}")
    if r.best_advice:
        L.append("")
        L.append(r.best_advice)
    L.append("")

    L.append("### ⏱ 今晚的黑暗时间")
    L.append("")
    if n.dusk and n.dawn:
        L.append(f"- 天文暗夜：**{_hm(n.dusk)} → {_hm(n.dawn)}**")
        L.append(f"- 持续：{n.dark_hours:.1f} 小时")
    else:
        L.append(f"- {n.dark_kind}，今夜没有真正暗夜")
    if n.best_window:
        L.append(f"- 最佳窗口：**{_hm(n.best_window[0])}–{_hm(n.best_window[1])}**")
    else:
        L.append("- 最佳窗口：**无**（没有连续 1 小时的好条件）")
    L.append("")

    L.append("### 🌙 月亮")
    L.append("")
    L.append(f"- 月相：{n.moon_name}")
    L.append(f"- 照亮：{n.moon_illum * 100:.0f}%")
    if n.moon_alt_mean > 0:
        L.append(f"- 平均高度：{n.moon_alt_mean:.0f}°")
    else:
        L.append("- 今夜大部分时间在地平线下")
    L.append("")

    L.append("### ☁️ 天气")
    L.append("")
    L.append(f"- 云量均值：{n.cloud_mean:.0f}%（最差 {n.worst_cloud:.0f}%）")
    L.append(f"- 透明度：{n.transparency * 100:.0f}%")
    L.append(f"- 视宁度：{n.seeing * 100:.0f}%")
    if n.wind_max > 0:
        L.append(f"- 最大风速：{n.wind_max:.0f} km/h")
    if n.dew_risk:
        L.append("- ⚠️ 露水风险高，带加热带")
    L.append("")

    L.append("### 🧲 极光")
    L.append("")
    kp = f"{r.aurora.kp_max:.1f}" if r.aurora.kp_time else "—"
    L.append(f"- 机会：{r.aurora.chance}")
    L.append(f"- Kp 预报：{kp}（本地相机可见需 ≥ {r.aurora.camera_kp:.1f}）")
    L.append(f"- 太阳耀斑：{r.sw.flare_class}")
    L.append("")

    pool = [s for s in r.suggestions if s.kind in ("深空", "月亮", "银河", "行星", "流星雨")] or r.suggestions
    top = pick_highlights(pool, 3)
    L.append("### 📷 值得拍")
    L.append("")
    if top:
        for i, s in enumerate(top, 1):
            L.append(f"**{i}. {s.title}**")
            L.append("")
            if s.when:
                L.append(f"- 时间：{s.when}")
            if s.where:
                L.append(f"- 位置：{s.where}")
            if s.detail:
                L.append(f"- 说明：{s.detail}")
            L.append("")
        if any(s.kind == "深空" for s in top) and r.cfg.loc.get("bortle", 5) > 4:
            L.append("> 💡 市区光害（Bortle %s），深空建议周末去暗夜点" % r.cfg.loc.get("bortle"))
            L.append("")
    else:
        L.append("今晚没有值得专门出门的目标，补觉或整理素材。")
        L.append("")

    hi = [s for s in r.suggestions if s.kind in ("极光", "流星雨")]
    if hi or r.alerts:
        L.append("### ⚡ 提醒")
        L.append("")
        for s in hi[:2]:
            L.append(f"- **{s.title}**")
        for a in r.alerts[:3]:
            L.append(f"- {a}")
        L.append("")

    if r.week:
        best = max(r.week, key=lambda w: w.deep_sky_score)
        L.append("### 📅 未来 7 夜")
        L.append("")
        for w in r.week:
            star = " · ⭐本周最佳" if w.day == best.day else ""
            win = f"{_hm(w.best_window[0])}–{_hm(w.best_window[1])}" if w.best_window else "无窗口"
            L.append(
                f"- **{w.day.month:02d}/{w.day.day:02d}（{w.grade} {w.deep_sky_score:.0f}）**  "
                f"月照 {w.moon_illum * 100:.0f}% · {win}{star}"
            )
        L.append("")

    if r.events:
        L.append("### 🔭 未来天象")
        L.append("")
        for e in r.events[:8]:
            line = f"- **{e.when.month:02d}/{e.when.day:02d}** {e.title}"
            if e.detail:
                line += f" — {e.detail}"
            L.append(line)
        L.append("")

    L.append("---")
    L.append("")
    L.append("数据来源：Open-Meteo · 7Timer · NOAA SWPC")
    L.append("")
    L.append(f"生成时间：{r.generated_at.strftime('%m-%d %H:%M')}")

    text = "\n".join(L)
    if len(text) > max_chars:
        text = text[: max_chars - 20].rsplit("\n", 1)[0] + "\n…（已截断）"
    return text
