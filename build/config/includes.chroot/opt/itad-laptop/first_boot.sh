#!/bin/bash
# first_boot.sh — Setup ITAD environment on the live Ubuntu USB

set -e

# Colors for output
G='\033[0;32m'
Y='\033[0;33m'
R='\033[0;31m'
N='\033[0m'

say() { echo -e "${G}▶${N} $*"; }
warn() { echo -e "${Y}▲${N} $*"; }
die() { echo -e "${R}✖${N} $*" >&2; exit 1; }

# Ensure running as root
if [ "$EUID" -ne 0 ]; then
  die "Please run this script with sudo: sudo bash first_boot.sh"
fi

say "Starting ITAD First Boot Setup..."

# 1. Update package lists (necessary before installing)
say "Updating package lists (this may take a minute)..."
apt-get update -y

# 2. Install required system tools
say "Installing hardware diagnostics tools..."
# smartmontools = hard drive health
# nvme-cli = NVMe SSD tools
# hdparm = hard drive parameters
# dmidecode = hardware info
# pciutils & usbutils = hardware buses
apt-get install -y python3 python3-pip smartmontools nvme-cli hdparm dmidecode pciutils usbutils

# 3. Install required Python packages
say "Installing Python libraries..."
pip3 install psutil requests --break-system-packages || pip3 install psutil requests

# 4. Make the Python scripts executable
say "Setting permissions..."
chmod +x *.py
chmod +x *.sh

# 5. Run the main ITAD script
say "Setup complete! Launching ITAD main menu..."
echo "------------------------------------------------------"
sleep 2

# This runs your main script. 
if [ -f "itad_run.py" ]; then
    python3 itad_run.py
else
    warn "itad_run.py not found. You can run the other scripts manually."
fi