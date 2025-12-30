"""Construct graph and run Louvain and Leiden community detection. Produce partitions and comparison metrics.

What the script does (step-by-step):
1. Load CSVs: `data/apps.csv`, `data/dependencies.csv`, `data/servers.csv`, `data/databases.csv`.
2. Build a directed NetworkX graph `G` with nodes for applications/servers/databases and node attributes (`type`, `env`, `BCP_score`, `BCP_tier`).
3. Add directed edges with attributes (`weight`, `dependency_type`, `data_flow_score`).
4. Project to an undirected weighted graph `Gu` by aggregating parallel edges and summing weights (Louvain/Leiden operate on this).
5. Run **Louvain** on `Gu` to get a node→community mapping and compute modularity.
6. Prepare an `igraph.Graph` (integer node indices, edge weights) and run **Leiden** to get a membership list and modularity.
7. Format communities, save JSON outputs (`outputs/communities_louvain.json`, `outputs/communities_leiden.json`), save `community_metrics.csv`, and pickle `Gu` for visualization (`outputs/graph_undirected.gpickle`).

How Louvain works (steps):
1. Initialize each node in its own community.
2. Repeatedly consider moving nodes to neighboring communities if the move increases modularity (greedy local improvement).
3. When no single-node move improves modularity, aggregate current communities into super-nodes and repeat the local moving on the coarse graph.
4. Iterate the move+aggregate steps until modularity no longer improves.
5. Output: final partition mapping. Louvain is fast but can get stuck in local optima and may produce less well-connected communities.

How Leiden works (steps):
1. Initialize communities (each node alone or using a seed partition).
2. Perform local moving to improve the quality function (e.g., modularity).
3. Refine communities by splitting poorly connected subparts to ensure communities are internally well connected (this addresses a Louvain limitation).
4. Aggregate the refined communities into a coarse graph and repeat the move-refine-aggregate cycle until stable.
5. Output: a partition that is typically more robust and better connected than Louvain's result.

Notes:
- Both algorithms operate on undirected weighted graphs; higher modularity indicates stronger community structure.
- Typical outputs: `communities_louvain.json`, `communities_leiden.json`, `community_metrics.csv`, and `graph_undirected.gpickle`.
"""
import pandas as pd
import networkx as nx
import time
from pathlib import Path
import community as community_louvain
import igraph as ig
import leidenalg
import json
import numpy as np

DATA = Path(__file__).resolve().parent.parent / 'data'
OUT = Path(__file__).resolve().parent.parent / 'outputs'
OUT.mkdir(exist_ok=True)

# Load data
apps = pd.read_csv(DATA / 'apps.csv')
deps = pd.read_csv(DATA / 'dependencies.csv')
servers = pd.read_csv(DATA / 'servers.csv')
dbs = pd.read_csv(DATA / 'databases.csv')

# Build networkx graph
G = nx.DiGraph()

# Add nodes with attributes
for _, r in apps.iterrows():
    G.add_node(r['app_instance_id'], type='application', env=r['env'], BCP_score=r['BCP_score'], BCP_tier=r['BCP_tier'])
for _, r in servers.iterrows():
    G.add_node(r['server_id'], type='server', env=r['env'])
for _, r in dbs.iterrows():
    G.add_node(r['db_id'], type='database', env=r['env'])

# Add edges
for _, r in deps.iterrows():
    # keep weight attr
    G.add_edge(r['source'], r['target'], weight=float(r['weight']), dependency_type=r['dependency_type'], data_flow_score=int(r['data_flow_score']))

print('Nodes:', G.number_of_nodes(), 'Edges:', G.number_of_edges())

# For Louvain we need an undirected weighted graph
Gu = nx.Graph()
for u,v,data in G.edges(data=True):
    w = data.get('weight',1.0)
    if Gu.has_edge(u,v):
        Gu[u][v]['weight'] += w
    else:
        Gu.add_edge(u,v,weight=w)

# Louvain
start = time.time()
partition_louvain = community_louvain.best_partition(Gu, weight='weight')
ltime = time.time() - start

# Leiden requires igraph
# Map nodes to indices
nodes = list(Gu.nodes())
index = {n:i for i,n in enumerate(nodes)}
edges = [(index[u], index[v], d['weight']) for u,v,d in Gu.edges(data=True)]
# Build igraph
g_ig = ig.Graph()
g_ig.add_vertices(len(nodes))
# add weighted edges undirected
edge_tuples = [(e[0],e[1]) for e in edges]
wts = [e[2] for e in edges]
if len(edge_tuples) > 0:
    g_ig.add_edges(edge_tuples)
    g_ig.es['weight'] = wts

start = time.time()
partition_leiden = {}
if g_ig.vcount() > 0:
    # use Leiden to partition the graph (returns a Partition object with .membership list)
    leiden_part = leidenalg.find_partition(g_ig, leidenalg.ModularityVertexPartition, weights='weight')
    membership = leiden_part.membership
    # map back to node labels
    partition_leiden = {nodes[i]: int(membership[i]) for i in range(len(nodes))}
ltime = time.time() - start

# Format partitions
# Louvain communities
from collections import defaultdict
comm_louv = defaultdict(list)
for node, com in partition_louvain.items():
    comm_louv[com].append(node)

comm_leiden = defaultdict(list)
for node, com in partition_leiden.items():
    comm_leiden[com].append(node)

# Save communities
with open(OUT / 'communities_louvain.json', 'w') as f:
    json.dump({str(k):v for k,v in comm_louv.items()}, f, indent=2)
with open(OUT / 'communities_leiden.json', 'w') as f:
    json.dump({str(k):v for k,v in comm_leiden.items()}, f, indent=2)

# Compute modularity for partitions
mod_louv = community_louvain.modularity(partition_louvain, Gu, weight='weight')
# For leiden, compute modularity via igraph
# build membership list ordered by nodes
membership = [partition_leiden[n] for n in nodes]
mod_leiden = g_ig.modularity(membership, weights=wts)

metrics = {
    'louvain': {'num_communities': len(comm_louv), 'modularity': mod_louv},
    'leiden': {'num_communities': len(comm_leiden), 'modularity': mod_leiden}
}

pd.DataFrame([{'algorithm':'louvain','num_communities':len(comm_louv),'modularity':mod_louv,'runtime_seconds':None},{'algorithm':'leiden','num_communities':len(comm_leiden),'modularity':mod_leiden,'runtime_seconds':None}]).to_csv(OUT / 'community_metrics.csv', index=False)
print('Saved community metrics and partitions to outputs')

# Save the Gu for visualizations using pickle to avoid NetworkX version issues
import pickle
with open(OUT / 'graph_undirected.gpickle', 'wb') as f:
    pickle.dump(Gu, f)
print('Done')
