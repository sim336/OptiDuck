#!/usr/bin/env bash
set -e
chmod +x /home/radxa/robot_status.py
sudo cp /home/radxa/robot-status.service /etc/systemd/system/robot-status.service
sudo systemctl daemon-reload
sudo systemctl enable robot-status
sudo systemctl restart robot-status
sleep 2
echo "=== service status ==="
sudo systemctl is-active robot-status
echo "=== /health ==="
curl -s http://127.0.0.1:8070/health; echo
echo "=== /api/status ==="
curl -s http://127.0.0.1:8070/api/status; echo