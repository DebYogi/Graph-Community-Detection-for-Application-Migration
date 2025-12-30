"""Generate synthetic dataset for data center migration planning.
Outputs CSVs: apps.csv, servers.csv, databases.csv, dependencies.csv
"""
import random
import math
from pathlib import Path
import pandas as pd
import numpy as np

OUT = Path(__file__).resolve().parent.parent / "data"
OUT.mkdir(exist_ok=True)

random.seed(42)
np.random.seed(42)

# Config
NUM_APPS = 300  # base applications
FRONTEND = 120
BACKEND = 180
NUM_SERVERS_PROD = 200
NUM_SERVERS_NONPROD = 200
NUM_DBS_PROD = 75
NUM_DBS_NONPROD = 75

# BCP tiers
BCP_TIERS = {
    'Mission Critical': (9,10),
    'Business Critical': (7,8),
    'Business Operational': (5,6),
    'Non-Critical': (1,4)
}

# Helper to map score to tier
def score_to_tier(score):
    s = int(round(score))
    if s >= 9:
        return 'Mission Critical'
    if s >= 7:
        return 'Business Critical'
    if s >= 5:
        return 'Business Operational'
    return 'Non-Critical'

# 1) Create base apps
apps = []
for i in range(1, NUM_APPS+1):
    app_id = f"APP_{i:03d}"
    app_type = 'frontend' if i <= FRONTEND else 'backend'
    apps.append({'base_app_id': app_id, 'app_type': app_type})

# 2) Create prod and non-prod application instances
app_instances = []
for a in apps:
    for env in ('prod','nonprod'):
        instance_id = f"{a['base_app_id']}-{env}"
        # Assign risk attributes
        # RTO in hours: mission critical tends to be <1-4, others higher
        if random.random() < 0.1:
            rto = round(random.uniform(0.25, 2.0), 2)
        else:
            rto = round(random.uniform(2.0, 72.0), 2)
        # RPO in minutes
        if rto <= 2:
            rpo = int(random.uniform(0,30))
        else:
            rpo = int(random.uniform(30, 1440))
        financial_impact = round(10**random.uniform(2,6) / 1000.0, 2)  # thousands per hour scaled
        regulatory = random.random() < 0.15  # 15% regulated
        customer_impact = int(np.clip(np.random.normal(6,2),1,10))
        # Quick BCP score heuristic combining normalized values
        # We want BCP in 1-10 scale
        # rto_score: shorter rto -> higher score
        rto_score = np.clip(11 - math.log1p(rto), 1, 10)
        # rpo_score: shorter rpo -> higher
        rpo_score = np.clip(11 - math.log1p(rpo/60.0), 1, 10)
        fin_score = np.clip(1 + math.log10(financial_impact+1)*2, 1, 10)
        reg_score = 9 if regulatory else 0
        cust_score = customer_impact
        # Weighted mix
        raw = (rto_score*0.25 + rpo_score*0.20 + fin_score*0.25 + (reg_score/10.0)*0.15 + cust_score*0.15)
        bcp = float(np.clip(round(raw,2),1,10))
        tier = score_to_tier(bcp)
        rationale = (
            f"RTO={rto}h, RPO={rpo}m, $impact={financial_impact}k/hr, "
            f"regulatory={regulatory}, customer_impact={customer_impact} -> BCP {bcp} ({tier})"
        )
        app_instances.append({
            'app_instance_id': instance_id,
            'base_app_id': a['base_app_id'],
            'env': env,
            'app_type': a['app_type'],
            'RTO_hours': rto,
            'RPO_minutes': rpo,
            'financial_impact_k_per_hour': financial_impact,
            'regulatory': regulatory,
            'customer_impact': customer_impact,
            'BCP_score': bcp,
            'BCP_tier': tier,
            'BCP_rationale': rationale
        })

apps_df = pd.DataFrame(app_instances)
apps_df.to_csv(OUT / 'apps.csv', index=False)
print('Wrote', OUT / 'apps.csv')

# 3) Create servers and databases, isolated by env
servers = []
for env, n in (('prod',NUM_SERVERS_PROD),('nonprod',NUM_SERVERS_NONPROD)):
    for i in range(1, n+1):
        sid = f"SRV-{env[:1].upper()}{i:03d}"
        capacity = int(random.uniform(8,256))  # vCPU or capacity metric
        location = random.choice(['DC1','DC2','DC3'])
        servers.append({'server_id': sid, 'env': env, 'capacity': capacity, 'location': location})

servers_df = pd.DataFrame(servers)
servers_df.to_csv(OUT / 'servers.csv', index=False)
print('Wrote', OUT / 'servers.csv')

# Databases
dbs = []
for env, n in (('prod',NUM_DBS_PROD),('nonprod',NUM_DBS_NONPROD)):
    for i in range(1, n+1):
        did = f"DB-{env[:1].upper()}{i:03d}"
        db_type = random.choice(['postgres','mysql','mssql','mongo','oracle'])
        size_gb = round(random.expovariate(1/50.0) + 1,2)
        dbs.append({'db_id': did, 'env': env, 'db_type': db_type, 'size_gb': size_gb})

dbs_df = pd.DataFrame(dbs)
dbs_df.to_csv(OUT / 'databases.csv', index=False)
print('Wrote', OUT / 'databases.csv')

# 4) Create dependencies: edges between apps, servers, dbs
# We'll create for each app instance a set of dependencies: servers (1-3), dbs (0-2), other apps (0-4)
dependency_types = ['synchronous','near-real-time','asynchronous','batch','informational']
dep_type_weight = {'synchronous':5,'near-real-time':4,'asynchronous':3,'batch':2,'informational':1}

deps = []

# Helper to sample environment-specific resource
servers_by_env = servers_df.groupby('env')['server_id'].apply(list).to_dict()
dbs_by_env = dbs_df.groupby('env')['db_id'].apply(list).to_dict()

app_instance_ids = apps_df['app_instance_id'].tolist()
base_to_instances = apps_df.groupby('base_app_id')['app_instance_id'].apply(list).to_dict()

for idx, row in apps_df.iterrows():
    src = row['app_instance_id']
    bcp = row['BCP_score']
    env = row['env']
    # servers
    n_servers = random.randint(1,3)
    for _ in range(n_servers):
        target = random.choice(servers_by_env[env])
        dtype = random.choices(dependency_types, weights=[0.2,0.15,0.35,0.2,0.1])[0]
        data_flow = int(np.clip(np.random.normal(5,2),1,10))
        weight = round(bcp * 0.6 + dep_type_weight[dtype] * 0.3 + data_flow * 0.1, 3)
        deps.append({'source': src, 'target': target, 'source_type': 'application', 'target_type': 'server', 'dependency_type': dtype, 'data_flow_score': data_flow, 'weight': weight})
    # databases
    n_dbs = random.randint(0,2)
    for _ in range(n_dbs):
        target = random.choice(dbs_by_env[env])
        dtype = random.choices(dependency_types, weights=[0.15,0.15,0.4,0.2,0.1])[0]
        data_flow = int(np.clip(np.random.normal(4,2),1,10))
        weight = round(bcp * 0.6 + dep_type_weight[dtype] * 0.3 + data_flow * 0.1, 3)
        deps.append({'source': src, 'target': target, 'source_type': 'application', 'target_type': 'database', 'dependency_type': dtype, 'data_flow_score': data_flow, 'weight': weight})
    # app-to-app dependencies (only within same env to respect isolation)
    n_apps = random.randint(0,4)
    candidates = [a for a in app_instance_ids if a != src and a.endswith(f'-{env}')]
    for _ in range(n_apps):
        target = random.choice(candidates)
        dtype = random.choices(dependency_types, weights=[0.15,0.2,0.35,0.2,0.1])[0]
        data_flow = int(np.clip(np.random.normal(6,3),1,10))
        # For target, use BCP of source (dependent) which is src
        weight = round(bcp * 0.6 + dep_type_weight[dtype] * 0.3 + data_flow * 0.1, 3)
        deps.append({'source': src, 'target': target, 'source_type': 'application', 'target_type': 'application', 'dependency_type': dtype, 'data_flow_score': data_flow, 'weight': weight})
    # Fallback waveback edges: to a 'fallback' server within same env (lightweight, lower data flow)
    fallback = random.choice(servers_by_env[env])
    deps.append({'source': src, 'target': fallback, 'source_type': 'application', 'target_type': 'server', 'dependency_type': 'fallback', 'data_flow_score': 1, 'weight': round(bcp * 0.6 + 1*0.3 + 1*0.1,3)})

deps_df = pd.DataFrame(deps)
deps_df.to_csv(OUT / 'dependencies.csv', index=False)
print('Wrote', OUT / 'dependencies.csv')

print('Dataset generation complete.')
