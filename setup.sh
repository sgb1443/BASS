#!/bin/bash

# BASS Setup Script
# Run this once after cloning the repo to configure the systemd service

USERNAME=$(whoami)
HOME_DIR=$(eval echo ~$USERNAME)
REPO_DIR="$HOME_DIR/BASS"
BASS_SERVICE="/etc/systemd/system/bass.service"
MEDIAMTX_SERVICE="/etc/systemd/system/mediamtx.service"
CONFIG="/boot/firmware/config.txt"

echo "==============================="
echo " BASS Setup"
echo " User: $USERNAME"
echo " Repo: $REPO_DIR"
echo "==============================="

# --- Preflight checks ---
if [ ! -f "$CONFIG" ]; then
    echo "ERROR: $CONFIG not found. Are you on a Pi?"
    exit 1
fi

if [ ! -d "$REPO_DIR" ]; then
    echo "ERROR: $REPO_DIR not found. Clone the repo first."
    exit 1
fi

# --- Backup boot config before touching it ---
echo "[0/6] Backing up $CONFIG..."
sudo cp "$CONFIG" "${CONFIG}.bak.$(date +%Y%m%d_%H%M%S)"
echo "  Backup saved as ${CONFIG}.bak.*"

# --- Enable camera ---
echo "[1/6] Enabling camera..."

# Only replace if the exact line exists — don't blindly sed
if grep -q 'camera_auto_detect=1' "$CONFIG"; then
    sudo sed -i 's/camera_auto_detect=1/camera_auto_detect=0/' "$CONFIG"
    echo "  Set camera_auto_detect=0"
else
    echo "  camera_auto_detect=1 not found, skipping (already 0 or not set)"
fi

# Only append if not already present
grep -qxF 'start_x=1' "$CONFIG" || { echo 'start_x=1' | sudo tee -a "$CONFIG" > /dev/null; echo "  Added start_x=1"; }
grep -qxF 'gpu_mem=128' "$CONFIG" || { echo 'gpu_mem=128' | sudo tee -a "$CONFIG" > /dev/null; echo "  Added gpu_mem=128"; }

# --- Enable I2C ---
echo "[2/6] Enabling I2C..."
sudo raspi-config nonint do_i2c 0
echo "  I2C enabled"

# --- Write BASS web app service ---
echo "[3/6] Configuring BASS web app service..."
sudo bash -c "cat > $BASS_SERVICE" << EOF
[Unit]
Description=BASS Web App
After=network.target mediamtx.service

[Service]
ExecStart=/usr/bin/python3 $REPO_DIR/my_app.py
WorkingDirectory=$REPO_DIR
Restart=always
RestartSec=3
User=$USERNAME

[Install]
WantedBy=multi-user.target
EOF
echo "  Written: $BASS_SERVICE"

# --- Write MediaMTX service ---
echo "[4/6] Configuring MediaMTX camera stream service..."
sudo bash -c "cat > $MEDIAMTX_SERVICE" << EOF
[Unit]
Description=MediaMTX Camera Stream
After=network.target

[Service]
ExecStart=$REPO_DIR/mediamtx $REPO_DIR/mediamtx.yml
WorkingDirectory=$REPO_DIR
Restart=always
RestartSec=3
User=$USERNAME

[Install]
WantedBy=multi-user.target
EOF
echo "  Written: $MEDIAMTX_SERVICE"

# --- Camera script permissions ---
echo "[5/6] Setting camera script permissions and updating paths..."
if [ -f "$REPO_DIR/start-camera.sh" ]; then
    chmod +x "$REPO_DIR/start-camera.sh"
    echo "  start-camera.sh made executable"
else
    echo "  WARNING: start-camera.sh not found in $REPO_DIR"
fi

if [ -f "$REPO_DIR/mediamtx.yml" ]; then
    sed -i "s|runOnInit:.*start-camera.sh|runOnInit: $REPO_DIR/start-camera.sh|" "$REPO_DIR/mediamtx.yml"
    echo "  mediamtx.yml path updated"
else
    echo "  WARNING: mediamtx.yml not found in $REPO_DIR"
fi

# --- Enable and start services ---
echo "[6/6] Enabling and starting services..."
sudo systemctl daemon-reload
sudo systemctl enable mediamtx
sudo systemctl enable bass
sudo systemctl start mediamtx
sleep 2
sudo systemctl start bass

echo ""
echo "==============================="
echo " Setup complete!"
echo " Web GUI:    http://$(hostname -I | awk '{print $1}'):5051"
echo " Cam stream: http://$(hostname -I | awk '{print $1}'):8889"
echo ""
echo " Service status:"
sudo systemctl is-active mediamtx && echo "  mediamtx: running" || echo "  mediamtx: FAILED"
sudo systemctl is-active bass && echo "  bass:     running" || echo "  bass:     FAILED"
echo ""
echo " A reboot is recommended for camera changes to take effect."
read -p " Reboot now? (y/N): " CONFIRM
if [[ "$CONFIRM" =~ ^[Yy]$ ]]; then
    echo " Rebooting..."
    sudo reboot
else
    echo " Skipping reboot. Run 'sudo reboot' when ready."
fi