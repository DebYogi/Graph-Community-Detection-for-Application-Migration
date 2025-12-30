# Data Center Application Migration Planning — Graph communit detection & Wave Planner ✅

## Project objective & why this matters
This project creates a synthetic, business-continuity–prioritized dataset and a suite of reproducible tools to plan and validate data center migration waves. It is necessary because large-scale migrations can cause significant business and regulatory risk; this tooling helps prioritize mission-critical services, enforce environment isolation (non-production before production), verify RTO/RPO in non-prod prior to production cutovers, and provide clear validation outputs for stakeholder sign-off.

## 📌 Objective
Create a synthetic, business-continuity–prioritized dataset and tooling to plan migration waves for applications (production and non-production). The deliverables include dataset CSVs, a weighted dependency graph (edges weighted by BCP), community detection results (Louvain & Leiden), an 8-wave migration plan per environment with validation checks, visualizations, and an interactive Streamlit dashboard.

---

## 🔍 Methodology & What Each Step Does
1. **Data generation (`scripts/generate_synthetic_dataset.py`)** 🔧
   - Produces `data/apps.csv`, `data/servers.csv`, `data/databases.csv`, `data/dependencies.csv`.
   - 300 applications (120 front-end, 180 back-end), each with a **prod** and **nonprod** instance (isolated envs), 400 servers and 150 DBs split across envs.
   - Each app instance has attributes: **RTO_hours**, **RPO_minutes**, **financial_impact_k_per_hour**, **regulatory**, **customer_impact**, plus a computed **BCP_score (1–10)** and **BCP_rationale** based on a weighted mix of the inputs.
   - Rationale recorded in `apps.csv` for traceability.

2. **Graph construction & edge weighting** 🕸️
   - Nodes: applications, servers, databases (environment-isolated).
   - Edge weight formula (applied when creating `data/dependencies.csv`):
     **Edge Weight = (BCP_A × 0.6) + (Dependency_Type_Weight × 0.3) + (Data_Flow_Volume_Score × 0.1)**
     - Dependency_Type_Weight: synchronous=5, near-real-time=4, asynchronous=3, batch=2, informational=1
   - This ensures dependencies of high-BCP apps get higher weights (business continuity prioritized).

3. **Community detection (`scripts/run_community_detection.py`)** 🧭

   **What the script does (step-by-step):**
   1. **Load CSVs**: reads `data/apps.csv`, `data/dependencies.csv`, `data/servers.csv`, and `data/databases.csv`.
   2. **Build directed graph (G)**: constructs a NetworkX DiGraph with nodes for apps/servers/dbs and node attributes (type, env, BCP_score, BCP_tier).
   3. **Add edges**: inserts directed edges with attributes `weight`, `dependency_type`, and `data_flow_score`.
   4. **Project to undirected weighted graph (Gu)**: create an undirected graph by aggregating parallel edges and summing weights (Louvain/Leiden are run on this weighted undirected graph).
   5. **Run Louvain**: call `community_louvain.best_partition` on `Gu` (uses edge weights) to obtain a `node -> community` mapping and compute modularity.
   6. **Prepare igraph for Leiden**: map NetworkX nodes to integer indices, build an `igraph.Graph` with the same undirected weighted edges.
   7. **Run Leiden**: call `leidenalg.find_partition` on the igraph graph (with `weights='weight'`) to obtain a membership list and map it back to node labels.
   8. **Format & save**: convert partitions to community lists and save `outputs/communities_louvain.json` and `outputs/communities_leiden.json`.
   9. **Compute & save metrics**: compute modularity for each partition, save `outputs/community_metrics.csv`, and pickle the undirected graph (`outputs/graph_undirected.gpickle`) for visualization.

   **Notes:** the script uses weighted modularity as the quality measure; runtime for each algorithm is captured and saved in the metrics CSV.

   **How Louvain works (step-by-step):**
   1. **Initialization**: put each node in its own community.
   2. **Local moving**: iterate through nodes and, for each node, consider moving it to a neighbor's community if that increases modularity (greedy, local improvement).
   3. **Repeat until convergence**: keep applying local moves until no move increases modularity.
   4. **Aggregation (coarsening)**: collapse current communities into super-nodes, summing edge weights between them, producing a smaller graph.
   5. **Iterate**: run local moving on the aggregated graph and repeat the aggregation until modularity no longer improves.
   6. **Output**: final mapping of original nodes to communities. Louvain is fast and practical but can get trapped in local optima and may produce disconnected communities.

   **How Leiden works (step-by-step):**
   1. **Initialization**: start with each node in its own community (or from a previous partition).
   2. **Local moving (similar to Louvain)**: nodes are moved to neighboring communities to improve a quality function (e.g., modularity).
   3. **Refinement**: refine communities by splitting poorly connected subgroups inside communities to ensure communities are internally well-connected (this step addresses a known Louvain shortcoming).
   4. **Aggregation**: collapse refined communities into super-nodes and rebuild the graph (with summed weights between communities).
   5. **Repeat until stable**: iterate the move-refine-aggregate cycle until no further improvement is found.
   6. **Output**: a partition with better-connected communities and usually improved robustness and modularity compared to Louvain. Leiden is generally preferred when production-quality, well-connected partitions are needed.

   - **Practical tip:** compare modularity and community counts from both algorithms; **Leiden** often yields slightly higher modularity and more well-connected communities, but both are useful for wave candidate generation.

   - **Files produced:** `outputs/communities_louvain.json`, `outputs/communities_leiden.json`, `outputs/community_metrics.csv` (modularity & counts), and `outputs/graph_undirected.gpickle`.

4. **Wave planning & constraint enforcement (`scripts/plan_waves.py`)** 📅
   - Produces **exactly 8 waves per environment** (configurable) while preserving cluster cohesion where possible.
   - Enforced constraints:
     - Waves exclusive to `prod` or `nonprod`.
     - Non-prod waves precede the corresponding prod wave by **>= 1 wave**.
     - BCP tiers: Mission Critical (9–10), Business Critical (7–8), Business Operational (5–6), Non-Critical (1–4).
     - Mission-critical apps placed away from first/last waves (prefer middle waves).
     - Zero cross-wave dependencies for apps with **BCP ≥ 8**.
     - For **BCP ≥ 7** and dependency weight > 7, ensure dependent apps migrate in same or immediately previous wave.
   - Outputs: `outputs/waves_louvain.json`, `outputs/waves_leiden.json`, `outputs/waves.csv` and detailed validation files (see Outputs below).

5. **Visualizations & reports (`scripts/visualize_and_reports.py`)** 📊
   - Creates graph visualizations (nodes colored by BCP tier), dependency heatmaps (top nodes), per-wave business impact CSVs, and a validation checklist CSV.
   - Saved artifacts: `outputs/graph_bcp_colored.png`, `outputs/dependency_heatmap_top60.png`, `outputs/business_impact_waves_*.csv`, `outputs/validation_checklist.csv`.

6. **Interactive dashboard (`dashboard.py`)** 🧑‍💻
   - Streamlit app to explore waves and per-wave dependencies, filter by BCP range, and export wave CSVs for runbooks.
   - Run: `streamlit run dashboard.py` (from project directory).

7. **Reproducible notebook (`migration_planning_analysis.ipynb`)** 📓
   - Step-by-step notebook to run installs, execute pipeline, run sweeps, compute metrics (Modularity, NMI, ARI, silhouette), and view visuals.

---

## ⚙️ How to run (step-by-step)
Prerequisites: Python 3.8+ recommended and an environment with the packages listed in `requirements.txt`.

1. Clone/ensure repo available and change to project directory:
   ```bash
   cd d:/Projects/Graph_Clustering/migration_planning
   ```

2. Install dependencies (recommended in a venv or conda env):
   ```bash
   pip install -r requirements.txt
   ```
   Or use the first cell in `migration_planning_analysis.ipynb` to install packages one-by-one (logs each install).

3. Generate the synthetic dataset:
   ```bash
   python scripts/generate_synthetic_dataset.py
   ```

4. Run community detection:
   ```bash
   python scripts/run_community_detection.py
   ```

5. Plan waves (this enforces the 8-wave requirement and saves validation outputs):
   ```bash
   python scripts/plan_waves.py
   ```

6. Generate visualizations and reports:
   ```bash
   python scripts/visualize_and_reports.py
   ```

7. Run the Streamlit dashboard (interactive exploration):
   ```bash
   streamlit run dashboard.py
   ```

8. (Optional) Open `migration_planning_analysis.ipynb` in VS Code/Jupyter and run cells to reproduce step-by-step, see interactive charts, and run parameter sweeps.

---

## 🧭 Configuration (where to change behavior)
- `scripts/plan_waves.py`:
  - `TARGET_WAVES_PER_ENV` — set number of waves per environment (default 8).
  - `MIN_WAVE`, `MAX_WAVE` — preferred per-wave app count range used in validation checks.
- `scripts/generate_synthetic_dataset.py`:
  - Seeds and distribution parameters at top (random seed) to change reproducibility or distributions.
- Graph & clustering parameters (in `run_community_detection.py` and notebook): resolution/seeds for Louvain/Leiden and parameter sweep ranges.

Change values and re-run pipeline scripts as needed.

---

## 📁 Output files & how to interpret them
All outputs are in `d:/Projects/Graph_Clustering/migration_planning/outputs/` unless noted.

- `data/apps.csv` — application instances (with `env`), **BCP_score**, **BCP_tier**, and `BCP_rationale`. Use this to prioritize and audit BCP scoring.
- `data/dependencies.csv` — dependency list with `source`, `target`, `dependency_type`, `data_flow_score`, and computed `weight` (formula applied here).
- `outputs/communities_*.json` — community assignments (node lists per community) for each algorithm.
- `outputs/community_metrics.csv` — **modularity** and number of communities for Louvain and Leiden (higher modularity usually better cohesion).
- `outputs/waves_louvain.json`, `outputs/waves_leiden.json` — final wave compositions per env.
- `outputs/waves.csv` — flattened waves for both algorithms (good for import into spreadsheets/runbooks).
- `outputs/wave_validation_summary.csv` — summary numbers and count of validation issues for each algorithm.
- `outputs/validation_issues_*.csv` — detailed list of validation problems (types include `wave_size_out_of_bounds`, `cross_wave_high_bcp`, `critical_not_co_migrate`, `nonprod_not_before_prod`, etc.). Use this to triage and remediate.
- `outputs/validation_stats_*.csv` — per-wave statistics (num apps), useful for quick overviews.
- `outputs/business_impact_waves_*.csv` — estimated financial impact per wave (k$/hr) and average BCP; helps to plan windows.
- `outputs/graph_bcp_colored.png` and `outputs/dependency_heatmap_top60.png` — visuals for stakeholder presentations.

How to infer key results:
- **High modularity** (0.4+) suggests strong community structure — communities are good candidates for a wave.
- **validation_issues**: primary source of automatic rebalancing failures. `wave_size_out_of_bounds` often occurs when a fixed number of waves conflicts with the original preferred size range.
- **cross_wave_high_bcp** and **critical_not_co_migrate** are **high priority** findings — these must be corrected (co-locate critical deps) before migration.

---

## ✅ Success criteria (what this pipeline verifies)
- Zero cross-wave dependencies for applications with **BCP ≥ 8** (no violation entries of that type).
- All applications with **BCP ≥ 7** should have their critical dependencies (weight > 7) in the same wave or immediately preceding wave.
- **Non-production validation of recovery procedures** takes place before the corresponding production migration (non-prod wave index < prod wave index).
- Business continuity capabilities maintained throughout migration schedule.

If any checks fail, see `outputs/validation_issues_*.csv` for remediation items.

---

## 🎯 Recommendations & Next steps
- **Remediation options**: Auto-rebalance waves (reduce variance across waves), relax `MIN_WAVE/MAX_WAVE`, or manually adjust waves for business-critical sets.
- **Test**: Run non-prod DR validation for early waves containing high BCP apps.
- **Stakeholder review**: Share `waves.csv`, `business_impact_waves_*.csv`, and `validation_issues_*.csv` for operational sign-off.
- **Hardening**: Add unit tests for constraint checks and automated runbook generation per wave that includes prechecks, rollback steps, and post-validation items.


---


*Created by Debabrata Pati.*
