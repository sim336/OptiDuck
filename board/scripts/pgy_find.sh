#!/usr/bin/env bash
echo "--- find binary ---"
find / -name 'pgyvisitor*' -type f 2>/dev/null | head -20
echo "--- dpkg files ---"
dpkg -L pgyvpn 2>/dev/null | head -40
echo "--- which via locate ---"
find /usr -maxdepth 4 -iname '*pgy*' 2>/dev/null | head -20