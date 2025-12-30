"""Generate visualizations, heatmaps, and reports for migration waves and dependencies."""
import pandas as pd
from pathlib import Path
import networkx as nx
import matplotlib.pyplot as plt
import seaborn as sns
import json
import numpy as np

DATA = Path(__file__).resolve().parent.parent / 'data'
OUT = Path(__file__).resolve().parent.parent / 'outputs'
OUT.mkdir(exist_ok=True)

apps = pd.read_csv(DATA / 'apps.csv')
deps = pd.read_csv(DATA / 'dependencies.csv')

# Load waves
with open(OUT / 'waves_louvain.json') as f:
    waves_louv = json.load(f)
with open(OUT / 'waves_leiden.json') as f:
    waves_leid = json.load(f)

# Build application-only graph (undirected weighted) for visualization
app_deps = deps[(deps['source_type']=='application') & (deps['target_type']=='application')]
G = nx.Graph()
for _, r in apps.iterrows():
    G.add_node(r['app_instance_id'], BCP_score=r['BCP_score'], BCP_tier=r['BCP_tier'], env=r['env'])
for _, r in app_deps.iterrows():
    u,v,w = r['source'], r['target'], r['weight']
    if G.has_edge(u,v):
        G[u][v]['weight'] += w
    else:
        G.add_edge(u,v,weight=w)

# Node colors by BCP tier
tier_colors = {'Mission Critical':'#d62728','Business Critical':'#ff7f0e','Business Operational':'#1f77b4','Non-Critical':'#2ca02c'}
colors = [tier_colors.get(G.nodes[n]['BCP_tier'],'#7f7f7f') for n in G.nodes()]
sizes = [50 + 20*G.nodes[n]['BCP_score'] for n in G.nodes()]

plt.figure(figsize=(12,10))
pos = nx.spring_layout(G, seed=42, k=0.15)
nx.draw_networkx_nodes(G, pos, node_color=colors, node_size=sizes, alpha=0.9)
# draw edges with linewidth scaled by weight
weights = [G[u][v]['weight'] for u,v in G.edges()]
maxw = max(weights) if weights else 1
nx.draw_networkx_edges(G, pos, width=[1+3*(w/maxw) for w in weights], alpha=0.6)
plt.title('Application dependency graph colored by BCP tier')
plt.axis('off')
plt.tight_layout()
plt.savefig(OUT / 'graph_bcp_colored.png', dpi=200)
plt.close()

# Dependency heatmap (apps are many; we'll show a reduced heatmap for top N by BCP or degree)
# pick top 60 apps by degree
deg = dict(G.degree())
top = sorted(deg.keys(), key=lambda x:deg[x], reverse=True)[:60]
mat = pd.DataFrame(0, index=top, columns=top, dtype=float)
for u,v,data in G.edges(data=True):
    if u in top and v in top:
        mat.loc[u,v] = data['weight']
        mat.loc[v,u] = data['weight']

plt.figure(figsize=(14,12))
sns.heatmap(mat, cmap='Reds')
plt.title('Dependency weight heatmap (top 60 apps)')
plt.savefig(OUT / 'dependency_heatmap_top60.png', dpi=200)
plt.close()

# Business impact per wave (for louvain and leiden)
def wave_business_impact(waves_json):
    rows = []
    for env,wlist in waves_json.items():
        for idx,w in enumerate(wlist):
            subset = apps[apps['app_instance_id'].isin(w)]
            total_fin = subset['financial_impact_k_per_hour'].sum()
            avg_bcp = subset['BCP_score'].mean()
            mission_ct = (subset['BCP_score']>=9).sum()
            rows.append({'env':env,'wave_index':idx,'num_apps':len(w),'total_fin_k_per_hour':total_fin,'avg_bcp':avg_bcp,'mission_critical_count':mission_ct})
    return pd.DataFrame(rows)

bi_louv = wave_business_impact(waves_louv)
bi_leid = wave_business_impact(waves_leid)
bi_louv.to_csv(OUT / 'business_impact_waves_louvain.csv', index=False)
bi_leid.to_csv(OUT / 'business_impact_waves_leiden.csv', index=False)

# Validation checklist: for each wave ensure RTO/RPO earliest tests
# Build checklist rows
checklist = []
for algo,waves in [('louvain',waves_louv),('leiden',waves_leid)]:
    for env,wlist in waves.items():
        for idx,w in enumerate(wlist):
            subset = apps[apps['app_instance_id'].isin(w)]
            # Determine highest BCP in wave
            max_bcp = subset['BCP_score'].max()
            # Determine whether all apps have RTO <= some threshold when mission critical
            # We'll list per-wave items
            checklist.append({'algorithm':algo,'env':env,'wave_index':idx,'num_apps':len(w),'max_bcp':float(max_bcp),'rto_max_hours':float(subset['RTO_hours'].max())})

pd.DataFrame(checklist).to_csv(OUT / 'validation_checklist.csv', index=False)

print('Visuals and reports generated in outputs/')
