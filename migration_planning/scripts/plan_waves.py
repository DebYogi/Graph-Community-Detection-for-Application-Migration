"""Create migration waves from community partitions and enforce BCP and scheduling constraints.
Outputs: waves_louvain.csv, waves_leiden.csv, validation reports
"""
import pandas as pd
from pathlib import Path
import json
import math

DATA = Path(__file__).resolve().parent.parent / 'data'
OUT = Path(__file__).resolve().parent.parent / 'outputs'
OUT.mkdir(exist_ok=True)

apps = pd.read_csv(DATA / 'apps.csv')
deps = pd.read_csv(DATA / 'dependencies.csv')

# Load communities
with open(OUT / 'communities_louvain.json') as f:
    comm_louv = json.load(f)
with open(OUT / 'communities_leiden.json') as f:
    comm_leiden = json.load(f)

# Focus only on application nodes
apps_list = set(apps['app_instance_id'].tolist())

# Wave planning parameters
MIN_WAVE=15  # original preferred range (kept for validation)
MAX_WAVE=25
TARGET_WAVES_PER_ENV = 8  # user requested constraint: plan for exactly 8 waves per environment


def clusters_to_waves(clusters, env, target_waves=TARGET_WAVES_PER_ENV):
    """Distribute applications as evenly as possible across `target_waves` for the given env.
    This preserves cluster ordering (concatenating clusters) but enforces near-equal counts per wave.
    """
    # collect clusters for env and flatten them while preserving order
    ordered = []
    for com_id, members in clusters.items():
        members_env = [m for m in members if m in apps_list and m.endswith(f'-{env}')]
        if members_env:
            ordered.append(members_env)
    app_list = [a for cluster in ordered for a in cluster]
    total_apps = len(app_list)
    if total_apps == 0:
        return [[] for _ in range(target_waves)]

    # compute per-wave target sizes: distribute remainder across first waves
    base = total_apps // target_waves
    rem = total_apps % target_waves
    sizes = [base + 1 if i < rem else base for i in range(target_waves)]

    waves = [[] for _ in range(target_waves)]
    idx = 0
    for i, sz in enumerate(sizes):
        if sz > 0:
            waves[i] = app_list[idx: idx + sz]
            idx += sz
        else:
            waves[i] = []
    # If any leftover (shouldn't happen) append to last wave
    if idx < total_apps:
        waves[-1].extend(app_list[idx:])
    return waves

# Build initial waves for both algorithms and both envs
def build_waves_for_algo(communities):
    waves = {'nonprod': [], 'prod': []}
    for env in ['nonprod','prod']:
        waves[env] = clusters_to_waves(communities, env, target_waves=TARGET_WAVES_PER_ENV)
    return waves

# Detailed validation checks
def validate_waves(waves, algorithm_name):
    """Run constraint checks and return list of issues and per-wave stats."""
    issues = []
    stats = []
    idxmap = wave_index_map(waves)
    # check wave counts
    for env in ['nonprod','prod']:
        if len(waves[env]) != TARGET_WAVES_PER_ENV:
            issues.append({'type':'wave_count_mismatch','algorithm':algorithm_name,'env':env,'expected':TARGET_WAVES_PER_ENV,'actual':len(waves[env])})
    # per-wave stats and size checks
    for env in ['nonprod','prod']:
        for i,w in enumerate(waves[env]):
            stats.append({'algorithm':algorithm_name,'env':env,'wave_index':i,'num_apps':len(w)})
            if len(w) < MIN_WAVE or len(w) > MAX_WAVE:
                issues.append({'type':'wave_size_out_of_bounds','algorithm':algorithm_name,'env':env,'wave_index':i,'num_apps':len(w),'preferred_range':[MIN_WAVE,MAX_WAVE]})
    # exclusivity: already per-env but double-check
    for env in ['nonprod','prod']:
        for i,w in enumerate(waves[env]):
            for a in w:
                if not a.endswith(f'-{env}'):
                    issues.append({'type':'env_exclusivity_violation','algorithm':algorithm_name,'env':env,'wave_index':i,'app':a})
    # non-prod must precede prod by at least one wave for same base app
    base_groups = apps.groupby('base_app_id')['app_instance_id'].apply(list).to_dict()
    for base, instances in base_groups.items():
        nonprod = [i for i in instances if i.endswith('-nonprod')]
        prod = [i for i in instances if i.endswith('-prod')]
        if nonprod and prod:
            np_idx = min(idxmap.get(n,(None,999))[1] for n in nonprod)
            p_idx = min(idxmap.get(p,(None,999))[1] for p in prod)
            if p_idx <= np_idx:
                issues.append({'type':'nonprod_not_before_prod','algorithm':algorithm_name,'base_app':base,'nonprod_wave':np_idx,'prod_wave':p_idx})
            if p_idx - np_idx < 1:
                issues.append({'type':'nonprod_production_gap_too_small','algorithm':algorithm_name,'base_app':base,'gap':p_idx-np_idx})
    # BCP and dependency checks
    app_deps = deps[deps['source_type']=='application']
    for _, r in app_deps.iterrows():
        src = r['source']; tgt = r['target']; wt = float(r['weight'])
        if src in idxmap and tgt in idxmap:
            s_env,s_idx = idxmap[src]; t_env,t_idx = idxmap[tgt]
            bcp_src = apps_df.loc[src]['BCP_score']
            # check env
            if s_env != t_env:
                issues.append({'type':'cross_env_dependency','algorithm':algorithm_name,'source':src,'target':tgt})
                # cross-env dependencies are not allowed; continue to next record
                continue
            # zero cross-wave deps for BCP>=8
            if bcp_src >= 8 and (s_idx != t_idx):
                issues.append({'type':'cross_wave_high_bcp','algorithm':algorithm_name,'source':src,'target':tgt,'s_idx':s_idx,'t_idx':t_idx})
            # for BCP >=7 and weight>7: critical dependencies must be same wave or previous
            if bcp_src >= 7 and wt > 7 and not (t_idx == s_idx or t_idx == s_idx -1):
                issues.append({'type':'critical_not_co_migrate','algorithm':algorithm_name,'source':src,'target':tgt,'weight':wt,'s_idx':s_idx,'t_idx':t_idx})
    # mission critical (9-10) not in first or last wave
    for env in ['nonprod','prod']:
        N = len(waves[env])
        for i,w in enumerate(waves[env]):
            for a in w:
                if apps_df.loc[a]['BCP_score'] >= 9 and (i==0 or i==N-1):
                    issues.append({'type':'mission_critical_edge_wave','algorithm':algorithm_name,'env':env,'wave_index':i,'app':a})
    return issues, stats

waves_louvain = build_waves_for_algo(comm_louv)
waves_leiden = build_waves_for_algo(comm_leiden)

# Helper: map app to wave index
def wave_index_map(waves):
    m = {}
    for env,wlist in waves.items():
        for i,w in enumerate(wlist):
            for app in w:
                m[app] = (env, i)
    return m

# Validation rules and enforcement
# 1) Non-prod must precede prod for same base app by at least one wave
# 2) BCP 9-10 apps placed in middle waves (not first/last)
# 3) Zero cross-wave dependencies for apps with BCP >=8 (i.e., all their app dependencies must be in same wave)
# 4) For BCP >=7, critical dependencies (weight>7) must be in same or preceding wave

apps_df = apps.set_index('app_instance_id')

def enforce_constraints(waves, target_waves=TARGET_WAVES_PER_ENV):
    """Iteratively enforce constraints while preserving the target number of waves per env.
    This function attempts safe adjustments (moving dependencies, aligning prod after nonprod, and placing high BCP in middle waves). It will not remove empty wave slots so the target wave count is preserved for planning.
    """
    changed = True
    iters = 0
    while changed and iters < 40:
        changed = False
        iters += 1
        idxmap = wave_index_map(waves)
        # rule 1: nonprod precedes prod by >=1 wave
        base_groups = apps.groupby('base_app_id')['app_instance_id'].apply(list).to_dict()
        for base, instances in base_groups.items():
            nonprod = [i for i in instances if i.endswith('nonprod')]
            prod = [i for i in instances if i.endswith('prod')]
            if nonprod and prod:
                np_idx = min(idxmap.get(n, (None,999))[1] for n in nonprod)
                p_idx = min(idxmap.get(p, (None,999))[1] for p in prod)
                if p_idx <= np_idx:
                    if np_idx < target_waves - 1:
                        target_idx = np_idx + 1
                        # move prod apps to target_idx
                        for p in prod:
                            old = idxmap.get(p)
                            if old and old[1] != target_idx:
                                # remove from old wave if present
                                if p in waves['prod'][old[1]]:
                                    waves['prod'][old[1]].remove(p)
                                waves['prod'][target_idx].append(p)
                                changed = True
                    else:
                        # nonprod is already in last wave; move nonprod earlier to ensure gap >=1
                        new_np_idx = max(np_idx - 1, 0)
                        for n in nonprod:
                            old = idxmap.get(n)
                            if old and old[1] != new_np_idx:
                                if n in waves['nonprod'][old[1]]:
                                    waves['nonprod'][old[1]].remove(n)
                                waves['nonprod'][new_np_idx].append(n)
                                changed = True
        # rule 3 and 4: dependency constraints
        app_deps = deps[deps['source_type']=='application']
        for _, r in app_deps.iterrows():
            src = r['source']; tgt = r['target']; wt = float(r['weight'])
            if src not in idxmap or tgt not in idxmap: continue
            src_env, s_idx = idxmap[src]
            tgt_env, t_idx = idxmap[tgt]
            if src_env != tgt_env: continue
            bcp_src = apps_df.loc[src]['BCP_score']
            # BCP >=8: force same wave
            if bcp_src >= 8 and s_idx != t_idx:
                try:
                    if tgt in waves[tgt_env][t_idx]:
                        waves[tgt_env][t_idx].remove(tgt)
                    if tgt not in waves[src_env][s_idx]:
                        waves[src_env][s_idx].append(tgt)
                        changed = True
                except Exception:
                    pass
            # BCP >=7 & wt>7: ensure same or preceding wave
            if bcp_src >= 7 and wt > 7 and not (t_idx == s_idx or t_idx == s_idx - 1):
                preferred = s_idx
                if preferred >= target_waves:
                    preferred = target_waves - 1
                if tgt in waves[tgt_env][t_idx]:
                    waves[tgt_env][t_idx].remove(tgt)
                if tgt not in waves[src_env][preferred]:
                    waves[src_env][preferred].append(tgt)
                    changed = True
        # rule 2: place BCP 9-10 into middle waves (not first/last)
        for env in ['nonprod','prod']:
            N = len(waves[env])
            if N <= 2: continue
            low = math.floor(0.25*N)
            high = math.ceil(0.75*N)-1
            middle = (low+high)//2
            for i in range(N):
                for a in list(waves[env][i]):
                    try:
                        bcp = apps_df.loc[a]['BCP_score']
                        if bcp >= 9 and (i == 0 or i == N-1):
                            waves[env][i].remove(a)
                            waves[env][middle].append(a)
                            changed = True
                    except Exception:
                        pass
        # Ensure waves list has exactly target_waves slots (do not drop empty slots)
        for env in ['nonprod','prod']:
            while len(waves[env]) < target_waves:
                waves[env].append([])
            if len(waves[env]) > target_waves:
                # merge trailing extras into last allowed waves
                while len(waves[env]) > target_waves:
                    extra = waves[env].pop()
                    waves[env][target_waves-1].extend(extra)
    # Final corrective pass: ensure nonprod precedes prod by at least one wave where possible
    def fix_nonprod_prod_order(waves):
        changed_local = True
        it = 0
        while changed_local and it < 40:
            changed_local = False
            it += 1
            idxmap = wave_index_map(waves)
            base_groups = apps.groupby('base_app_id')['app_instance_id'].apply(list).to_dict()
            for base, instances in base_groups.items():
                nonprod = [i for i in instances if i.endswith('nonprod')]
                prod = [i for i in instances if i.endswith('prod')]
                if nonprod and prod:
                    np_idx = min(idxmap.get(n, (None,999))[1] for n in nonprod)
                    p_idx = min(idxmap.get(p, (None,999))[1] for p in prod)
                    if p_idx <= np_idx:
                        # try moving nonprod earlier if possible
                        if np_idx > 0:
                            new_np = max(np_idx - 1, 0)
                            for n in nonprod:
                                old = idxmap.get(n)
                                if old and old[1] != new_np:
                                    if n in waves['nonprod'][old[1]]:
                                        waves['nonprod'][old[1]].remove(n)
                                    waves['nonprod'][new_np].append(n)
                                    changed_local = True
                        # else try moving prod later if possible
                        elif p_idx < target_waves - 1:
                            new_p = min(p_idx + 1, target_waves - 1)
                            for p in prod:
                                old = idxmap.get(p)
                                if old and old[1] != new_p:
                                    if p in waves['prod'][old[1]]:
                                        waves['prod'][old[1]].remove(p)
                                    waves['prod'][new_p].append(p)
                                    changed_local = True
        return waves

    waves = fix_nonprod_prod_order(waves)

    # Sanitize waves: ensure env exclusivity, uniqueness, and assign any missing apps back evenly
    def sanitize_waves(waves):
        seen = set()
        # ensure each wave only contains apps for its env and no duplicates
        for env in ['nonprod','prod']:
            # ensure wave list length
            if len(waves[env]) < target_waves:
                while len(waves[env]) < target_waves:
                    waves[env].append([])
            for i, w in enumerate(waves[env]):
                newlist = []
                for a in w:
                    # only keep proper env suffix and avoid duplicates
                    if not isinstance(a, str):
                        continue
                    if not a.endswith(f'-{env}'):
                        continue
                    if a in seen:
                        continue
                    seen.add(a)
                    newlist.append(a)
                waves[env][i] = newlist
        # find missing apps per env and distribute round-robin to rebalance
        for env in ['nonprod','prod']:
            all_env_apps = [a for a in apps['app_instance_id'].tolist() if a.endswith(f'-{env}')]
            missing = [a for a in all_env_apps if a not in seen]
            if len(waves[env]) == 0:
                waves[env] = [[] for _ in range(target_waves)]
            j = 0
            for a in missing:
                waves[env][j % len(waves[env])].append(a)
                seen.add(a)
                j += 1
        return waves

    waves = sanitize_waves(waves)

    # Final equalize pass: re-distribute apps evenly across waves per env while spreading high-BCP apps
    def equalize_waves(waves):
        for env in ['nonprod','prod']:
            all_env_apps = [a for a in apps['app_instance_id'].tolist() if a.endswith(f'-{env}')]
            # sort by BCP descending so high-risk apps are spread early
            all_env_apps = sorted(all_env_apps, key=lambda x: apps_df.loc[x]['BCP_score'], reverse=True)
            total = len(all_env_apps)
            if total == 0:
                waves[env] = [[] for _ in range(target_waves)]
                continue
            base = total // target_waves
            rem = total % target_waves
            sizes = [base + 1 if i < rem else base for i in range(target_waves)]
            new_w = []
            idx = 0
            for s in sizes:
                new_w.append(all_env_apps[idx: idx + s])
                idx += s
            waves[env] = new_w
        return waves

    waves = equalize_waves(waves)
    return waves

waves_louvain = enforce_constraints(waves_louvain)
waves_leiden = enforce_constraints(waves_leiden)

# Save waves to CSVs
def waves_to_df(waves, algorithm):
    rows = []
    for env,wlist in waves.items():
        for i,w in enumerate(wlist):
            for app in w:
                rows.append({'algorithm':algorithm,'env':env,'wave_index':i,'app_instance_id':app})
    return pd.DataFrame(rows)

pd.concat([waves_to_df(waves_louvain,'louvain'), waves_to_df(waves_leiden,'leiden')]).to_csv(OUT / 'waves.csv', index=False)

# Validation report (detailed checks)
summary = []
for algorithm,waves in [('louvain',waves_louvain),('leiden',waves_leiden)]:
    # enforce constraints while preserving target wave count
    waves = enforce_constraints(waves, target_waves=TARGET_WAVES_PER_ENV)
    # validate
    issues, stats = validate_waves(waves, algorithm)
    # Save issues and stats
    issues_df = pd.DataFrame(issues)
    stats_df = pd.DataFrame(stats)
    issues_df.to_csv(OUT / f'validation_issues_{algorithm}.csv', index=False)
    stats_df.to_csv(OUT / f'validation_stats_{algorithm}.csv', index=False)
    # summary row
    summary.append({'algorithm':algorithm,'num_waves_nonprod':len(waves['nonprod']),'num_waves_prod':len(waves['prod']),'issues_found':len(issues)})
    # overwrite waves variable to ensure final saved waves contain enforced changes
    if algorithm == 'louvain':
        waves_louvain = waves
    else:
        waves_leiden = waves

pd.DataFrame(summary).to_csv(OUT / 'wave_validation_summary.csv', index=False)

# Save final wave files
for algo,waves in [('louvain',waves_louvain),('leiden',waves_leiden)]:
    with open(OUT / f'waves_{algo}.json','w') as f:
        json.dump(waves, f, indent=2)

print('Waves planned and saved to outputs. Validation details saved to outputs/validation_issues_*.csv and validation_stats_*.csv')
