"""
DedupAwareTevict — deduplication-aware adaptive TTL eviction policy.

Based on SkyStore's full adaptive TTL mechanism (histogram learning,
calc_evict_cost, region-pair caching), with ONE additional decision
layer: the storage-format choice.

Design difference from SkyStore (single-variable ablation):
    SkyStore:   always stores a full physical replica when caching.
    Ours:       when caching, decides between:
                   HOT_STATE  — full assembled physical replica
                   DEDUP_STATE — chunk reference manifest only

    Decision formula (Dedup-Aware Break-Even):
        tau_effective = (1 - L) * N / S = (1 - L) * T_even

        where:
            L = chunk locality ratio in target region  (0.0 ~ 1.0)
            N = network transfer cost per GB  ($)
            S = storage cost per GB per second  ($)
            T_even = N / S  (break-even time, seconds)

        if time_to_next_access <= tau_effective:
            -> HOT  (full replica:  local hit, 0 transfer cost)
        else:
            -> DEDUP (chunk refs only:  save storage, pay transfer on reassembly)

    Intuition:
        L=0 (all chunks remote):  tau_effective = T_even  --> classical break-even
        L=0.5 (half local):       tau_effective = 0.5 T_even  --> DEDUP more favored
        L=0.9 (90% local):        tau_effective = 0.1 T_even  --> DEDUP overwhelmingly favored

    This bridges traditional deduplication (saves storage cost) with
    adaptive TTL caching (saves transfer cost), using object-level
    chunk locality to make precise storage-format decisions.
"""

from typing import List
from operations.schemas.object_schemas import StartUploadRequest
from operations.policy.placement_policy.base import (
    PlacementPolicy,
)
from datetime import timedelta, datetime, timezone
import math
from collections import defaultdict
from operations.policy.utils.definitions import GB
from operations.policy.utils.helpers import (
    get_avg_network_cost,
    get_avg_storage_cost,
    get_min_network_cost,
    get_median_network_cost,
)
from operations.schemas.object_schemas import LocateObjectResponse
from operations.utils.helper import TraceIdx
import threading
import csv
import os


# ------------------------------------------------------------------
#  Object storage-format state
# ------------------------------------------------------------------

class ObjState:
    HOT = "hot"          # full physical replica cached locally
    DEDUP = "dedup"      # chunk reference manifest only


# ------------------------------------------------------------------
#  DedupAwareTevict
# ------------------------------------------------------------------

class DedupAwareTevict(PlacementPolicy):
    """
    Deduplication-aware adaptive TTL eviction.

    ======  SkyStore 原有逻辑（完整保留） ======
    继承 SkyStore 的全部自适应 TTL 机制:
      - histogram 学习 inter-arrival 分布
      - calc_evict_cost() 选择最小成本 TTL
      - region-pair TTL 缓存
      - update_past_requests() 窗口管理

    ======  新增: 去重感知层 (Dedup-Aware Formula) ======
    在 SkyStore 决定缓存后，使用 chunk locality 做精确决策:

        tau_effective = (1 - L) * T_even = (1 - L) * N / S

        if time_to_next_access <= tau_effective:
            → HOT_STATE:   存储完整物理副本
                           (后续 GET 0 传输费, 存储成本 = size × TTL × S)
        else:
            → DEDUP_STATE: 仅存储 chunk 引用清单
                           (后续 GET 需支付传输费 = size × (1-L) × N)

    为什么用 (1-L) × T_even 而非 adaptive_ttl:
      - 纳入 chunk 局域性 L：L 越大，DEDUP 优势越大
      - N/S 固定值: 2-reg 约 7h，不需要 trace 学习
      - 对象级粒度: 每个 PUT 根据其 chunk 在目标区域的命中率独立决策
      - T_even vs adaptive_ttl 对比:
          T15 (one-hit 48%):  T_even=7h vs adaptive_ttl≈30min → T_even 更保守
          T65 (hot 67%):      T_even=7h vs adaptive_ttl≈3h → T_even 更宽容缓存
          T29 (cold 98%):     T_even=7h vs adaptive_ttl≈1h → T_even 更宽容缓存
        最终由 L 来修正: L高→倾向DEDUP, L低→倾向HOT

    单变量消融: 仅改变"存储形式"这一个变量，TTL 计算逻辑与 SkyStore 完全一致。
    """

    _instance = None
    _lock = threading.Lock()

    # ===============================================================
    #  __init__  — SkyStore 全部字段 + 去重专用字段
    # ===============================================================

    def read_timestamps_from_csv(self, file_path: str, timestamp_column: str) -> list:
        timestamps = []

        with open(file_path, mode="r") as file:
            csv_reader = csv.DictReader(file)
            for row in csv_reader:
                timestamp_str = row[timestamp_column]
                try:
                    timestamps.append(int(timestamp_str))
                except ValueError:
                    print(f"Invalid timestamp format: {timestamp_str}")

        print(f"Total length of timestamps: {len(timestamps)}")
        return timestamps

    def _build_key_mappings(self, file_path: str):
        """Second pass over the trace: build idx->key and key->time_to_next maps.

        This allows get_ttl() to compute time_to_next_access internally
        without relying on external set_key_context() calls.
        """
        from collections import defaultdict
        self._idx_to_key = {}
        _key_timestamps = defaultdict(list)  # obj_key -> [(idx, timestamp), ...]

        with open(file_path, mode="r") as file:
            csv_reader = csv.DictReader(file)
            for i, row in enumerate(csv_reader):
                key = row.get("obj_key") or row.get("data_id") or row.get("key", "")
                ts = row.get("timestamp", "")
                if key and ts:
                    try:
                        t = int(ts)
                        self._idx_to_key[i] = key
                        _key_timestamps[key].append((i, t))
                    except ValueError:
                        pass

        # Build (key, timestamp) -> seconds_to_next_access
        self._key_time_to_next = {}
        for key, pairs in _key_timestamps.items():
            sorted_pairs = sorted(pairs, key=lambda x: x[1])
            for j in range(len(sorted_pairs)):
                idx_j, ts_j = sorted_pairs[j]
                if j + 1 < len(sorted_pairs):
                    _, ts_next = sorted_pairs[j + 1]
                    self._key_time_to_next[(key, ts_j)] = ts_next - ts_j
                else:
                    self._key_time_to_next[(key, ts_j)] = None  # no future access

        print(f"Key mappings: {len(self._idx_to_key)} idx->key, {len(_key_timestamps)} unique keys")

    def __init__(self, init_regions: List[str] = []) -> None:
        super().__init__(init_regions)

        # ---- SkyStore 原有字段 ----
        self.region_pairs_ttl = defaultdict(list)
        for region in self.init_regions:
            for region2 in self.init_regions:
                self.region_pairs_ttl[(region, region2)] = 0
        self.window_size = -1

        self.avgNetworkCost = get_avg_network_cost(self.stat_graph)
        self.avgStorageCost = get_avg_storage_cost(self.stat_graph)
        self.minNetworkCost = get_min_network_cost(self.stat_graph)
        self.medNetworkCost = get_median_network_cost(self.stat_graph)

        self.past_requests = {}
        self.hist = {}
        self.last_hist = {}
        self.num_requests = {}

        self.next_past_requests = {}
        self.next_hist = {}
        self.next_last_hist = {}
        self.next_num_requests = {}
        if self.window_size != -1:
            self.last_updated = None
        else:
            self.last_updated = datetime.fromtimestamp(0, tz=timezone.utc).replace(
                tzinfo=None
            )

        self.regions = self.init_regions
        self.region_pairs_ttl = defaultdict(list)
        for region in self.regions:
            for region2 in self.regions:
                self.region_pairs_ttl[(region, region2)] = []
        self.timestamps = self.read_timestamps_from_csv(
            os.environ.get("TRACE_FILE_PATH", "../experiment/trace/Tr065.typeE.3reg.tevict.prototype"),
            "timestamp"
        )

        # Build idx->key and key->time_to_next lookup tables (Dedup-Aware)
        self._build_key_mappings(
            os.environ.get("TRACE_FILE_PATH", "../experiment/trace/Tr065.typeE.3reg.tevict.prototype")
        )

        self.k = 12
        self.seen_days = None
        self.storage_cost = defaultdict(int)
        self.network_cost = []
        self.previous_hour = None
        self.hits = 0
        self.miss = 0

        # ---- 新增: 去重感知专用字段 ----
        self._dedup_key: str = None
        self._dedup_time_to_next: float = None        # 秒
        self._dedup_lock = threading.Lock()

        self._obj_state: dict = {}                     # key -> ObjState
        self._obj_chunk_refs: dict = {}                # key -> [chunk manifest]
        self._obj_manifest_size: dict = {}              # key -> manifest bytes (for cost tracking)

        self._transitions_to_hot: int = 0
        self._transitions_to_dedup: int = 0

        # ---- 新增: Chunk 局域性追踪 (per-region) ----
        # L = local_hits / total_chunks_ever_checked
        self._chunk_local_hits: dict = {}   # region -> cumulative local hit count
        self._chunk_total_checks: dict = {} # region -> cumulative chunk check count
        self._chunk_locality_lock = threading.Lock()

        # OCP (P7): persist HOT/DEDUP state + manifests across restarts
        self._state_path = os.environ.get(
            "DEDUP_POLICY_STATE_PATH", "/tmp/dedup_policy_state.json"
        )
        self._load_persisted_state()

    # ===============================================================
    #  单例模式
    # ===============================================================

    @classmethod
    def get_instance(cls, init_regions: List[str]):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(init_regions=init_regions)
        return cls._instance

    # ===============================================================
    #  去重感知层: 上下文桥接 (GET handler 在 get_ttl 前调用)
    # ===============================================================

    def set_key_context(self, key: str, time_to_next: float = None):
        """GET handler 调用，注入 time_to_next_access 信息。"""
        with self._dedup_lock:
            self._dedup_key = key
            self._dedup_time_to_next = time_to_next

    def get_obj_state(self, key: str) -> str:
        with self._dedup_lock:
            return self._obj_state.get(key, ObjState.HOT)  # 默认 HOT 保证兼容

    def set_obj_chunk_refs(self, key: str, refs: list):
        with self._dedup_lock:
            self._obj_chunk_refs[key] = refs
            # 记录 manifest 序列化后大小，用于 DEDUP 对象存储成本计算
            import json
            try:
                manifest_bytes = json.dumps(refs, separators=(",", ":")).encode("utf-8")
                self._obj_manifest_size[key] = len(manifest_bytes)
            except (TypeError, ValueError):
                self._obj_manifest_size[key] = len(refs) * 120  # fallback estimate
            self._persist_state_unlocked()

    def get_obj_chunk_refs(self, key: str) -> list:
        with self._dedup_lock:
            return self._obj_chunk_refs.get(key, [])

    def _load_persisted_state(self):
        """Load sidecar JSON written by prior process (best-effort)."""
        import json
        try:
            if not os.path.isfile(self._state_path):
                return
            with open(self._state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._obj_state = data.get("obj_state", {}) or {}
            self._obj_chunk_refs = data.get("obj_chunk_refs", {}) or {}
            self._obj_manifest_size = {
                k: int(v) for k, v in (data.get("obj_manifest_size", {}) or {}).items()
            }
            self._chunk_local_hits = {
                k: int(v) for k, v in (data.get("chunk_local_hits", {}) or {}).items()
            }
            self._chunk_total_checks = {
                k: int(v) for k, v in (data.get("chunk_total_checks", {}) or {}).items()
            }
        except Exception as e:
            print(f"[dedup] state load skipped: {e}")

    def _persist_state_unlocked(self):
        """Caller must hold _dedup_lock."""
        import json
        try:
            payload = {
                "obj_state": self._obj_state,
                "obj_chunk_refs": self._obj_chunk_refs,
                "obj_manifest_size": self._obj_manifest_size,
                "chunk_local_hits": self._chunk_local_hits,
                "chunk_total_checks": self._chunk_total_checks,
            }
            tmp = self._state_path + ".tmp"
            parent = os.path.dirname(self._state_path) or "/tmp"
            os.makedirs(parent, exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f)
            os.replace(tmp, self._state_path)
        except Exception as e:
            print(f"[dedup] state persist skipped: {e}")

    def get_obj_manifest_size(self, key: str) -> int:
        """返回 DEDUP 对象的 chunk manifest 存储大小（字节），用于成本计算。"""
        with self._dedup_lock:
            return self._obj_manifest_size.get(key, 0)

    # ===============================================================
    #  去重感知层: Chunk 局域性追踪
    # ===============================================================

    def record_chunk_locality(self, region: str, local_hits: int, total_chunks: int):
        """PUT/GET handler 调用: 记录此次操作中目标 region 已有的 chunk 数。

        在去重引擎完成 chunk fingerprint 查重后调用:
          - region: 目标存储区域名 (e.g. "aws:us-east-1")
          - local_hits: 在该 region 已存在的 chunk 数
          - total_chunks: 该对象的总 chunk 数

        用于计算 L = local_hits / total_chunks 的累计移动平均。
        """
        if total_chunks <= 0:
            return
        with self._chunk_locality_lock:
            self._chunk_local_hits[region] = (
                self._chunk_local_hits.get(region, 0) + local_hits
            )
            self._chunk_total_checks[region] = (
                self._chunk_total_checks.get(region, 0) + total_chunks
            )
        # keep sidecar in sync (acquire dedup lock briefly)
        with self._dedup_lock:
            self._persist_state_unlocked()

    def get_chunk_L(self, region: str) -> float:
        """返回目标 region 的 chunk 局域性 L ∈ [0, 1]。

        L = 该 region 累计本地命中 chunk 数 / 累计检查 chunk 总数。
        若该 region 尚无统计，返回 0.0 (悲观假设: 无本地 chunk)。
        """
        with self._chunk_locality_lock:
            total = self._chunk_total_checks.get(region, 0)
            if total == 0:
                return 0.0
            return self._chunk_local_hits.get(region, 0) / total

    # ===============================================================
    #  SkyStore 原有方法（完整保留）
    # ===============================================================

    def add_to_network_cost(self, src, dst, size):
        if src == dst:
            n_cost = 0
        else:
            if self.stat_graph.has_edge(src, dst):
                n_cost = self.stat_graph[src][dst]["cost"] * (size / GB)
            else:
                n_cost = "-1"
        self.network_cost.append(n_cost)

    def add_to_cost(self, timedelta: int, region: str, size):
        added_cost = (
            timedelta
            / 3600
            / 24
            * self.stat_graph.nodes[region]["priceStorage"]
            * 3
            * size
            / (1024 * 1024 * 1024)
        )
        self.storage_cost[region] += added_cost

    def place(self, req: StartUploadRequest) -> List[str]:
        return [req.client_from_region]

    def is_next_window(self, old_datetime: datetime, new_datetime: datetime):
        time_difference = new_datetime - old_datetime
        return time_difference > timedelta(hours=self.window_size)

    def is_large_time_gap(self, old_datetime: datetime, new_datetime: datetime):
        time_difference = abs(new_datetime - old_datetime)
        return time_difference > timedelta(hours=self.window_size * 2)

    def round_to_next_hour(self, dt: datetime):
        if dt.minute != 0 or dt.second != 0 or dt.microsecond != 0:
            dt += timedelta(hours=1)
            dt = dt.replace(minute=0, second=0, microsecond=0)
        else:
            dt += timedelta(hours=1)
        return dt

    def round_to_next_hour_seconds(self, dt: int):
        return (dt // 3600 + 1) * 3600

    def update_past_requests(
        self, cur_timestamp_idx: int, response: LocateObjectResponse, place_region: str
    ):
        with DedupAwareTevict._lock:
            cur_timestamp = datetime.fromtimestamp(
                self.timestamps[cur_timestamp_idx] / 1000, tz=timezone.utc
            ).replace(tzinfo=None)

            if self.window_size != -1:
                if self.last_updated is None or self.is_large_time_gap(
                    self.last_updated, cur_timestamp
                ):
                    self.last_updated = cur_timestamp.replace(
                        minute=0, second=0, microsecond=0
                    )
                    self.past_requests = {}
                    self.hist = {}
                    self.num_requests = {}
                    self.last_hist = {}
                    self.next_last_hist = {}
                    self.next_past_requests = {}
                    self.next_hist = {}
                    self.next_num_requests = {}
                elif self.is_next_window(self.last_updated, cur_timestamp):
                    self.last_updated = self.last_updated + timedelta(
                        hours=self.window_size
                    )
                    self.past_requests = self.next_past_requests
                    self.hist = self.next_hist
                    self.last_hist = self.next_last_hist
                    self.num_requests = self.next_num_requests
                    self.next_past_requests = {}
                    self.next_hist = {}
                    self.next_num_requests = {}
                    self.next_last_hist = {}

            tnext = 0
            self.next_num_requests[place_region] = (
                self.next_num_requests.get(place_region, 0) + 1
            )
            if place_region not in self.next_past_requests:
                self.next_past_requests[place_region] = {}
            if response.key in self.next_past_requests[place_region]:
                req_to_sec = (
                    cur_timestamp
                    - datetime.fromtimestamp(0, tz=timezone.utc).replace(tzinfo=None)
                ).total_seconds()
                tnext = (
                    req_to_sec - self.next_past_requests[place_region][response.key][-1]
                )
                if tnext == 0:
                    tnext = 1

                curentWindow = int(
                    math.ceil(
                        (
                            self.round_to_next_hour_seconds(
                                self.next_past_requests[place_region][response.key][-1]
                            )
                            - self.next_past_requests[place_region][response.key][-1]
                        )
                        / 3600
                    )
                )

                if (
                    place_region in self.next_last_hist
                    and curentWindow in self.next_last_hist[place_region]
                ):
                    self.next_last_hist[place_region][curentWindow] -= (
                        response.size / GB
                    )

                if int(math.ceil(tnext / 3600)) not in self.next_hist[place_region]:
                    self.next_hist[place_region][int(math.ceil(tnext / 3600))] = 0

                self.next_hist[place_region][int(math.ceil(tnext / 3600))] += (
                    response.size / GB
                )
                self.next_past_requests[place_region][response.key].append(
                    (
                        cur_timestamp
                        - datetime.fromtimestamp(0, tz=timezone.utc).replace(
                            tzinfo=None
                        )
                    ).total_seconds()
                )

                if place_region not in self.next_last_hist:
                    self.next_last_hist[place_region] = {}
                tendWindow = int(
                    math.ceil(
                        (
                            self.round_to_next_hour(cur_timestamp) - cur_timestamp
                        ).total_seconds()
                        / 3600
                    )
                )

                self.next_last_hist[place_region][tendWindow] = (
                    self.next_last_hist[place_region].get(tendWindow, 0)
                    + response.size / GB
                )
            else:
                tnext = (
                    cur_timestamp
                    - datetime.fromtimestamp(0, tz=timezone.utc).replace(tzinfo=None)
                ).total_seconds()
                if place_region not in self.next_hist:
                    self.next_hist[place_region] = {}
                self.next_past_requests[place_region][response.key] = []
                self.next_past_requests[place_region][response.key].append(
                    (
                        cur_timestamp
                        - datetime.fromtimestamp(0, tz=timezone.utc).replace(
                            tzinfo=None
                        )
                    ).total_seconds()
                )
                if place_region not in self.next_last_hist:
                    self.next_last_hist[place_region] = {}
                tendWindow = int(
                    math.ceil(
                        (
                            self.round_to_next_hour(cur_timestamp) - cur_timestamp
                        ).total_seconds()
                        / 3600
                    )
                )
                self.next_last_hist[place_region][tendWindow] = (
                    self.next_last_hist[place_region].get(tendWindow, 0)
                    + response.size / GB
                )

    def calc_evict_cost(self, region, teven_hours, net_cost, storage_cost_hour):
        if self.window_size == -1:
            if region not in self.next_hist or region not in self.next_last_hist:
                return -1, -1
            X = self.next_hist[region]
            last_X = self.next_last_hist[region]
        else:
            if region not in self.hist or region not in self.last_hist:
                return -1, -1
            X = self.hist[region]
            last_X = self.last_hist[region]

        cost_hist = {}
        if len(X.keys()) > 0:
            maxKey = int(max(X.keys()))
        else:
            maxKey = 1

        ret = 0
        for c in range(min(maxKey + 1, math.ceil(teven_hours))):
            c = int(c)
            if c not in cost_hist:
                cost_hist[c] = 0
            tevict = c
            for i in X:
                if i <= tevict:
                    assert i >= 1
                    cost_hist[c] += (
                        float(X.get(i, 0)) * ((i - 1) + 0.6) * storage_cost_hour
                    )

                else:
                    cost_hist[c] += float(X.get(i, 0)) * (
                        (tevict * storage_cost_hour) + net_cost
                    )

                cost_hist[c] += float(last_X.get(i, 0)) * (tevict * storage_cost_hour)

            if cost_hist[c] <= cost_hist[ret]:
                ret = c
        return (ret, cost_hist[ret])

    def get_tevict(self, src, dst):
        return self.region_pairs_ttl[(src, dst)][-1]

    def find_min(self, y, teven, region):
        noPastData = False
        if self.window_size == -1:
            if region not in self.next_num_requests:
                noPastData = True
                ttl = teven / 3600 / 2
            else:
                numRequests = self.next_num_requests[region]
        else:
            if region not in self.num_requests:
                noPastData = True
                ttl = teven / 3600 / 2
            else:
                numRequests = self.num_requests[region]

        if not noPastData:
            if y == -1 or self.last_updated is None or numRequests < 1000:
                ttl = teven / 3600 / 2
            else:
                ttl = y

        return ttl * 3600

    def name(self) -> str:
        return "dedup_aware_tevict"

    # ===============================================================
    #  ★ get_ttl — SkyStore 自适应计算 + 去重感知存储形式决策 (Dedup-Aware Formula)
    # ===============================================================

    def get_ttl(
        self,
        idx: int,
        src: str = None,
        dst: str = None,
        fixed_base_region: bool = False,
    ) -> int:
        """SkyStore 自适应 TTL + 去重感知存储形式决策。

        Phase 1:  运行 SkyStore 自适应 TTL 计算（histogram → calc_evict_cost → find_min）
        Phase 2:  如果 Phase 1 决定缓存（adaptive_ttl > 0）:
                    使用 Dedup-Aware 公式做存储形式决策:

                        τ_effective = (1 - L_dst) × T_even(src, dst)
                                       = (1 - L_dst) × N / S

                    其中 L_dst 是目标区域 dst 的 chunk 局域性。

                    time_to_next ≤ τ_effective  →  HOT_STATE
                      (下次访问在盈亏平衡点内, 存完整物理副本)
                    time_to_next > τ_effective  →  DEDUP_STATE
                      (盈亏平衡点内等不到下次访问, 仅保留 chunk 引用)

        公式推导见 experiment_logs/dedup_aware_tevict_strategy_20260809.md

        返回:
            int   TTL 秒数, -1 表示 base region 永不过期
        """
        if fixed_base_region:
            return -1

        # ===== Phase 1: SkyStore 自适应 TTL 计算 =====
        adaptive_ttl = self._compute_skystore_ttl(idx, src, dst)

        if adaptive_ttl <= 0:
            return 0

        # ===== Phase 2: Dedup-Aware 存储形式决策 =====
        # 内部查找: 通过 idx 映射 obj_key → 计算 time_to_next
        # 不依赖外部 set_key_context() 调用
        key = self._idx_to_key.get(idx)
        ttn = None
        if key is not None and idx < len(self.timestamps):
            current_ts = self.timestamps[idx]
            ttn = self._key_time_to_next.get((key, current_ts))

        # 在锁外计算 tau_effective (只读 get_chunk_L, 有独立锁)
        teven_sec = self._compute_teven(src, dst)
        L = self.get_chunk_L(dst) if dst else 0.0
        tau_effective = (1.0 - L) * teven_sec

        if ttn is None or ttn <= 0 or key is None:
            # 未知下次访问时间 → 不做决定，不修改 _obj_state
            return 0

        # 所有 _obj_state / _transitions 修改必须在锁内
        with self._dedup_lock:
            if ttn <= tau_effective:
                # HOT: time_to_next 在 τ_effective 内 → 存完整副本, 本地命中 0 传输
                self._obj_state[key] = ObjState.HOT
                self._transitions_to_hot += 1
                self._persist_state_unlocked()
                return adaptive_ttl
            else:
                # DEDUP: τ_effective 内等不到下次访问 → 仅存 chunk 引用, 节省存储
                self._obj_state[key] = ObjState.DEDUP
                self._transitions_to_dedup += 1
                self._persist_state_unlocked()
                return 0

    # ---------------------------------------------------------------
    #  内部: 提取 SkyStore 的 TTL 计算逻辑（与 SkyStore.get_ttl 同）
    # ---------------------------------------------------------------

    def _compute_skystore_ttl(self, idx: int, src: str, dst: str) -> int:
        with DedupAwareTevict._lock:
            now_timestamp = datetime.fromtimestamp(
                self.timestamps[idx] / 1000, tz=timezone.utc
            ).replace(tzinfo=None)
            if self.seen_days is None or now_timestamp >= self.seen_days + timedelta(
                hours=self.k
            ):
                for region in self.regions:
                    for region2 in self.regions:
                        if region != region2:
                            net_cost = self.stat_graph[region][region2]["cost"]
                            storage = self.stat_graph.nodes[region2]["priceStorage"] * 3
                            storage_cost_per_hour = storage / 24
                            teven = net_cost / storage * 60 * 60 * 24

                            best_ttl, calculated_cost = self.calc_evict_cost(
                                region2, teven / 3600, net_cost, storage_cost_per_hour
                            )
                            self.region_pairs_ttl[(region, region2)].append(
                                self.find_min(best_ttl, teven, region2)
                            )
                self.seen_days = now_timestamp

            if src is None:
                return int(
                    self.medNetworkCost
                    / self.stat_graph.nodes[dst].get("priceStorage", 0.026)
                    * 60 * 60 * 24 / 3
                )

            return int(self.get_tevict(src, dst))

    # ---------------------------------------------------------------
    #  内部: T_even = N / S (秒)
    # ---------------------------------------------------------------

    def _compute_teven(self, src: str, dst: str) -> float:
        """计算盈亏平衡时间 T_even = N / S (秒)。

        使用 N = src→dst 网络传输单价 ($/GB)
            S = dst 存储单价 × 3 (3副本, $/GB/day) → 转换为 $/GB/s

        T_even = N / (S_replicated * seconds_per_day)
               = net_cost / (storage_price * 3) * 86400

        与 SkyStore._compute_skystore_ttl 中 teven 计算一致（line 605）:
            teven = net_cost / storage * 60 * 60 * 24
        其中 storage = priceStorage * 3

        注意: priceStorage 单位是 $/GB/day（非 $/GB/month），
        与 add_to_cost() 中 timedelta/3600/24 * priceStorage * 3 一致。

        返回:
            float  T_even 秒数
        """
        if src is None or dst is None:
            # fallback: 使用中位网络成本 + 平均存储成本 * 3
            default_net = self.medNetworkCost if self.medNetworkCost > 0 else 0.02
            default_storage = self.avgStorageCost if self.avgStorageCost > 0 else 0.026
            return (default_net / (default_storage * 3.0)) * 86400.0

        if not self.stat_graph.has_edge(src, dst):
            default_net = self.medNetworkCost if self.medNetworkCost > 0 else 0.02
            default_storage = self.stat_graph.nodes[dst].get("priceStorage", 0.026)
            return (default_net / (default_storage * 3.0)) * 86400.0

        net_cost = self.stat_graph[src][dst].get("cost", 0.02)
        storage_price = self.stat_graph.nodes[dst].get("priceStorage", 0.026)

        if net_cost <= 0:
            net_cost = self.medNetworkCost if self.medNetworkCost > 0 else 0.02
        if storage_price <= 0:
            storage_price = 0.026

        # T_even = N / (S * 3) * 86400 (seconds/day)
        # 与 policy_skystore.py 的 calc_evict_cost 中 teven 计算完全一致
        return (net_cost / (storage_price * 3.0)) * 86400.0

    # ===============================================================
    #  统计
    # ===============================================================

    def stats(self) -> dict:
        with self._dedup_lock:
            n_hot = sum(1 for s in self._obj_state.values() if s == ObjState.HOT)
            n_dedup = sum(1 for s in self._obj_state.values() if s == ObjState.DEDUP)
        with self._chunk_locality_lock:
            locality = {}
            for region in self._chunk_total_checks:
                total = self._chunk_total_checks[region]
                if total > 0:
                    L = self._chunk_local_hits.get(region, 0) / total
                    locality[region] = round(L, 4)
        total_storage = sum(self.storage_cost.values())
        total_network = sum(self.network_cost) if self.network_cost else 0.0
        total_ops = max(self.hits + self.miss, 1)
        total_objects = n_hot + n_dedup
        avg_manifest_bytes = (
            sum(self._obj_manifest_size.values()) / max(len(self._obj_manifest_size), 1)
            if self._obj_manifest_size else 0
        )
        return {
            "name": self.name(),
            "hits": self.hits,
            "misses": self.miss,
            "hit_rate": round(self.hits / total_ops, 4),
            "storage_cost": dict(self.storage_cost),
            "storage_cost_total": round(total_storage, 6),
            "network_cost": round(total_network, 6),
            "total_cost": round(total_storage + total_network, 6),
            "objects_hot": n_hot,
            "objects_dedup": n_dedup,
            "objects_total": total_objects,
            "transitions_to_hot": self._transitions_to_hot,
            "transitions_to_dedup": self._transitions_to_dedup,
            "chunk_locality_L": locality,
            "avg_manifest_bytes": round(avg_manifest_bytes, 1),
        }
