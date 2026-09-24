#!/usr/bin/env bash
# WeddingHub Photobooth Uploader Installation Helper Script
# Usage: sudo ./scripts/install.sh

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
   echo "[ERROR] This installation script must be run as root (e.g. using sudo)." >&2
   exit 1
fi

echo "=== WeddingHub Photobooth Uploader Installer ==="

# 1. Ensure photobooth user exists
PHOTO_USER="photobooth"
if ! id -u "$PHOTO_USER" >/dev/null 2>&1; then
    echo "[INFO] Creating service user '$PHOTO_USER'..."
    useradd -r -s /usr/sbin/nologin -d /var/lib/photobooth "$PHOTO_USER" || true
else
    echo "[OK] Service user '$PHOTO_USER' already exists."
fi

# 2. Create directory paths
echo "[INFO] Setting up directories..."
CONFIG_DIR="/etc/weddinghub-photobooth"
DATA_DIR="/var/lib/weddinghub-photobooth"
PHOTOS_DIR="/var/lib/photobooth/photos"

mkdir -p "$CONFIG_DIR"
mkdir -p "$DATA_DIR"
mkdir -p "$PHOTOS_DIR"

chown -R "$PHOTO_USER:$PHOTO_USER" "$DATA_DIR"
chmod 750 "$DATA_DIR"

# 3. Copy example configuration if config.toml doesn't exist
CONFIG_FILE="$CONFIG_DIR/config.toml"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"

if [[ ! -f "$CONFIG_FILE" ]]; then
    echo "[INFO] Installing default configuration from config.example.toml to $CONFIG_FILE..."
    cp "$REPO_DIR/config.example.toml" "$CONFIG_FILE"
    chown root:"$PHOTO_USER" "$CONFIG_FILE"
    chmod 640 "$CONFIG_FILE"
    echo "[IMPORTANT] Please edit $CONFIG_FILE to set your real 'device_token' and 'watch_directory'."
else
    echo "[OK] Existing configuration file found at $CONFIG_FILE."
fi

# 4. Pre-flight checks
echo "[INFO] Running Python pre-flight checks..."
if ! command -v python3 >/dev/null 2>&1; then
    echo "[ERROR] python3 is not installed. Please install Python 3.11+." >&2
    exit 1
fi

PY_VERSION=$(python3 -c 'import sys; print("%02d%02d" % (sys.version_info.major, sys.version_info.minor))')
if [ "$PY_VERSION" -lt 311 ]; then
    echo "[ERROR] Python version must be >= 3.11." >&2
    exit 1
fi

VENV_TEST_DIR=$(mktemp -d)
if ! python3 -m venv "$VENV_TEST_DIR" >/dev/null 2>&1; then
    echo "[ERROR] Failed to create a test virtual environment." >&2
    echo "Please ensure python3-venv is correctly installed, e.g.: sudo apt install python3-venv" >&2
    rm -rf "$VENV_TEST_DIR"
    exit 1
fi
rm -rf "$VENV_TEST_DIR"

# 5. Install Python package in dedicated virtual environment (PEP 668 compliant)
APP_DIR="/opt/weddinghub-photobooth"
VENV_DIR="$APP_DIR/venv"

echo "[INFO] Setting up virtual environment at $VENV_DIR..."
mkdir -p "$APP_DIR"
if [[ ! -d "$VENV_DIR" ]]; then
    echo "[INFO] Creating dedicated Python virtual environment..."
    python3 -m venv "$VENV_DIR"
else
    echo "[OK] Existing virtual environment found at $VENV_DIR."
fi

echo "[INFO] Installing/updating package in dedicated virtual environment..."
"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install "$REPO_DIR"

# Symlink executable to /usr/local/bin for interactive CLI usage
mkdir -p /usr/local/bin
ln -sf "$VENV_DIR/bin/weddinghub-photobooth" /usr/local/bin/weddinghub-photobooth
echo "[OK] CLI symlinked to /usr/local/bin/weddinghub-photobooth."

# 5. Install systemd service
SERVICE_SRC="$REPO_DIR/systemd/weddinghub-photobooth-uploader.service"
SERVICE_DEST="/etc/systemd/system/weddinghub-photobooth-uploader.service"

echo "[INFO] Installing systemd service unit to $SERVICE_DEST..."
cp "$SERVICE_SRC" "$SERVICE_DEST"
chmod 644 "$SERVICE_DEST"

systemctl daemon-reload
echo "[OK] systemd daemon reloaded."

echo ""
echo "=== Installation Finished Successfully ==="
echo "Next steps:"
echo "1. Edit configuration: sudo nano $CONFIG_FILE"
echo "2. Test connectivity:  weddinghub-photobooth -c $CONFIG_FILE test"
echo "3. Check queue status: weddinghub-photobooth -c $CONFIG_FILE status"
echo "4. Enable and start:   sudo systemctl enable --now weddinghub-photobooth-uploader.service"
echo "5. Monitor logs:       journalctl -u weddinghub-photobooth-uploader.service -f"
