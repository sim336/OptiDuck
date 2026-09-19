#!/usr/bin/env bash
sudo -n true 2>&1
echo "sudo_no_pass_exit=$?"
id
echo "--- script present? ---"
ls -l /tmp/pgy_install.sh