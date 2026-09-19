#!/usr/bin/env bash
set -e
echo "=== 1. download ==="
sudo mkdir -p /usr/local/share/pgyvpn
sudo -n true
# upload password via sudo -S
echo | true