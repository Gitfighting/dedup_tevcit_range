#!/bin/bash
# ===========================================================================
# setup_cloud.sh —— SkyStore CDC Dedup 云服务器一键初始化脚本
# 使用方法: chmod +x setup_cloud.sh && sudo ./setup_cloud.sh
# 适配: Ubuntu 22.04 LTS
# ===========================================================================
set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log()  { echo -e "${GREEN}[SETUP]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
err()  { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

# ==============================
# 1. 系统基础依赖
# ==============================
log "Step 1/6: Installing OS packages..."
sudo apt-get update -qq
sudo apt-get install -y -qq \
    build-essential pkg-config libssl-dev \
    curl wget git \
    python3 python3-pip python3-venv \
    postgresql postgresql-client \
    docker.io \
    htop iotop sysstat \
    nvme-cli

# ==============================
# 2. 挂载数据盘
# ==============================
log "Step 2/6: Mounting data disk to /data..."
if ! mountpoint -q /data; then
    DATA_DISK=$(lsblk -n -o NAME,SIZE,TYPE | grep disk | grep -v nvme0 | grep -v vda | head -1 | awk '{print $1}')
    if [ -n "$DATA_DISK" ]; then
        sudo mkdir -p /data
        sudo mkfs.ext4 -F /dev/$DATA_DISK
        sudo mount /dev/$DATA_DISK /data
        echo "/dev/$DATA_DISK  /data  ext4  defaults  0  0" | sudo tee -a /etc/fstab
        log "Data disk /dev/$DATA_DISK mounted to /data"
    else
        warn "No data disk found. Using system disk only."
        sudo mkdir -p /data
    fi
else
    log "/data already mounted"
fi
sudo chown -R $(whoami):$(whoami) /data

# ==============================
# 3. 安装 Rust 工具链
# ==============================
log "Step 3/6: Installing Rust toolchain..."
if ! command -v rustc &>/dev/null; then
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain stable
    source "$HOME/.cargo/env"
else
    log "Rust already installed: $(rustc --version)"
fi

# ==============================
# 4. Python 虚拟环境
# ==============================
log "Step 4/6: Setting up Python environment..."
python3 -m venv /data/venv
source /data/venv/bin/activate
pip install --upgrade pip
pip install \
    boto3 psycopg2-binary sqlalchemy fastapi uvicorn \
    pydantic numpy pandas tqdm minio
log "Python packages installed"

# ==============================
# 5. PostgreSQL 初始化
# ==============================
log "Step 5/6: Setting up PostgreSQL..."
sudo systemctl start postgresql
sudo systemctl enable postgresql

sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname='skystore'" | grep -q 1 || \
    sudo -u postgres psql -c "CREATE USER skystore WITH PASSWORD 'skystore';"
sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='skystore'" | grep -q 1 || \
    sudo -u postgres psql -c "CREATE DATABASE skystore OWNER skystore;"
sudo -u postgres psql -c "ALTER USER skystore CREATEDB;"
log "PostgreSQL ready (user:skystore, db:skystore)"

# ==============================
# 6. 实验目录
# ==============================
log "Step 6/6: Creating experiment directory structure..."
mkdir -p /data/datasets/{linux,php,ubuntu,sqldump}
mkdir -p /data/traces
mkdir -p /data/mappings
mkdir -p /data/chunks
mkdir -p /data/results
mkdir -p /data/logs
mkdir -p /data/skystore-prototype
log "Directory structure created"

# ==============================
# 完成
# ==============================
log ""
log "============================================"
log " SETUP COMPLETE"
log "============================================"
log " Data dir:  /data"
log " Rust:      $(rustc --version 2>/dev/null || echo 'please source ~/.cargo/env')"
log " Python:    /data/venv/bin/python"
log " Activate:  source /data/venv/bin/activate"
log ""
log " Next: scp datasets, traces, and prototype code to /data/"
log "============================================"
