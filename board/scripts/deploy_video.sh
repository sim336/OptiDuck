#!/usr/bin/env bash
set -e
chmod +x /home/radxa/report_video.py
# 先干掉手工临时起过的实例，否则 systemd 启动时 8072 被占会进重启循环
pkill -f report_video.py 2>/dev/null || true
sleep 1
sudo cp /home/radxa/robot-video.service /etc/systemd/system/robot-video.service
sudo systemctl daemon-reload
sudo systemctl enable robot-video
sudo systemctl restart robot-video
sleep 3
echo "=== service status ==="
sudo systemctl is-active robot-video
echo "=== /health ==="
curl -s http://127.0.0.1:8072/health; echo
TOKEN=$(cat /home/radxa/robot_terminal_token)
echo "=== /stats ==="
curl -s "http://127.0.0.1:8072/stats?token=$TOKEN"; echo
echo "=== stream probe (frames in 3s) ==="
timeout 3 curl -s -N "http://127.0.0.1:8072/video?token=$TOKEN" | grep -c 'Content-Type: image/jpeg' || true
echo "=== /stats after probe ==="
curl -s "http://127.0.0.1:8072/stats?token=$TOKEN"; echo