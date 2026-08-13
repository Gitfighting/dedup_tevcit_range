MILP-ECnew vs Replica: Quick Validation Notes

1. Where paths diverge
- Replica: no per-region EC decision state, transfer size typically falls back to full request size.
- MILP-ECnew: per-region decisions are cached as RegionDecision(mode, shardAt, ttlHours), and EC branch uses shard-sized transfer and placement.

2. Real-cost impact points
- Write placement size:
  Replica -> full req.size per placed copy.
  MILP-ECnew -> req.size/EC_K per shard region.
- Read transfer size:
  Replica -> full req.size.
  MILP-ECnew -> req.size/EC_K for EC-layout object.

3. Storage settlement
- Both branches eventually settle storage by RegionManager based on PhysicalObject size and residence duration.
- Therefore, MILP-ECnew only becomes truly EC-cost-faithful when shard sizes are passed into object creation and transfer metric calculations.

4. Runtime prerequisite
- milp_ec_new should run on SimulatorV3 path so getPlacementSizesForWrite and getReadTransferSize are delegated to placement policy.
