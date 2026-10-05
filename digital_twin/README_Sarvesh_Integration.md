# Sarvesh's Modules: Battery, PSO and Scenarios

Integration guide for the Smart Grid Digital Twin team (Sujit, Jayanth, Sanjay, ESP32).

Owner: **Sarvesh**. Algorithm: **Particle Swarm Optimization (PSO)** for battery scheduling, plus the scenario and contingency generator.

---

## 1. What is in this package

| File | Purpose |
|---|---|
| `sarvesh_lib.py` | Shared library: battery model, PSO, scenario table, contingencies, integration helpers |
| `battery_model.ipynb` | Defines and validates the battery, adds it to the SimBench net, saves `output/battery_config.json` |
| `pso_battery_optimizer.ipynb` | Runs PSO, compares strategies, validates with AC power flow, saves the 96-step schedule |
| `scenario_generator.ipynb` | Builds the scenario table, runs battery OFF vs ON, runs contingency tests |

**Requirements:** `pip install simbench pandapower numba numpy pandas matplotlib`

**Placement:** keep `sarvesh_lib.py` in `digital_twin/` next to the notebooks. All notebooks write to `digital_twin/output/`.

## 2. Run order (Sarvesh's part)

1. `battery_model.ipynb` creates `output/battery_config.json`
2. `pso_battery_optimizer.ipynb` creates the schedule and comparison files
3. `scenario_generator.ipynb` creates the scenario and contingency results

Step 2 needs Sujit's price forecast (see section 3). Without it, a synthetic price is used and a warning is printed.

## 3. Files exchanged between team members

| File | Direction | Contents |
|---|---|---|
| `output/price_forecast_day.csv` | **Sujit -> Sarvesh** | 96 rows, column `price_forecast` (EUR/MWh), one value per 15-min step of the chosen day |
| `output/battery_config.json` | Sarvesh -> everyone | Battery bus, power and energy rating, SOC limits, efficiencies |
| `output/battery_schedule_pso_96.csv` | **Sarvesh -> Sujit, Sanjay** | `step, timestamp, price, battery_power_mw, battery_soc, battery_mode` |
| `output/battery_impact_comparison.csv` | Sarvesh -> Sujit (report) | No battery vs simple rule vs PSO: cost, peak, Vmin, loading, losses |
| `output/scenario_library.csv` | Sarvesh -> everyone | Scenario ids, `profile_step`, load/PV/price multipliers, battery SOC, outage type |
| `output/scenario_battery_results.csv` | Sarvesh -> Sujit | Per-scenario before/after metrics with the battery acting |
| `output/contingency_results.csv` | Sarvesh -> Sujit | Line outage, transformer derate, generator loss and switch-open results |

## 4. Conventions that must be shared

- **Battery sign:** `battery_power_mw > 0` means **charging** (extra load). `< 0` means **discharging** (supports the grid). This matches pandapower `storage.p_mw`.
- **Time:** 15-minute steps, 96 per day. `profile_step = day_of_year(0-based) * 96 + step_in_day`. Annual peak (09-12-2016 18:15) is `profile_step = 33001`.
- **SOC:** `battery_soc` in the CSV is in percent. Inside the library, energy is in MWh.
- **SOC limits:** 10% to 90%. Every function repairs requests so these limits and the power limit are never violated.
- **Dataset column mapping** (for the unified dataset):

| Dataset field | Source |
|---|---|
| `battery_soc` | `battery_soc` column of the schedule, or the running SOC returned by `battery_dispatch` |
| `battery_power_mw` | the power actually applied to the net |
| `battery_command_mw` | Sanjay's fuzzy command |

## 5. For Sujit: digital-twin core and final integration

**Order of operations at each timestep matters.** `apply_profile_step` overwrites `net.load.p_mw` and `net.sgen.p_mw`, so do it first. Add EV loads after it, as new load elements or on top of the freshly written values.

```python
import json, pandas as pd
from sarvesh_lib import *

cfg   = BatteryConfig(**json.load(open("output/battery_config.json")))
sidx  = add_battery(net, cfg)                      # real pandapower storage element
sched = pd.read_csv("output/battery_schedule_pso_96.csv")

E = cfg.e_init                                      # MWh
for t in range(96):
    apply_profile_step(net, profiles, day_start + t)   # 1. SimBench loads and PV
    # 2. add Jayanth's EV load here
    p, E_next = battery_dispatch(sched.battery_power_mw[t], E, cfg)   # 3. PSO set-point
    set_battery_power(net, sidx, p)
    run_pf(net)                                         # 4. BEFORE-control power flow
    before = grid_metrics(net)                          # dict of metrics for the dataset
    # 5. Sanjay's fuzzy controller, then call battery_dispatch again with fuzzy_cmd_mw
    E = E_next
```

Helpers you can reuse: `run_pf(net)`, `grid_metrics(net)` (voltages, loading, losses, import, worst bus/line/trafo, violation counts, unsupplied buses), `run_day(...)` (96 power flows for one day).

**Scenarios and contingencies:**

```python
snap = snapshot_state(net)                 # save the healthy topology once
table = pd.read_csv("output/scenario_library.csv")
for _, row in table.iterrows():
    reset_contingencies(net, snap)         # always restore first
    info = apply_scenario_row(net, profiles, row, sidx, cfg)
    run_pf(net)
```

`apply_scenario_row` applies the profile step, load and PV multipliers, battery SOC and any outage. It scales the loads and PV currently in the net, so add EV loads after calling it.

## 6. For Sanjay: fuzzy controller

Your controller gets the grid state from `grid_metrics(net)` and the SOC from the running battery state. Return the battery command as MW (negative = discharge, positive = charge) and pass it through the dispatch function:

```python
p_final, E_next = battery_dispatch(
    p_scheduled   = sched.battery_power_mw[t],   # PSO day-ahead plan
    E_now         = E,                           # MWh
    cfg           = cfg,
    fuzzy_cmd_mw  = fuzzy_battery_mw)            # None = follow PSO plan
set_battery_power(net, sidx, p_final)
run_pf(net)                                       # AFTER-control power flow
```

If the fuzzy command asks for the impossible (for example a discharge at 10% SOC), `battery_dispatch` clips it, so the digital twin never breaks the battery limits.

## 7. For Jayanth: EV GA

- Your EV loads and the battery are independent injections into the same net. Nothing in `sarvesh_lib.py` touches your EV elements.
- Do **not** put EV demand into profile-driven load rows. `apply_profile_step` overwrites them every step. Create separate load elements for EV charging, or add the EV MW after the profile step.
- If your GA needs the battery state, read `output/battery_schedule_pso_96.csv`.

## 8. For the ESP32 module

The battery fields to include in the compact state vector are `battery_soc` (percent) and, optionally, the applied `battery_power_mw`. The edge override `SUPPORT_GRID` can map to a discharge command, which should still go through `battery_dispatch` on the PC.

## 9. How PSO works (for the report and viva)

- One **particle** is a full day schedule: 96 battery power set-points in MW.
- A hard repair step keeps every particle feasible (power limit, SOC 10% to 90%).
- **Fitness** (lower is better) = cost + peak import + losses + voltage penalty + end-of-day SOC penalty + a small battery-wear term. Cost, peak and losses are normalised to the no-battery case. Weights are in `DEFAULT_WEIGHTS` in `sarvesh_lib.py`.
- Running full AC power flow on 10,458 buses inside the PSO loop would be far too slow. PSO therefore scores particles with a **surrogate** (quadratic loss curve and linear Vmin curve) **fitted to real pandapower runs**.
- The final schedule is then **re-validated with full AC power flow** for all 96 steps. The reported results come from that validation, not from the surrogate.

## 10. Troubleshooting

| Problem | Fix |
|---|---|
| `ModuleNotFoundError: sarvesh_lib` | Run the notebook from `digital_twin/`, or keep `sys.path.insert(0, ".")` in the first cell |
| Power flow does not converge in an outage scenario | Expected for severe outages. Check the `converged` column and `unsupplied_buses` |
| Battery power appears to have no effect | Check sign convention, and check that the storage element is `in_service` |
| Vmin or loading `NaN` | An island formed after an outage. Metrics are NaN-safe, but inspect `unsupplied_buses` |
| Notebook says "synthetic price used" | Sujit's `price_forecast_day.csv` is missing from `output/` |

## 11. Viva line for Sarvesh

"I developed a PSO-based battery-energy-management algorithm and the scenario/contingency generator used to test the digital twin."
