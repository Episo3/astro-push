#!/usr/bin/env bash
# 安装每日定时推送（macOS launchd）
#   bash scripts/install_launchd.sh            # 用 config.json 里的 15:30
#   bash scripts/install_launchd.sh 16 5       # 指定 16:05
set -euo pipefail

PROJ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.astropush.daily"
DEST="${HOME}/Library/LaunchAgents/${LABEL}.plist"
PY="$(command -v python3)"
UID_NUM="$(id -u)"
HOUR="${1:-}"
MINUTE="${2:-}"

if [[ -z "${HOUR}" ]]; then
    HOUR="$(cd "${PROJ}" && "${PY}" -c 'import json;print(json.load(open("config.json"))["schedule"]["hour"])')"
fi
if [[ -z "${MINUTE}" ]]; then
    MINUTE="$(cd "${PROJ}" && "${PY}" -c 'import json;print(json.load(open("config.json"))["schedule"]["minute"])')"
fi

mkdir -p "${PROJ}/logs" "${HOME}/Library/LaunchAgents"

# 先跑一次，确认脚本本身是通的
echo "==> 自检：先手动跑一次（不发送）"
if ( cd "${PROJ}" && "${PY}" run.py --dry-run --no-save >/dev/null 2>&1 ); then
    echo "    通过"
else
    echo "    失败，请先手动执行：cd ${PROJ} && python3 run.py --dry-run"
    exit 1
fi

cat > "${DEST}" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>${PY}</string>
        <string>${PROJ}/run.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>${PROJ}</string>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>${HOUR}</integer>
        <key>Minute</key>
        <integer>${MINUTE}</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>${PROJ}/logs/launchd.out.log</string>
    <key>StandardErrorPath</key>
    <string>${PROJ}/logs/launchd.err.log</string>
    <key>RunAtLoad</key>
    <false/>
    <key>ProcessType</key>
    <string>Background</string>
</dict>
</plist>
PLIST

# 已存在就先卸载，避免 bootstrap 报 "already loaded"
launchctl bootout "gui/${UID_NUM}/${LABEL}" 2>/dev/null || true
launchctl bootstrap "gui/${UID_NUM}" "${DEST}"
launchctl enable "gui/${UID_NUM}/${LABEL}"

echo "==> 已安装：每天 $(printf '%02d:%02d' "${HOUR}" "${MINUTE}") 推送"
echo "    plist : ${DEST}"
echo "    日志  : ${PROJ}/logs/launchd.out.log"
echo "    卸载  : bash ${PROJ}/scripts/uninstall_launchd.sh"
