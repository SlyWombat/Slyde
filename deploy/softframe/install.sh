#!/usr/bin/env bash
# Install the Memento Soft Frame on a Raspberry Pi (Pi OS Lite / Debian, no desktop).
# Usage:  sudo ./install.sh [git-repo-url]
set -euo pipefail

APP=/opt/memento-frame
DATA=/var/lib/memento-frame
REPO="${1:-https://github.com/SlyWombat/memento-manager.git}"
HERE="$(cd "$(dirname "$0")" && pwd)"

apt-get update
apt-get install -y python3 python3-venv git \
    libsdl2-2.0-0 libsdl2-image-2.0-0 libdrm2 libgbm1

id memento &>/dev/null || useradd --system --create-home --groups video,render,input memento
install -d -o memento -g memento "$APP" "$DATA"

# Build a venv and install core + emulator with the 'display' extra (pulls pygame/SDL).
tmp="$(mktemp -d)"
git clone --depth 1 "$REPO" "$tmp"
sudo -u memento python3 -m venv "$APP/venv"
sudo -u memento "$APP/venv/bin/pip" install --upgrade pip
sudo -u memento "$APP/venv/bin/pip" install \
    "$tmp/packages/memento-core" "$tmp/packages/memento-emulator[display]"
rm -rf "$tmp"

install -m 644 "$HERE/memento-frame.service" /etc/systemd/system/memento-frame.service
systemctl daemon-reload
systemctl enable --now memento-frame.service

echo "Memento Soft Frame installed and started."
echo "  logs:   journalctl -u memento-frame -f"
echo "  data:   $DATA   (config/photos persist here)"
echo "It should now appear in the Manager (by LAN discovery, or add its IP to FRAME_HOSTS)."
