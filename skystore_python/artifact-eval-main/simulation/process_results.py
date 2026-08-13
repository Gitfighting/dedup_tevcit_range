import json
import pandas as pd
import os

def process_results(json_path, output_path):
    if not os.path.exists(json_path):
        print(f"Error: {json_path} not found.")
        return

    with open(json_path, 'r') as f:
        data = json.load(f)

    # Flatten the data
    # Assuming data is a dictionary where keys are trace/policy identifiers or a list of result objects
    # Based on previous views, it seems to be a dictionary where keys might be "T15_AlwaysStore" etc.
    # Let's inspect the structure dynamically.
    
    rows = []
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, dict):
                row = value.copy()
                row['experiment_id'] = key
                rows.append(row)
            else:
                print(f"Unexpected structure for key {key}: {value}")
    elif isinstance(data, list):
         rows = data
    
    if not rows:
        print("No data found to process.")
        return

    df = pd.DataFrame(rows)
    
    # Reorder columns if needed, prioritize key metrics
    cols = list(df.columns)
    priority_cols = ['experiment_id', 'trace', 'policy', 'total_cost', 'storage_cost', 'network_cost', 'avg_latency']
    ordered_cols = [c for c in priority_cols if c in cols] + [c for c in cols if c not in priority_cols]
    df = df[ordered_cols]

    df.to_csv(output_path, index=False)
    print(f"Successfully wrote results to {output_path}")
    print(df.head())

if __name__ == "__main__":
    json_path = r"d:\JavaProjects\Skystore1212\skystore_python\artifact-eval-main\simulation\simulation_results.json"
    output_path = r"d:\JavaProjects\Skystore1212\skystore_python\artifact-eval-main\simulation\simulation_report.csv"
    process_results(json_path, output_path)
