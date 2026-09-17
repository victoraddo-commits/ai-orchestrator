#!/bin/sh
set -e
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip
mkdir -p /opt/kai-directory /var/lib/kai-directory
cp -r services/kai_directory /opt/kai-directory/kai_directory
python3 -m venv /opt/kai-directory/.venv
/opt/kai-directory/.venv/bin/pip install -q fastapi uvicorn
cp services/kai_directory/deploy/kai-directory.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now kai-directory.service
sleep 2
systemctl is-active kai-directory.service
curl -s -m 5 http://127.0.0.1:8097/health; echo
