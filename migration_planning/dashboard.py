import streamlit as st
import pandas as pd
import json
from pathlib import Path
import networkx as nx
import plotly.graph_objects as go
import plotly.express as px

BASE = Path(__file__).resolve().parent
OUT = BASE / 'outputs'
DATA = BASE / 'data'

@st.cache_data
def load_data():
    apps = pd.read_csv(DATA / 'apps.csv')
    deps = pd.read_csv(DATA / 'dependencies.csv')
    with open(OUT / 'waves_louvain.json') as f:
        waves_louv = json.load(f)
    with open(OUT / 'waves_leiden.json') as f:
        waves_leid = json.load(f)
    return apps, deps, waves_louv, waves_leid

apps, deps, waves_louv, waves_leid = load_data()

st.set_page_config(page_title='Migration Waves Dashboard', layout='wide')
st.title('Migration Waves & Dependency Explorer')

# Sidebar controls
alg = st.sidebar.selectbox('Algorithm', ['louvain','leiden'])
env = st.sidebar.selectbox('Environment', ['nonprod','prod'])
# determine available waves
waves = waves_louv if alg == 'louvain' else waves_leid
num_waves = len(waves.get(env, []))
wave_idx = st.sidebar.slider('Wave index', 0, max(0, num_waves-1), 0)

min_bcp, max_bcp = st.sidebar.slider('BCP score range', 1.0, 10.0, (1.0, 10.0), step=0.5)
edge_thresh = st.sidebar.slider('Highlight critical dependency weight >', 0.0, 10.0, 7.0, step=0.1)

st.sidebar.markdown('---')
st.sidebar.markdown('Run with: `streamlit run dashboard.py`')

# selected wave apps
wave_apps = waves.get(env, [])[wave_idx] if num_waves>0 else []

st.header(f'Wave {wave_idx} ({env}) — {len(wave_apps)} apps')

if len(wave_apps) == 0:
    st.info('No apps in selected wave.')
else:
    df_wave = apps[apps['app_instance_id'].isin(wave_apps)].copy()
    df_wave = df_wave[(df_wave['BCP_score'] >= min_bcp) & (df_wave['BCP_score'] <= max_bcp)]
    st.subheader('Wave composition')
    st.dataframe(df_wave[['app_instance_id','app_type','BCP_score','BCP_tier','RTO_hours','RPO_minutes','financial_impact_k_per_hour']])
    st.download_button('Download wave CSV', df_wave.to_csv(index=False).encode('utf-8'), file_name=f'wave_{alg}_{env}_{wave_idx}.csv')

    # Build subgraph of app-to-app dependencies among apps in same env
    app_deps = deps[(deps['source_type']=='application') & (deps['target_type']=='application')]
    sub = app_deps[app_deps['source'].isin(wave_apps) & app_deps['target'].isin(wave_apps) & (app_deps['source'].str.endswith(f'-{env}'))]

    G = nx.DiGraph()
    G.add_nodes_from(df_wave['app_instance_id'].tolist())
    for _, r in sub.iterrows():
        G.add_edge(r['source'], r['target'], weight=float(r['weight']), dep_type=r['dependency_type'])

    # plotly network
    pos = nx.spring_layout(G, seed=42)
    edge_x = []
    edge_y = []
    edge_weights = []
    edge_colors = []
    for u,v,data in G.edges(data=True):
        x0,y0 = pos[u]
        x1,y1 = pos[v]
        edge_x += [x0, x1, None]
        edge_y += [y0, y1, None]
        edge_weights.append(data.get('weight',1.0))
        edge_colors.append('red' if data.get('weight',0) > edge_thresh else 'rgba(0,0,0,0.2)')

    node_x = []
    node_y = []
    node_text = []
    colors = {'Mission Critical':'#d62728','Business Critical':'#ff7f0e','Business Operational':'#1f77b4','Non-Critical':'#2ca02c'}
    node_colors = []
    node_sizes = []
    for n in G.nodes():
        x,y = pos[n]
        node_x.append(x)
        node_y.append(y)
        r = apps[apps['app_instance_id']==n].iloc[0]
        node_text.append(f"{n}<br>BCP={r['BCP_score']}<br>RTO={r['RTO_hours']}h")
        node_colors.append(colors.get(r['BCP_tier'],'#7f7f7f'))
        node_sizes.append(10 + r['BCP_score']*5)

    edge_trace = go.Scatter(x=edge_x, y=edge_y, line=dict(width=1, color='rgba(0,0,0,0.2)'), hoverinfo='none', mode='lines')
    node_trace = go.Scatter(x=node_x, y=node_y, mode='markers+text', textposition='top center', hoverinfo='text', text=[n for n in G.nodes()], marker=dict(color=node_colors, size=node_sizes, line_width=1), textfont=dict(size=9), hovertext=node_text)

    fig = go.Figure(data=[edge_trace, node_trace], layout=go.Layout(showlegend=False, margin=dict(t=20,l=20,b=20,r=20)))
    st.plotly_chart(fig, use_container_width=True)

    st.subheader('Dependency table (wave)')
    st.dataframe(sub[['source','target','dependency_type','data_flow_score','weight']])

    # Quick impact summary
    st.subheader('Wave impact summary')
    tot_fin = df_wave['financial_impact_k_per_hour'].sum()
    avg_bcp = df_wave['BCP_score'].mean()
    st.metric('Total financial impact (k$/hr)', f"{tot_fin:.2f}", delta=None)
    st.metric('Average BCP', f"{avg_bcp:.2f}")

    st.markdown('---')
    st.write('Use the controls to navigate algorithms, environments and waves. Export wave CSV to include in migration runbooks.')
