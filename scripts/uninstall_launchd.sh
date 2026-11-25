#!/usr/bin/env bash
# 卸载每日定时推送
set -euo pipefail
LABEL="com.astropush.daily"
DEST="$HOME/Library/LaunchAgents/$LABEL.plist"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$DEST"
echo "已卸载 $LABEL（本地代码、报告与日志保留）"
