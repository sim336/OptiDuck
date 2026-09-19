#!/usr/bin/env bash
echo "--- login ---"
/usr/sbin/pgyvisitor login -u "21563164:004" -p "315207liu" 2>&1 | head -20
echo "login_exit=$?"
echo "--- autologin ---"
/usr/sbin/pgyvisitor autologin -y 2>&1 | head -10
echo "--- status ---"
/usr/sbin/pgyvisitor showsets 2>&1 | head -30