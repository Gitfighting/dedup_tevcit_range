#!/bin/bash
# Quick setup script - runs independently on server
set -e

LOGFILE=/data/logs/setup_complete.log
exec > >(tee -a $LOGFILE) 2>&1

echo "=== $(date) Starting quick setup ==="

# 1. Kill any stale rustup
pkill -f rustup 2>/dev/null || true
rm -f /root/.cargo/.rustup-install-lock 2>/dev/null || true

# 2. Install Rust (non-interactive)
echo "[1/4] Installing Rust..."
if ! /root/.cargo/bin/rustc --version 2>/dev/null; then
    curl --proto "=https" --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
    echo "Rust installed"
else
    echo "Rust already installed"
fi
/root/.cargo/bin/rustc --version
/root/.cargo/bin/cargo --version

# 3. Python venv
echo "[2/4] Setting up Python venv..."
if [ ! -d /data/venv ]; then
    python3 -m venv /data/venv
fi
source /data/venv/bin/activate
pip install --upgrade pip -q
pip install boto3 psycopg2-binary sqlalchemy fastapi uvicorn pydantic numpy pandas tqdm minio "paramiko" -q
echo "Python packages installed"

# 4. PostgreSQL config
echo "[3/4] Configuring PostgreSQL..."
sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname='skystore'" | grep -q 1 || \
    sudo -u postgres psql -c "CREATE USER skystore WITH PASSWORD 'skystore';"
sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='skystore'" | grep -q 1 || \
    sudo -u postgres psql -c "CREATE DATABASE skystore OWNER skystore;"
sudo -u postgres psql -c "ALTER USER skystore CREATEDB;"
echo "PostgreSQL ready"

# 5. Mount data disk if exists
echo "[4/4] Checking data disk..."
VDISK=$(lsblk -n -o NAME,SIZE,TYPE | grep disk | grep -v vda | head -1 | awk '{print $1}')
if [ -n "$VDISK" ]; then
    echo "Found disk: $VDISK"
    if ! mountpoint -q /data; then
        mkfs.ext4 -F /dev/$VDISK
        mount /dev/$VDISK /data
        echo "/dev/$VDISK  /data  ext4  defaults  0  0" >> /etc/fstab
        echo "Data disk mounted"
    fi
else
    echo "No extra data disk found, using system disk"
fi

echo ""
echo "=== $(date) SETUP COMPLETE ==="
df -h / /data 2>/dev/null
echo ""
echo "Tools:"
/root/.cargo/bin/rustc --version 2>&1
python3 --version 2>&1
pg_isready 2>&1
docker --version 2>&1
echo ""
echo "Log: $LOGFILE"
