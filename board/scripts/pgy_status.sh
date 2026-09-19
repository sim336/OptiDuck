#!/usr/bin/env bash
echo "--- showsets ---"
/usr/sbin/pgyvisitor showsets 2>&1 | head -20
echo "--- getmbrs ---"
/usr/sbin/pgyvisitor getmbrs 2>&1 | head -20