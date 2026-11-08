#!/usr/bin/env python3
"""
build_file_pool.py —— 从下载的压缩包中提取所有单文件

每个压缩包解压到 /data/filepool/<dataset>/<version>/
然后收集所有单文件的路径列表，用于 CDC 去重实验。

文件池大小预估:
  linux:  5 versions x ~60K files/version = ~300K files, ~3.2GB
  php:    3 versions x ~3K files/version  = ~9K files,    ~180MB  
  ubuntu: 4 versions x ~1K files/version  = ~4K files,    ~300MB

用法:
  source /data/venv/bin/activate && python build_file_pool.py
"""

import os
import subprocess
import tarfile
import time
from pathlib import Path
from collections import defaultdict

BASE = Path("/data/datasets")
POOL = Path("/data/filepool")
LOG_FILE = "/data/logs/filepool.log"


def log(msg: str):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, 'a') as f:
        f.write(line + '\n')


def extract_tar(archive_path: Path, dest_dir: Path) -> int:
    """Extract a tar archive (supports .tar.xz, .tar.gz, plain .tar).
    Returns number of files extracted."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    
    try:
        if archive_path.name.endswith('.tar.xz'):
            # Use xz extraction
            result = subprocess.run(
                ['tar', '-xJf', str(archive_path), '-C', str(dest_dir)],
                timeout=600, capture_output=True, text=True
            )
        elif archive_path.name.endswith('.tar.gz') or archive_path.name.endswith('.tgz'):
            result = subprocess.run(
                ['tar', '-xzf', str(archive_path), '-C', str(dest_dir)],
                timeout=600, capture_output=True, text=True
            )
        else:
            log(f"  Unknown format: {archive_path.name}")
            return 0
        
        if result.returncode != 0:
            log(f"  tar error: {result.stderr[:200]}")
        
    except subprocess.TimeoutExpired:
        log(f"  TIMEOUT extracting {archive_path.name}")
        return 0
    except Exception as e:
        log(f"  ERROR: {e}")
        return 0
    
    # Count files recursively
    count = sum(1 for _ in dest_dir.rglob('*') if _.is_file())
    return count


def extract_dataset(name: str, archive_dir: Path) -> int:
    """Extract all archives in a dataset directory."""
    log(f"\n--- {name.upper()} ---")
    
    archives = sorted(archive_dir.glob('*.tar*'))
    if not archives:
        log(f"  No archives found in {archive_dir}")
        return 0
    
    total_files = 0
    for arch in archives:
        # Dest: /data/filepool/<name>/<version_name>
        ver_name = arch.stem.replace('.tar', '')
        dest = POOL / name / ver_name
        
        # Skip if already extracted (check if dest has files)
        if dest.exists():
            existing = sum(1 for _ in dest.rglob('*') if _.is_file())
            if existing > 10:
                log(f"  SKIP {arch.name} -> {existing} files already extracted")
                total_files += existing
                continue
        
        log(f"  Extracting {arch.name} ({arch.stat().st_size/1e6:.1f}MB)...")
        t0 = time.time()
        count = extract_tar(arch, dest)
        elapsed = time.time() - t0
        log(f"  DONE: {count} files in {elapsed:.1f}s")
        total_files += count
    
    return total_files


def collect_file_list() -> dict:
    """Walk the file pool and return mapping of dataset -> list of file paths."""
    file_map = defaultdict(list)
    
    for dataset_dir in sorted(POOL.iterdir()):
        if not dataset_dir.is_dir():
            continue
        for version_dir in sorted(dataset_dir.iterdir()):
            if not version_dir.is_dir():
                continue
            for f in version_dir.rglob('*'):
                if f.is_file() and f.stat().st_size > 0:
                    file_map[dataset_dir.name].append(str(f))
    
    return file_map


def main():
    log("=" * 60)
    log("BUILDING FILE POOL (extract all archives)")
    
    t0 = time.time()
    
    # Extract each dataset
    total_files = 0
    datasets = [
        ('linux',  BASE / 'linux'),
        ('php',    BASE / 'php'),
        ('ubuntu', BASE / 'ubuntu'),
    ]
    
    for name, path in datasets:
        if not path.exists():
            log(f"{name}: directory not found, skipping")
            continue
        total_files += extract_dataset(name, path)
    
    elapsed = time.time() - t0
    log(f"\n{'=' * 60}")
    log(f"EXTRACTION COMPLETE: {total_files} total files in {elapsed/60:.1f} min")
    
    # Collect file list
    log("\nCollecting file paths...")
    file_map = collect_file_list()
    
    total_size = 0
    for dataset_name, files in sorted(file_map.items()):
        size = sum(Path(f).stat().st_size for f in files)
        total_size += size
        log(f"  {dataset_name}: {len(files):,} files, {size/1e6:.1f} MB")
    
    log(f"  TOTAL: {sum(len(v) for v in file_map.values()):,} files, {total_size/1e9:.2f} GB")
    
    # Save file list
    import json
    list_file = POOL / "file_list.json"
    list_file.write_text(json.dumps(file_map, indent=2))
    log(f"\nFile list saved to {list_file}")
    
    log("\n" + "=" * 60)
    log("READY FOR EXPERIMENT!")


if __name__ == "__main__":
    main()
