#!/bin/bash

# BASS Setup Script
# Run this once after cloning the repo to configure the systemd service

USERNAME=$(whoami)
HOME_DIR=$(eval echo ~$USERNAME)
REPO_DIR="$HOME_DIR/BASS"
BASS_SERVICE="/etc/systemd/system/bass.service"
MEDIAMTX_SERVICE="/etc/systemd/system/mediamtx.service"

echo "==============================="
echo " BASS Setup"
echo " User: $USERNAME"
echo " Repo: $REPO_DIR"
echo "==============================="

# Enable camera
echo "[1/5] Enabling camera..."
sudo sed -i 's/camera_auto_detect=1/camera_auto_detect=0/' /boot/firmware/config.txt
grep -qxF 'start_x=1' /boot/firmware/config.txt || echo 'start_x=1' | sudo tee -a /boot/firmware/config.txt
grep -qxF 'gpu_mem=128' /boot/firmware/config.txt || echo 'gpu_mem=128' | sudo tee -a /boot/firmware/config.txt

# Enable I2C
echo "[2/5] Enabling I2C..."
sudo raspi-config nonint do_i2c 0

# Write BASS web app service
echo "[3/5] Configuring BASS web app service..."
sudo bash -c "cat > $BASS_SERVICE" << EOF
[Unit]
Description=BASS Web App
After=network.target mediamtx.service

[Service]
ExecStart=/usr/bin/python3 $REPO_DIR/cam_web/app.py
WorkingDirectory=$REPO_DIR
Restart=always
User=$USERNAME

[Install]
WantedBy=multi-user.target
EOF

# Write MediaMTX service
echo "[4/5] Configuring MediaMTX camera stream service..."
sudo bash -c "cat > $MEDIAMTX_SERVICE" << EOF
[Unit]
Description=MediaMTX Camera Stream
After=network.target

[Service]
ExecStart=$REPO_DIR/mediamtx $REPO_DIR/mediamtx.yml
WorkingDirectory=$REPO_DIR
Restart=always
User=$USERNAME

[Install]
WantedBy=multi-user.target
EOF

# Enable and start both services
echo "[5/5] Enabling and starting services..."
sudo systemctl daemon-reload
sudo systemctl enable mediamtx
sudo systemctl enable bass
sudo systemctl start mediamtx
sudo systemctl start bass

echo ""
echo "==============================="
echo " Setup complete!"
echo " Web GUI:    http://$(hostname -I | awk '{print $1}'):5050"
echo " Cam stream: http://$(hostname -I | awk '{print $1}'):8889"
echo " Rebooting in 5 seconds..."
echo "==============================="
sleep 5
sudo reboot
