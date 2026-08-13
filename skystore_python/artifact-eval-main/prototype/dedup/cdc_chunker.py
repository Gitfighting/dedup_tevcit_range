"""
FastCDC content-defined chunking.

Reference: Wen Xia et al., "FastCDC: a Fast and Efficient Content-Defined
Chunking Approach for Data Deduplication", USENIX ATC 2016.

Uses Gear-based rolling hash with normalized chunk-size distribution.
"""

import struct
from typing import List, Generator

_GEAR_TABLE = []
_GEAR_TABLE_SIZE = 256

def _init_gear_table():
    global _GEAR_TABLE
    if _GEAR_TABLE:
        return
    import random
    rng = random.Random(0xDEADBEEF)  # stable gear-table seed
    _GEAR_TABLE = [rng.getrandbits(64) for _ in range(_GEAR_TABLE_SIZE)]


class FastCDC:
    """Content-defined chunker using the FastCDC algorithm.

    min_size  - minimum chunk size in bytes (default 2 KB)
    avg_size  - average / target chunk size in bytes (default 8 KB)
    max_size  - maximum chunk size in bytes (default 64 KB)
    """

    def __init__(self, min_size=2*1024, avg_size=8*1024, max_size=64*1024):
        _init_gear_table()
        self.min_size = min_size
        self.avg_size = avg_size
        self.max_size = max_size
        self.mask_s = self._mask_bits(avg_size)
        self.mask_l = self._mask_bits(avg_size // 2)

    @staticmethod
    def _mask_bits(target):
        bits = 0
        while (1 << bits) < target:
            bits += 1
        return bits

    def chunk(self, data: bytes) -> List[bytes]:
        return list(self.iter_chunks(data))

    def iter_chunks(self, data: bytes) -> Generator[bytes, None, None]:
        n = len(data)
        cur = 0
        while cur < n:
            start = cur
            h = 0
            while cur < n:
                b = data[cur]
                cur += 1
                h = (h << 1) + _GEAR_TABLE[b]
                sz = cur - start
                if sz >= self.max_size:
                    break
                if sz >= self.min_size:
                    mask = self._normalized_mask(sz)
                    if (h & mask) == 0:
                        break
            yield data[start:cur]

    def _normalized_mask(self, sz):
        if sz < self.avg_size // 2:
            return (1 << self.mask_l) - 1
        return (1 << self.mask_s) - 1


def chunk_file(path, min_size=2*1024, avg_size=8*1024, max_size=64*1024):
    cdc = FastCDC(min_size=min_size, avg_size=avg_size, max_size=max_size)
    with open(path, "rb") as f:
        return cdc.chunk(f.read())
