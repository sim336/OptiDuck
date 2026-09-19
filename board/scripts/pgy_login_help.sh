#!/usr/bin/env bash
echo "--- version ---"
/usr/sbin/pgyvisitor -v 2>&1 | head -5
echo "--- login help ---"
/usr/sbin/pgyvisitor login -h 2>&1 | head -40