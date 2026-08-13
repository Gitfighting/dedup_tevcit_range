import networkx as nx
from src.utils.helpers import make_nx_graph

def check_price():
    G = make_nx_graph()
    target_regions = ["aws:us-east-1", "aws:us-west-2"]
    for node in target_regions:
        price = G.nodes[node].get("priceStorage")
        print(f"Region: {node}, Price: {price}, Price*3: {price*3 if price else 'N/A'}")

if __name__ == "__main__":
    check_price()
