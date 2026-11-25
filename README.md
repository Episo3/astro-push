# 今晚值得拍吗 · 每日天文摄影推送

每天下午自动汇总 **天气 + 月相 + 行星 + 流星雨 + 极光 + 深空目标**，判断「今晚值不值得出摊、拍什么、几点拍」，推到 Telegram / 飞书 / 企业微信 / 微信（Server酱）/ iOS（Bark）。

- **零第三方依赖**：太阳、月亮、行星、晨昏、升落、中天全部用纯标准库自算（Meeus 算法），不需要 astropy/ephem，也不会因为 pip 装不上而挂
- **精度已验证**：行星 <1 角分（对照 JPL Horizons）、月相 ±1 分钟（对照 2024 北美日全食时刻）、日出日落秒级 / 月升月落秒级（对照 MET Norway）
- **会做判断，不只是罗列数据**：100% 云量的夜晚不会给你推深空目标，会直接说「今晚没戏，本周最佳是周六」

---

## 长这样

```
🌌 11月25日 周三 · Madison, WI
今晚评分 D｜很差 · 建议不出摊｜深空 13 · 天气 16

⏱ 天文暗夜 18:06 → 05:23（11.3 小时）
⚠️ 今晚没有一个小时是同时「云少 + 月光可接受」的
🌙 亏凸月 照亮 96%，整夜平均高度 49°
☁️ 云量均值 100%（最差 100%）・透明度 36%・视宁度 65%
💨 最大风速 19 km/h
🧲 极光 很低（预报 Kp 3.3，本纬度相机需 ≥3.9）・耀斑 B2.4
🗓 本周最佳出摊夜：11/29（C 66 分，窗口 18:05–05:35，月照 61%）

📷 值得拍
1. 月面：亏凸月 96%
   月出/月落前后 1 小时｜月亮平均高度 49°
2. 木星：高度 60°  05:06
3. 火星：高度 59°  05:06

⚡ 提醒
· 满月整夜在天上，深空/银河基本没戏，改拍月面或地景月光
· 今晚没有连续 1 小时的好窗口，建议看实时卫星云图再决定

📅 未来 7 夜
11/25 D▂  13 🌕96% 无窗口
11/29 C▆  66 🌖61% 18:05-05:35
...

🔭 未来天象
12/09 新月 — 深空/银河最佳窗口
12/14 双子座流星雨极大（ZHR 150）— 月相 23%，条件友好
12/27 木星合月（1.8°）
```

好夜晚的版本会给出 `A 优秀`、连续最佳窗口、按「高度 × 月光分离度 × 亮度 × 波段」排好的 3 个深空目标，以及方位/高度。

---

## 三步上手

### 1. 填推送密钥

```bash
cd astro_push
cp .env.example .env
# 编辑 .env，填 Telegram 或飞书 webhook
```

当前 `config.json` 已配成 **Server酱**：`.env` 里填 `SERVERCHAN_KEY=你的SendKey` 即可（微信扫码登录 [sct.ftqq.com](https://sct.ftqq.com) 获取）。

也支持其它通道，改 `.env` + `config.json` 的 `webhook.type`：

| 通道 | `.env` 变量 | `webhook.type` | 说明 |
|---|---|---|---|
| Server酱（微信） | `SERVERCHAN_KEY` | `serverchan` | 兼容 Turbo 与 Server酱3 |
| 飞书群机器人 | `FEISHU_WEBHOOK` | `feishu` | 群设置 → 机器人 → 自定义机器人 |
| 企业微信 | `WECOM_WEBHOOK` | `wecom` | 群机器人 webhook |
| Bark（iOS） | `BARK_URL` | `bark` | 形如 `https://api.day.app/KEY` |
| Telegram | `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` | — | 通道名 `telegram`，需在 `channels` 里加上 |

### 2. 验证

```bash
python3 run.py --test-push     # 只发一条测试消息
python3 run.py --dry-run       # 看完整报告，不发
```

### 3. 装定时（每天 15:30）

```bash
bash scripts/install_launchd.sh          # 或 bash scripts/install_launchd.sh 16 5
```

卸载：`bash scripts/uninstall_launchd.sh`

> 笔记本休眠时 launchd 不跑，**醒来后会补跑一次**；不保证 15:30 准点。要真正 24h 在线，放到常开的机器/服务器上跑同一个 `run.py` 即可（无 GUI 依赖）。

---

## 不开电脑也能推送（推荐：GitHub Actions）

推送本身只是「往 Server酱 发一个 HTTPS 请求」，所以真正要解决的是**谁来在 15:30 执行它**。四种选法：

| 方案 | 成本 | 准点性 | 适合 |
|---|---|---|---|
| **GitHub Actions**（推荐） | 免费 | 偶发延迟 5–30 分钟 | 没有常开设备的人 |
| 常开的 NAS / 树莓派 / 小主机 | 已有设备 | 几乎准点 | 家里已有 NAS 的 |
| 云函数（阿里云 FC / 腾讯云 SCF） | 免费额度 | 准点，可设时区 | 想全在国内网络 |
| 本机 launchd | 免费 | 电脑睡眠就不跑 | 已装好的本地版 |

### 1. 拿 Server酱 SendKey

微信扫码登录 [sct.ftqq.com](https://sct.ftqq.com) → 复制 **SendKey**（免费版每天 5 条，一天 1 条足够）。

本项目同时兼容两代 Key，直接粘进去就行：

- `SCT` 开头 → Turbo，走 `sctapi.ftqq.com`
- `sctp{数字}t…` 开头 → Server酱3，自动走 `{数字}.push.ft07.com`

### 2. 把代码推到一个 GitHub 仓库

```bash
cd astro_push
git init -b main
git add .
git commit -m "每日天文推送"
# 先在 GitHub 网页建一个空仓库（public/private 都行），然后：
git remote add origin git@github.com:<你的用户名>/astro-push.git
git push -u origin main
```

> `.env` 已在 `.gitignore` 里，**不要把 Key 提交进仓库**。

### 3. 配置 Secret

仓库 → Settings → Secrets and variables → Actions → **New repository secret**：

- Name: `SERVERCHAN_KEY`
- Value: 你的 SendKey

### 4. 测试

仓库 → Actions → 左侧「每日天文推送」→ **Run workflow**。几分钟内微信应该收到。

### 5. 之后

每天自动跑，`reports/` 会作为 artifact 保留 30 天。

**时区怎么处理的**：GitHub 的 cron 只认 UTC，而芝加哥夏令时/冬令时差 1 小时。所以工作流挂了两个 cron（20:30 UTC 与 21:30 UTC），脚本用 `--if-due` 检查本地时间，只有对的那个会真正执行——换城市/换时区不用改 cron。

**两个小提醒**：

- GitHub 的定时任务可能延迟几分钟到半小时，`--if-due` 给了 40 分钟容差；对「今晚拍什么」这种用途完全够
- public 仓库若 60 天没有任何提交，GitHub 会自动暂停定时任务，偶尔提交一次即可

### 用常开设备（NAS/树莓派）代替

```bash
# 在设备上放好代码并填 .env，然后：
crontab -e
30 15 * * * cd /path/to/astro_push && /usr/bin/python3 run.py >> logs/cron.log 2>&1
```

脚本零第三方依赖，只要 Python 3.9+ 就能跑，ARM 也支持。

---

## 配置（config.json）

| 字段 | 说明 |
|---|---|
| `location.name/lat/lon/tz` | 观测点。换城市只改这四项（`tz` 必须是 IANA 时区名） |
| `location.bortle` | 光害等级，≤4 时报告不再提示「去暗夜点」 |
| `schedule.hour/minute` | 推送时间，安装脚本默认读这里 |
| `channels` | `file`（本地存档）/ `telegram` / `webhook` |
| `webhook.type` | `feishu` / `wecom` / `serverchan` / `bark` / `generic` |
| `thresholds.min_target_alt` | 目标最低高度（默认 20°，低于此不算「能拍」） |
| `thresholds.aurora_kp_min` | 极光提示阈值（实际按地磁纬度动态算，这是兜底） |
| `dark_sites` | 附近暗夜点，报告里可引用 |

也支持环境变量覆盖地点：`SITE_LAT` / `SITE_LON` / `SITE_NAME` / `SITE_TZ`。

---

## 评分怎么算的

**天气分 `sky_score`**
`0.66 × 有效云量 + 0.20 × 透明度 + 0.14 × 视宁度`

有效云量不是简单平均：低云 ×1.0、中云 ×0.75、薄高云 ×0.35（低云最致命）。降水概率 ≥60% 时该时段清晰度 ×0.2，有雾 ×0.45。

**深空分 `deep_sky`** 在此基础上叠加月光惩罚：
`clear × (1 − 0.62 × 月照比例 × √(月亮高度/25))`

**等级**：A ≥85 / B ≥70 / C ≥50 / D <50。

**目标排序**：`(0.55×高度分 + 0.25×亮度分 + 0.2) × 月光分离度 × 波段惩罚 × 当晚深空分`
其中「宽带」目标（星系/反射星云）怕月光，「窄带」目标（Hα/OIII）抗月光，惩罚不同。

**极光**：用偶极子近似算本地地磁纬度（Madison ≈ 51.9°），按「极光卵赤道边界 ≈ 66.5° − 3.2×Kp」反推相机可见/肉眼可辨所需的 Kp，再和 NOAA 的 3 天 Kp 预报比对。

---

## 数据源

| 用途 | 来源 | 说明 |
|---|---|---|
| 云量分层/降水/能见度/温湿风 | [Open-Meteo](https://open-meteo.com/) | 免 key，7 天逐小时 |
| 视宁度 seeing / 透明度 | [7Timer!](https://www.7timer.info/) astro | 免 key，仅 3 天、3 小时步长 |
| Kp 地磁预报 / X 射线耀斑 / 太阳风 | [NOAA SWPC](https://services.swpc.noaa.gov/) | 免 key |
| 天文历 | 本地计算 | Meeus，纯标准库 |

深空目录、流星雨表在 `astropush/targets.py`，是人工整理的经典清单 —— **想加目标直接往里加一行**（赤经用度 = 小时 × 15）。

---

## 目录

```
astro_push/
├── run.py                  # 入口：抓数据 → 评分 → 渲染 → 推送
├── config.json             # 地点/时间/通道/阈值
├── .env                    # 密钥（.env.example 是模板）
├── astropush/
│   ├── astro.py            # 太阳/月亮/行星/晨昏/升落/中天（纯计算）
│   ├── weather.py          # Open-Meteo + 7Timer
│   ├── spaceweather.py     # NOAA SWPC + 极光推断
│   ├── targets.py          # 深空目录 / 流星雨 / 暗夜点
│   ├── score.py            # 评分引擎 + 目标排序 + 天象日历
│   ├── report.py           # 文案（Telegram HTML / 纯文本）
│   ├── notify.py           # 5 个推送通道
│   └── config.py           # 配置 + .env 加载
├── .github/workflows/daily.yml  # 不开电脑：GitHub Actions 定时推送
├── scripts/                # launchd 安装/卸载
├── tests/                  # 25 项回归测试（对照 JPL/MET/Open-Meteo）
├── reports/                # 每日存档 Markdown
└── logs/                   # launchd 日志
```

## 常用命令

```bash
python3 run.py                 # 正常生成并推送
python3 run.py --dry-run       # 不发，只看
python3 run.py --night 1       # 看明晚
python3 run.py --json          # 输出结构化 JSON（接别的自动化）
python3 run.py --if-due        # 到点才跑（CI 去重/夏令时）
python3 -m unittest discover -s tests -v   # 跑测试
```

---

## 已知限制

- **7Timer 只给 3 天** 的 seeing/透明度，第 4-7 天用湿度/风速估算，报告里不夸大
- **没有彗星、ISS 过境、日食月食**：彗星亮度变化大，未见可靠免 key 接口；ISS 需要 SGP4，暂未接。`score.phenomena_calendar()` 里留了扩展位
- **极光是概率判断**，NOAA 的 Kp 是 3 小时分段预报，实际还要看 IMF Bz 和云；报告给的是「值不值得等」而不是承诺
- **Bortle 与暗夜点是社区估算**，请在 `config.json` 里按你自己经验改
- 深空坐标是 J2000 近似，用于排序/选址没问题，**实际指向请用 Stellarium/ASIAIR**
- GitHub Actions 的 cron 有几分钟到半小时的排队延迟；Server酱 免费版每天限 5 条
