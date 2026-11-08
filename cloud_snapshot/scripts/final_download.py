#!/usr/bin/env python3
"""final_download.py - Download real datasets (no Docker required)"""
import subprocess, sys, time, os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

BASE = Path("/data/datasets")
LOG_FILE = "/data/logs/final_download.log"

def log(msg: str):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, 'a') as f:
        f.write(line + '\n')

def wget(url: str, outpath: str, timeout: int = 600) -> bool:
    """Download a file. Returns True on success."""
    try:
        result = subprocess.run(
            ["wget", "-q", "--show-progress", "-O", outpath, url],
            timeout=timeout, capture_output=True, text=True
        )
        if result.returncode == 0 and Path(outpath).stat().st_size > 1000:
            return True
    except subprocess.TimeoutExpired:
        log("  TIMEOUT")
    except Exception as e:
        log("  ERROR: %s" % e)
    return False


# ===================== PHP Source Tarballs =====================
def download_php():
    """Download PHP source tarballs (3 versions).
    Each ~11MB, high code overlap between minor versions -> 90%+ dedup"""
    log("=" * 50)
    log("[PHP] Downloading 3 PHP source versions")
    d = BASE / "php"
    d.mkdir(parents=True, exist_ok=True)
    
    versions = {
        "8.2.33": "https://www.php.net/distributions/php-8.2.33.tar.xz",
        "8.3.33": "https://www.php.net/distributions/php-8.3.33.tar.xz",
        "8.4.24": "https://www.php.net/distributions/php-8.4.24.tar.xz",
    }
    
    for ver, url in versions.items():
        fpath = d / f"php-{ver}.tar.xz"
        if fpath.exists() and fpath.stat().st_size > 10000:
            log(f"  SKIP php-{ver} (exists, {fpath.stat().st_size/1e6:.1f}MB)")
            continue
        log(f"  Downloading php-{ver} ({_get_size_hint(url)})...")
        ok = wget(url, str(fpath))
        if ok:
            log(f"  DONE: {fpath.stat().st_size/1e6:.1f}MB")
        else:
            log(f"  FAILED: php-{ver}")
    
    _show_stats(d, "PHP Sources")


# ===================== Ubuntu Base Rootfs =====================
def download_ubuntu():
    """Download Ubuntu base rootfs tarballs (5 LTS versions).
    These are lightweight (~25-80MB each), content-rich, and share many files
    across versions -> ~60-80% dedup"""
    log("=" * 50)
    log("[Ubuntu] Downloading 5 Ubuntu base rootfs versions")
    d = BASE / "ubuntu"
    d.mkdir(parents=True, exist_ok=True)
    
    # Ubuntu base filesystems (minimal, no desktop)
    # Format: ubuntu-base-{version}-base-amd64.tar.gz
    versions = {
        "20.04.6": "https://cdimage.ubuntu.com/ubuntu-base/releases/20.04/release/ubuntu-base-20.04.6-base-amd64.tar.gz",
        "22.04.5": "https://cdimage.ubuntu.com/ubuntu-base/releases/22.04/release/ubuntu-base-22.04.5-base-amd64.tar.gz",
        "24.04.2": "https://cdimage.ubuntu.com/ubuntu-base/releases/24.04/release/ubuntu-base-24.04.2-base-amd64.tar.gz",
        "26.04":   "https://cdimage.ubuntu.com/ubuntu-base/releases/26.04/release/ubuntu-base-26.04-base-amd64.tar.gz",
    }
    
    for ver, url in versions.items():
        fpath = d / f"ubuntu-base-{ver}-amd64.tar.gz"
        if fpath.exists() and fpath.stat().st_size > 10000:
            log(f"  SKIP ubuntu-{ver} (exists, {fpath.stat().st_size/1e6:.1f}MB)")
            continue
        log(f"  Downloading ubuntu-base-{ver}...")
        ok = wget(url, str(fpath), timeout=300)
        if ok:
            log(f"  DONE: {fpath.stat().st_size/1e6:.1f}MB")
        else:
            # Try alternate URL format
            alt_url = url.replace("cdimage.ubuntu.com", "mirrors.aliyun.com")
            log(f"  Retry with Aliyun mirror...")
            ok = wget(alt_url, str(fpath), timeout=300)
            if ok:
                log(f"  DONE (mirror): {fpath.stat().st_size/1e6:.1f}MB")
            else:
                log(f"  FAILED: ubuntu-{ver}")
    
    _show_stats(d, "Ubuntu Base")


# ===================== Wikipedia SQL =====================
def download_sqldump():
    """Download Wikipedia categorylinks.sql.gz (3 months).
    ~2.3GB each, ~95% overlap between consecutive months -> excellent dedup"""
    log("=" * 50)
    log("[SQLDump] Downloading Wikipedia categorylinks dumps")
    d = BASE / "sqldump"
    d.mkdir(parents=True, exist_ok=True)
    
    months = ["20260501", "20260601", "20260701"]
    base_url = "https://dumps.wikimedia.org/enwiki/{date}/enwiki-{date}-categorylinks.sql.gz"
    
    for date in months:
        fname = f"enwiki-{date}-categorylinks.sql.gz"
        fpath = d / fname
        if fpath.exists() and fpath.stat().st_size > 1000:
            log(f"  SKIP {fname} (exists, {fpath.stat().st_size/1e9:.2f}GB)")
            continue
        url = base_url.format(date=date)
        log(f"  Downloading {fname} (~2.3GB)...")
        ok = wget(url, str(fpath), timeout=1200)
        if ok:
            log(f"  DONE: {fpath.stat().st_size/1e9:.2f}GB")
        else:
            log(f"  FAILED: {fname}")
    
    _show_stats(d, "Wikipedia SQL")


# ===================== Helpers =====================
def _get_size_hint(url: str) -> str:
    try:
        r = subprocess.run(["curl", "-sI", "--max-time", "5", url],
                          capture_output=True, text=True, timeout=10)
        for line in r.stdout.split('\n'):
            if 'content-length' in line.lower():
                size = int(line.split(':')[1].strip())
                if size > 1e9: return "%.1fGB" % (size/1e9)
                if size > 1e6: return "%.1fMB" % (size/1e6)
                return "%dKB" % (size//1024)
    except:
        pass
    return "?"

def _show_stats(d: Path, label: str):
    files = list(d.glob("*"))
    total = sum(f.stat().st_size for f in files if f.is_file())
    count = sum(1 for f in files if f.is_file())
    log(f"  [{label}] {count} files, {total/1e6:.1f} MB total")


# ===================== Main =====================
def main():
    log("=" * 60)
    log("FINAL DATASET DOWNLOAD (No Docker)")
    log("Server: " + (os.uname().nodename if hasattr(os, 'uname') else 'unknown'))
    
    # Disk space
    disk = os.statvfs("/data")
    free_gb = disk.f_frsize * disk.f_bavail / 1e9
    log(f"Free: {free_gb:.1f} GB")
    
    t0 = time.time()
    
    download_php()
    download_ubuntu()
    download_sqldump()
    
    elapsed = time.time() - t0
    log("=" * 60)
    log(f"ALL DONE in {elapsed/60:.1f} min")
    log("=" * 60)
    
    # Final summary
    for subdir in ["linux", "php", "ubuntu", "sqldump"]:
        d = BASE / subdir
        if d.exists():
            total = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
            count = sum(1 for f in d.rglob("*") if f.is_file())
            log(f"  {subdir:>10s}: {count:>5d} files, {total/1e9:>8.2f} GB")


if __name__ == "__main__":
    main()
