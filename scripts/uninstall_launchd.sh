#!/usr/bin/env bash
# 卸载每日定时推送（可重复执行，不会报错）
set -euo pipefail
LABEL="com.astropush.daily"
DEST="${HOME}/Library/LaunchAgents/${LABEL}.plist"
UID_NUM="$(id -u)"

# 注意：变量后面跟中文/全角符号时必须用 ${VAR}，否则 bash 会把全角字符
# 当成变量名的一部分（这是之前 "$LABEL（" 报 unbound variable 的原因）。
if launchctl print "gui/${UID_NUM}/${LABEL}" >/dev/null 2>&1; then
    launchctl bootout "gui/${UID_NUM}/${LABEL}" 2>/dev/null || true
    echo "已停止 launchd 任务：${LABEL}"
else
    echo "launchd 里没有 ${LABEL}，无需停止"
fi

if [[ -f "${DEST}" ]]; then
    rm -f "${DEST}"
    echo "已删除：${DEST}"
else
    echo "plist 已不存在：${DEST}"
fi

echo "完成。本地代码、reports/ 存档和 logs/ 日志都保留，不受影响。"
