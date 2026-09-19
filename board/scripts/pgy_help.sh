#!/usr/bin/env bash
command -v pgyvisitor
echo "--- version ---"
pgyvisitor -v 2>&1 | head -5
echo "--- login help ---"
pgyvisitor login -h 2>&1 | head -30