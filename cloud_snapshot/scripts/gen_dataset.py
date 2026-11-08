#!/usr/bin/env python3
"""
gen_dataset.py —— 生成具有去重特性的合成数据集

模拟 5 个连续 Linux 内核版本：
  - 每个文件 ~10MB
  - 版本间差异 5%（~95% 重叠）
  - 包含结构化数据（C 代码、头文件、Makefile）

用途：受控的 CDC 去重实验，无需从外网下载。
"""

import random, os, struct
from pathlib import Path


def generate_source_file(fname: str, size: int, seed: int) -> bytes:
    """Generate a C-like source file with structured content."""
    rng = random.Random(seed)
    lines = []
    size_so_far = 0
    
    func_names = [
        "init_module", "cleanup_module", "do_mount", "do_umount",
        "sys_read", "sys_write", "sys_open", "sys_close",
        "kmalloc", "kfree", "printk", "schedule",
        "wake_up", "wait_event", "mutex_lock", "mutex_unlock",
        "register_filesystem", "unregister_filesystem",
        "vfs_read", "vfs_write", "d_alloc", "d_delete",
        "inode_init", "inode_free", "block_read", "block_write",
        "page_cache_read", "page_cache_write", "kmem_cache_alloc",
    ]
    
    types = ["int", "long", "ssize_t", "void", "struct inode *", "struct file *",
             "struct dentry *", "struct super_block *", "unsigned long", "size_t"]
    
    while size_so_far < size:
        # Header/footer area
        if size_so_far < size * 0.02 or size_so_far > size * 0.95:
            lines.append(f"/* Auto-generated kernel source - {fname} */")
        else:
            choice = rng.random()
            if choice < 0.6:
                # Function definition
                fn = rng.choice(func_names)
                t = rng.choice(types)
                lines.append(f"static {t} {fn}(void)")
                lines.append("{")
                n_lines = rng.randint(3, 15)
                for _ in range(n_lines):
                    lines.append(f"    int __var_{rng.randint(0,9999):04d} = {rng.randint(0,100)};")
                lines.append(f"    return {rng.randint(-5, 0)};")
                lines.append("}")
                lines.append("")
            elif choice < 0.8:
                # Include / macro
                h = rng.choice(["kernel.h", "module.h", "sched.h", "mm.h", "fs.h",
                                "slab.h", "spinlock.h", "mutex.h", "wait.h"])
                lines.append(f"#include <linux/{h}>")
            elif choice < 0.9:
                # Variable declarations
                t = rng.choice(types)
                lines.append(f"static {t} __global_{rng.randint(0,999):04d};")
            else:
                # Comment
                lines.append(f"/* Section {rng.randint(0,99):02d}: {rng.choice(['init', 'exit', 'core', 'utils'])} */")
        
        size_so_far += sum(len(l) + 1 for l in lines[-5:] if lines[-5:])  # rough estimate
        if len(lines) > 1000:
            break
    
    return "\n".join(lines).encode('utf-8')


def generatefile(seed: int, target_size: int) -> bytes:
    """Generate a mixed content file with ~target_size bytes."""
    rng = random.Random(seed)
    data = bytearray()
    
    # 40% structured C code
    code = generate_source_file(f"kernel_{seed}.c", int(target_size * 0.4), seed)
    data.extend(code)
    
    # 30% binary-like data (firmware)
    bin_size = int(target_size * 0.3)
    bin_data = bytearray()
    for i in range(bin_size):
        bin_data.append(rng.randint(0, 255))
    data.extend(bin_data)
    
    # 30% text (documentation, Kconfig)
    text_size = target_size - len(data)
    if text_size > 0:
        text_words = ["kernel", "config", "module", "driver", "device", "memory",
                       "process", "thread", "interrupt", "timer", "dma", "pci",
                       "usb", "network", "filesystem", "block", "char", "tty"]
        text = ""
        while len(text.encode('utf-8')) < text_size:
            text += " ".join(rng.sample(text_words, 5)) + ". "
            text += f"CONFIG_{rng.choice(text_words).upper()}_{rng.randint(0,99)}=y\n"
        data.extend(text.encode('utf-8'))
    
    return bytes(data[:target_size])


def mutate_file(base: bytes, mutation_rate: float, seed: int) -> bytes:
    """Create a mutated version of a file by randomly changing bytes."""
    rng = random.Random(seed)
    data = bytearray(base)
    num_mutations = int(len(data) * mutation_rate)
    
    for _ in range(num_mutations):
        pos = rng.randint(0, len(data) - 1)
        data[pos] = rng.randint(0, 255)
    
    return bytes(data)


def main():
    output_dir = Path("/data/datasets/linux")
    output_dir.mkdir(parents=True, exist_ok=True)
    
    FILE_SIZE = 10 * 1024 * 1024  # 10 MB each
    NUM_VERSIONS = 5
    MUTATION_RATE = 0.05  # 5% change between versions = 95% dedup
    
    print(f"Generating {NUM_VERSIONS} synthetic kernel versions")
    print(f"  Size per file: {FILE_SIZE / 1e6:.0f} MB")
    print(f"  Mutation rate: {MUTATION_RATE:.0%}")
    print(f"  Expected dedup: ~{1-MUTATION_RATE:.0%}")
    print()
    
    # Generate base file
    print("Generating base kernel...")
    base = generatefile(seed=42, target_size=FILE_SIZE)
    fpath = output_dir / "linux-6.1.100.tar.xz"
    fpath.write_bytes(base)
    print(f"  {fpath.name}: {len(base):,} B")
    
    # Generate mutated versions
    for i in range(1, NUM_VERSIONS):
        ver = 100 + i
        print(f"Generating variant {ver} (5% mutation)...")
        mutated = mutate_file(base, MUTATION_RATE, seed=42 + i * 100)
        fpath = output_dir / f"linux-6.1.{ver}.tar.xz"
        fpath.write_bytes(mutated)
        
        # Verify actual difference
        diff_count = sum(1 for a, b in zip(base, mutated) if a != b)
        actual_rate = diff_count / len(base)
        print(f"  {fpath.name}: {len(mutated):,} B ({actual_rate:.2%} different)")
    
    total_size = sum(f.stat().st_size for f in output_dir.glob("*.tar.xz"))
    print(f"\nTotal: {total_size / 1e6:.0f} MB across {NUM_VERSIONS} files")
    print("Done!")


if __name__ == "__main__":
    main()
