#!/usr/bin/env bash
set -e
mkdir -p /usr/local/share/pgyvpn
cd /usr/local/share/pgyvpn
wget -q https://pgy.oray.com/softwares/58/download/1839/PgyVisitor_Raspberry_2.4.0.52291_arm64.deb -O pgy.deb
ls -lh pgy.deb
echo "--- dpkg install ---"
dpkg -i pgy.deb
echo "install exit=$?"