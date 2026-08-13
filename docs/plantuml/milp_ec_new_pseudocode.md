MILP-ECnew Pseudocode (from dataset ingest to real cost)

Layer L0: Input and request normalization
1. Open trace CSV.
2. For each row:
3.   Parse timestamp, operation, object key, issue region, object size.
4.   Map GET -> read, PUT -> write.
5.   Ensure LogicalObject exists in memory.

Layer L1: Policy decision and state update
6. If op == write:
7.   placeRegions <- PolicyMilpECnew.place(req, config)
8.   Policy side:
9.     windowStats[issueRegion].writeSizeGB += req.sizeGB
10.    checkAndSolveWindow(req.timestamp)
11.    If RegionDecision for issueRegion is EC and valid:
12.      return 3 shard regions
13.    else:
14.      return [issueRegion]
15.   placementSizes <- getPlacementWithSizes(req, config)
16.   If EC: placementSizes[r] = req.size / EC_K for each shard region
17. If op == read:
18.   PolicyMilpECnew.updatePastRequests(req, issueRegion)
19.   Policy side:
20.     if current decision does not guarantee local data:
21.       windowStats[issueRegion].readSizeGB += req.sizeGB
22.     checkAndSolveWindow(req.timestamp)
23.   readTransferSize <- getReadTransferSize(req, config)
24.   If object is EC layout: readTransferSize = req.size / EC_K

Layer L2: Transfer execution and object state mutation
25. Build transfer graph(s) using transfer policy.
26. For each transfer graph:
27.   compute transfer latency/throughput path values
28.   if write and destination already has old object:
29.     RegionManager.removeObjectFromRegion(dst, oldObj, replacementTime)
30.   create new PhysicalObject(size = placementSizes[dst] if exists else req.size)
31.   ttl <- calculateTtl(...)
32.   add object into logicalObjects[objKey].physicalObjects[dst]
33.   RegionManager.addObjectToRegion(dst, newObj)

Layer L3: Runtime cost accounting (real simulation cost)
34. effectiveSize <- readTransferSize for read or placementSizes[dst] for write or req.size fallback
35. metrics <- _updateTransferMetric(graph, req, effectiveSize)
36. tracker.addTransferCost(metrics.cost)
37. if read:
38.   tracker.addRequestCost(priceGet(readRegion))
39. if write:
40.   for each placed region r:
41.     tracker.addRequestCost(pricePut(r))

Layer L4: End-of-trace settlement and aggregate
42. cleanupPolicy(): PolicyMilpECnew.flushPendingEvents(now)
43. RegionManager.calculateRemainingStorageCosts(endTime, logicalObjects)
44. tracker.addStorageCost(RegionManager.aggregateStorageCost())
45. metrics <- tracker.getMetrics()
46. totalCost = totalTransferCost + totalStorageCost + totalRequestCost
47. write results json

MILP core (inside checkAndSolveWindow)
A. Trigger when window full or enough events.
B. For each active issue region with non-empty RegionStats:
C.   Build one MILP with variables:
D.     d (replica vs EC), sigma[i], hatTau, z[i], vread[i]
E.   Constraints:
F.     sum sigma = EC_N * d
G.     sigma[issueRegion] >= d
H.     hatTau lower-bounded by selected shard tauH
I.     z linearizes sigma * hatTau
J.     read routing sum(vread) = (EC_K - 1) * d
K.   Objective combines:
L.     replica storage term
M.     EC shard storage term
N.     EC write distribution transfer term
O.     read pull term (replica approximation vs EC routed shard pull)
P.   Solve and cache RegionDecision(mode, shardAt, ttlHours).

State color map (for diagrams)
- Blue: input/driver state
- Green: policy and optimization state
- Yellow: transfer/object runtime state
- Purple: metric and billing counters
- Red: final storage settlement and aggregate output
