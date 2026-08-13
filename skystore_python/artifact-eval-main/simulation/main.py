# from src.simulator import Simulator
from src.simulator_v2 import SimulatorV2
import argparse

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SkyStorage Simulator")
    parser.add_argument(
        "--config", default="config/spanstore.yaml", help="Path to config file"
    )
    parser.add_argument(
        "--trace",
        default="traces/mc_dir/fixed/IBMObjectStoreTrace003Part0.typeA.mc",
        help="Path to trace file",
    )
    parser.add_argument("--vm", default=5, help="Number of VMs")
    # add a bool variable to set base region
    parser.add_argument("--setbase", action='store_true', help="Set base region (use flag to enable FB mode, omit for FP mode)")
    parser.add_argument("--days", default=0, help="Only calculate cost after Day [days]")
    parser.add_argument("--simversion", default="0", help="Simulator version")
    parser.add_argument("--tag", default="v2", help="Tag for results file (default: v2)")
    args = parser.parse_args()
    if args.simversion == "0":
        # Fallback to SimulatorV2 since Simulator is missing
        simulator = SimulatorV2(
            args.config,
            args.trace,
            int(args.vm),
            args.setbase,  # Now correctly boolean
            int(args.days),
            version_enable=True,
            tag=args.tag,
        )
    else:
        simulator = SimulatorV2(
            args.config,
            args.trace,
            int(args.vm),
            args.setbase,  # Now correctly boolean
            int(args.days),
            version_enable=True,
            tag=args.tag,
        )  # , store_decision = True
    simulator.run()

    # Print metrics to console FIRST (this also adds storage costs to tracker)
    # simulator.plot_graphs(graphs)
    simulator.report_metrics()

    # Save results to JSON AFTER report_metrics (tracker now has complete data)
    import json
    import os
    from datetime import datetime

    metrics = simulator.tracker.get_metrics()
    
    # Add context info
    metrics['trace'] = args.trace
    metrics['config'] = args.config
    metrics['timestamp'] = datetime.now().isoformat()
    metrics['tag'] = args.tag
    
    results_file = f"simulation_results_{args.tag}.json"
    
    # Read existing data or init empty list
    if os.path.exists(results_file):
        try:
            with open(results_file, 'r') as f:
                data = json.load(f)
                if not isinstance(data, list):
                    data = []
        except:
            data = []
    else:
        data = []
        
    data.append(metrics)
    
    with open(results_file, 'w') as f:
        json.dump(data, f, indent=4, default=str)

    # Print metrics to console AFTER saving JSON
    # simulator.plot_graphs(graphs)
    simulator.report_metrics()
