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

# 4. Install Python package
echo "[INFO] Installing Python package..."
python3 -m pip install "$REPO_DIR"

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
echo "2. Test configuration: weddinghub-photobooth status -c $CONFIG_FILE"
echo "3. Enable and start:   sudo systemctl enable --now weddinghub-photobooth-uploader.service"
echo "4. Monitor logs:       journalctl -u weddinghub-photobooth-uploader.service -f"
