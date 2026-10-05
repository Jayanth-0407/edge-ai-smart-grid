# Sarvesh Module — PSO Battery Energy Management Integration

## 1. Overview

This document records the integration, validation, fixes, observations, and final results for Sarvesh's **PSO-based Battery Energy Management** module in the Smart Grid Digital Twin project.

The final integrated pipeline is:

```text
Hybrid Electricity Price Forecast
        ↓
24 hourly predicted prices
        ↓
96 × 15-minute price signal
        ↓
PSO Battery Optimizer
        ↓
Battery charge/discharge schedule
        ↓
Full SimBench AC power-flow validation
        ↓
Scenario + contingency validation
```

The complete SimBench network used throughout is:

```text
SimBench code: 1-MVLV-urban-all-0-sw

Buses:        10,458
Lines:        10,328
Transformers: 135
Loads:        11,542
Static gens:  806
Switches:     20,933
```

The battery is connected at the selected MV-side location:

```text
Battery bus:       30944
Voltage level:     10 kV
Energy capacity:   20 MWh
Power limit:       ±5 MW
Minimum SOC:       10%
Maximum SOC:       90%
Initial SOC:       50%

Sign convention:
+ battery_power_mw  = charging
- battery_power_mw  = discharging
```

---

## 2. Original Sarvesh Contribution

Sarvesh's module originally contained three major parts:

1. **Battery physical model**
2. **PSO battery optimizer**
3. **Scenario and contingency generator**

The original implementation already contained the core battery equations, SOC constraints, PSO formulation, surrogate model, scenario definitions, and contingency helper functions.

During integration, the core PSO algorithm was preserved. Most of the changes made were for:

- cross-module integration,
- memory efficiency,
- correct SimBench profile handling,
- output compatibility,
- safe power-flow validation,
- reproducibility,
- and failure handling.

---

## 3. Battery Model Validation

Before integrating PSO, the battery model was validated independently.

### Validation tests

| Test | Result |
|---|---:|
| Charge-only case | SOC reached 90% maximum |
| Discharge-only case | SOC reached 10% minimum |
| Idle case | SOC remained unchanged |
| Round-trip efficiency | 0.9025 |
| Charge request at maximum SOC | Allowed power = 0 MW |

The round-trip efficiency matched:

```text
0.95 × 0.95 = 0.9025
```

### Observation

The model correctly prevented the battery from exceeding:

```text
SOC > 90%
SOC < 10%
```

and clipped physically impossible commands.

### Inference

The optimizer cannot obtain an artificially good solution by using unavailable battery energy. All battery schedules are constrained by the actual SOC and power limits.

---

## 4. Integration Change — Real Hybrid Price Forecast

Sarvesh's PSO originally supported a synthetic price fallback.

During early standalone testing, the notebook printed:

```text
WARNING: synthetic price used
```

For final integration, this was replaced by the project's real Hybrid electricity-price forecasting output.

The price forecasting model is:

```text
XGBoost
   +
BiLSTM
   ↓
Linear Regression stacker
   ↓
Hybrid forecast
```

Final Hybrid forecasting metrics:

| Metric | Value |
|---|---:|
| MAE | 1.0618 |
| RMSE | 1.5096 |
| MAPE | 1.56% |
| R² | 0.9636 |

### Hourly forecast to 15-minute signal

The price model outputs 24 hourly forecasts for one selected unseen test window.

The SimBench battery optimizer operates at 15-minute resolution:

```text
24 hours × 4 intervals/hour = 96 intervals
```

Each hourly forecast is therefore repeated four times:

```text
71.777
↓
71.777
71.777
71.777
71.777
```

This produces:

```text
digital_twin/output/price_forecast_day.csv
```

with:

```text
96 rows
column: price_forecast
```

### Why repetition instead of interpolation?

No artificial intermediate market-price values are created. Each hourly forecast is simply held constant over its four corresponding 15-minute simulation intervals.

### Important limitation

The Spanish electricity-price dataset and SimBench are independent datasets.

Therefore the forecast is used as an **external market-price signal** for the selected SimBench day.

It should **not** be described as the actual market price corresponding to the SimBench calendar timestamp.

Correct description:

> A 24-hour unseen Hybrid electricity-price forecast is used as an external market-price signal for the selected SimBench operating day.

---

## 5. Integration Change — Battery Schedule Export Fix

During PSO output export, the battery energy array had a two-dimensional shape similar to:

```text
(1, 97)
```

while `pandas.DataFrame` expected a one-dimensional vector.

This caused:

```text
ValueError: Per-column arrays must each be 1-dimensional
```

The export logic was corrected by squeezing the arrays before building the final table.

The final schedule contains:

```text
96 timestamps
96 forecast prices
96 battery power commands
96 SOC values
96 operating modes
```

Operating modes are:

```text
CHARGE
DISCHARGE
IDLE
```

---

## 6. PSO Objective and Optimization Behaviour

The PSO is a multi-objective optimizer.

Its objective includes terms related to:

- electricity cost,
- peak grid import,
- power losses,
- voltage penalty,
- end-of-day SOC,
- and battery usage/wear.

Therefore the PSO is **not** designed to minimize electricity cost alone.

This distinction becomes important when comparing PSO against the simple rule-based strategy.

---

## 7. Final PSO Convergence

Using the real Hybrid price signal, PSO fitness evolved approximately as:

```text
Iteration 0   → 1.90000
Iteration 25  → 1.77913
Iteration 50  → 1.77565
Iteration 75  → 1.77115
Iteration 100 → 1.76701
Iteration 125 → 1.76154
Iteration 150 → 1.75785
Iteration 175 → 1.75525
Iteration 199 → 1.75414
```

### Observation

The fitness consistently decreased and flattened near the end of the optimization.

### Inference

PSO successfully converged toward a stable solution without divergence or large oscillations.

---

## 8. Final Full AC Power-Flow Validation

The final schedules were replayed on the complete SimBench network using full AC power flow.

### Final validated results

| Metric | No Battery | Simple Rule | PSO |
|---|---:|---:|---:|
| Cost | €15,059.03 | €14,504.07 | €15,026.38 |
| Peak grid import | 19.3592 MW | 19.3592 MW | **14.3565 MW** |
| Minimum voltage | 0.9879 pu | 0.9879 pu | 0.9879 pu |
| Maximum line loading | 36.7454% | 36.7454% | 36.7454% |
| Maximum transformer loading | 31.3781% | 31.3781% | 31.3781% |
| Losses | 7.5154 MWh | 7.5216 MWh | **7.5148 MWh** |
| Violation steps | 0 | 0 | 0 |

All 96 steps converged for all three strategies:

```text
No battery  → 0 non-converged steps
Simple rule → 0 non-converged steps
PSO         → 0 non-converged steps
```

Total successful daily AC power-flow solves:

```text
96 × 3 = 288
```

---

## 9. Major Result — Peak Import Reduction

PSO reduced peak grid import from:

```text
19.3592 MW
```

to:

```text
14.3565 MW
```

Percentage reduction:

```text
≈ 25.84%
```

### Inference

The strongest validated benefit of the PSO battery scheduler is **peak shaving**.

This result was obtained only after replaying the final PSO schedule through the full SimBench AC network.

---

## 10. Cost Result and Correct Interpretation

No-battery cost:

```text
€15,059.03
```

PSO cost:

```text
€15,026.38
```

Approximate saving:

```text
€32.65/day
≈ 0.22%
```

This is a small cost reduction.

It should not be exaggerated.

### Why the simple rule appears cheaper

The simple rule ended with:

```text
Final SOC ≈ 10%
Equivalent cycles ≈ 1.092
```

PSO ended with:

```text
Final SOC ≈ 47.50%
Equivalent cycles ≈ 0.252
```

The simple rule achieves lower short-term cost partly by draining the battery close to its minimum SOC.

PSO keeps the battery near its original:

```text
Initial SOC = 50%
Final SOC   ≈ 47.5%
```

while using much less battery throughput.

### Correct report interpretation

> Although the simple rule achieved a larger short-term cost reduction, it depleted the battery close to its minimum SOC. PSO produced a more balanced solution by reducing peak grid import while maintaining end-of-day SOC near its initial value and substantially reducing battery cycling.

---

## 11. Surrogate Model vs Full AC Validation

Sarvesh uses a surrogate model inside PSO to avoid running thousands of expensive full-network AC power flows inside every swarm iteration.

For example, the surrogate estimated a PSO minimum voltage close to:

```text
1.003 pu
```

while the final AC validation gave:

```text
0.9879 pu
```

### Inference

The surrogate is an:

```text
optimization accelerator
```

not the final electrical truth.

All final report metrics should come from:

```text
full pandapower AC validation
```

rather than the internal surrogate estimates.

---

## 12. Why Voltage and Line Loading Barely Changed

PSO strongly reduced upstream grid import but produced almost no change in:

```text
minimum voltage
worst line loading
worst transformer loading
```

The battery is installed at the MV substation rather than directly on the feeder containing the most severe downstream voltage/loading point.

### Correct interpretation

> Under the selected MV-substation battery placement, PSO primarily provides peak-shaving and energy-management benefits, while the most stressed downstream feeder voltage and thermal metrics remain largely unchanged.

This should not be presented as a voltage-control improvement.

---

## 13. Scenario Generator Memory Problem

The original scenario notebook attempted to run:

```python
sb.get_absolute_values(...)
```

for the complete annual SimBench profile.

This expanded approximately:

```text
35,136 timestamps × 11,542 loads
```

and attempted a single memory allocation of around:

```text
3.02 GiB
```

which caused a `MemoryError`.

---

## 14. Integration Change — Memory-Safe Profile Cache

The final scenario experiment contains:

```text
300 scenarios
12 scenario types
25 samples per scenario type
```

Only the profile steps required by those scenarios are needed.

The 300 scenario rows resulted in:

```text
297 unique profile steps
```

Therefore only those 297 profile rows plus the required annual-peak point are expanded.

Final cache dimensions:

```text
Load-P cache: (297, 11542)
Load-Q cache: (297, 11542)
SGen-P cache: (297, 806)
```

### Important point

The electrical network was **not reduced**.

The full:

```text
10,458-bus network
11,542 loads
806 static generators
```

remains active.

Only unused annual timestamps were avoided.

---

## 15. Integration Change — Correct SimBench Load Suffixes

The memory-safe profile loader initially exposed a profile-mapping issue.

SimBench load profiles require the correct suffixes:

```text
Active load power   → _pload
Reactive load power → _qload
```

The final implementation explicitly uses these suffixes.

Without them, the reconstructed operating point does not reproduce the validated SimBench state correctly.

---

## 16. Annual-Peak Regression Validation

A regression test was added after implementing the memory-safe profile loader.

The cached annual-peak state reproduced:

```text
Vmin              = 0.994604 pu
Max line loading  = 32.2039%
Max trafo loading = 31.3781%
Grid import       = 19.359202 MW
```

Result:

```text
ANNUAL-PEAK CACHE VALIDATION: PASS
```

### Inference

The memory optimization changed **how** the profiles are loaded, but did not change the electrical operating state being simulated.

---

## 17. Integration Change — Safe BEFORE/AFTER Power-Flow Handling

The scenario validation was modified so that results are never read unless the associated AC power flow converged.

The final sequence is:

```text
Apply scenario
        ↓
Run BEFORE power flow
        ↓
Check convergence
        ↓
Read BEFORE metrics
        ↓
Apply battery command
        ↓
Run AFTER power flow
        ↓
Check convergence
        ↓
Read AFTER metrics
```

The final output stores:

```text
converged_before
converged_after
```

separately.

This prevents stale pandapower result tables from being interpreted as valid new results.

---

## 18. Final Scenario Validation

Final result:

```text
Rows:               300
Before PF failures: 0
After PF failures:  0
```

Therefore all 300 scenario cases converged before and after battery control.

---

## 19. Scenario Types

The 12 scenario classes are:

```text
NORMAL_WEEKDAY
MORNING_PEAK
HIGH_DEMAND
HIGH_SOLAR
LOW_RENEWABLE
PRICE_SPIKE
LOW_BATTERY_SOC
HIGH_BATTERY_SOC
RENEWABLE_SURPLUS
LINE_OUTAGE
TRANSFORMER_STRESS
COMBINED_EXTREME
```

Each scenario class contains 25 samples.

---

## 20. Emergency Battery Support Rule

The scenario notebook uses a simple fallback battery support rule.

Battery discharge is requested when:

```text
Vmin < 0.97 pu
OR
Max line loading > 70%
OR
Max transformer loading > 70%
```

Otherwise the battery remains idle.

This rule is only used for scenario validation.

It is **not** the final fuzzy controller and should not be confused with the PSO schedule.

---

## 21. Battery Activation Across 300 Scenarios

Final battery activation statistics:

| Scenario | Minimum | Maximum | Mean battery power |
|---|---:|---:|---:|
| COMBINED_EXTREME | -5 MW | 0 MW | -0.8 MW |
| TRANSFORMER_STRESS | -5 MW | 0 MW | -1.0 MW |
| Other 10 scenario classes | 0 MW | 0 MW | 0 MW |

### Combined Extreme

Since every activation uses `-5 MW`:

```text
Mean = -0.8 MW
```

corresponds to:

```text
4 of 25 cases
= 16%
```

### Transformer Stress

```text
Mean = -1.0 MW
```

corresponds to:

```text
5 of 25 cases
= 20%
```

### Overall activation

```text
9 activations / 300 scenarios
= 3%
```

### Inference

The emergency support rule is deliberately conservative and activates only under severe electrical stress.

Normal daily optimization remains the responsibility of PSO.

---

## 22. Scenario-Level Grid Import Effect

Most scenario categories show identical BEFORE and AFTER metrics because the battery remained idle.

Two stressed classes showed average grid-import reductions:

| Scenario | Before | After |
|---|---:|---:|
| COMBINED_EXTREME | 21.416 MW | 20.615 MW |
| TRANSFORMER_STRESS | 20.342 MW | 19.341 MW |

### Inference

The battery responds to genuinely stressed individual cases rather than cycling unnecessarily under healthy operation.

---

## 23. Integration Change — Independent Contingency Testing

The contingency workflow was modified so that each test starts from the same healthy annual-peak network state.

Final sequence:

```text
Restore healthy topology
        ↓
Apply annual-peak profile
        ↓
Apply exactly one contingency
        ↓
Run AC power flow
        ↓
Record metrics
        ↓
Restore topology
```

The final contingency section also uses a fresh SimBench network so that previous scenario mutations cannot leak into contingency testing.

---

## 24. Contingency Baseline

Healthy annual-peak baseline:

```text
Vmin              = 0.994604 pu
Max line loading  = 32.2039%
Max trafo loading = 31.3781%
Grid import       = 19.3592 MW
Unsupplied buses  = 0
```

---

## 25. Line 10229 Outage

Result:

```text
Converged         = True
Vmin              = 0.999896 pu
Max line loading  = 29.6489%
Max trafo loading = 31.3250%
Grid import       = 16.3543 MW
Unsupplied buses  = 1247
```

### Observation

The remaining energized portion of the network appears electrically less stressed.

### Correct inference

This is **not** an improvement.

Grid import and loading fall partly because:

```text
1,247 buses are no longer supplied
```

Therefore service continuity must be evaluated together with voltage/loading metrics.

---

## 26. Switch 20735 Opened

Opening switch 20735 produced essentially the same result as the tested line outage:

```text
Unsupplied buses ≈ 1247
Grid import      ≈ 16.354 MW
```

### Inference

The selected switch isolates approximately the same feeder/network section as the explicit line outage.

---

## 27. Generator 805 Lost

Result:

```text
Baseline grid import  = 19.359 MW
After generator loss  = 21.088 MW
Increase              ≈ 1.73 MW
Unsupplied buses      = 0
```

### Inference

When local generation is removed, the external grid supplies the missing power.

This behaviour is physically consistent.

---

## 28. Transformer 5 Derated by 50%

The tested 50% transformer derating produced:

```text
converged = False
```

The result is intentionally stored as non-convergent rather than replaced with stale or fabricated metrics.

### Correct interpretation

> The tested 50% transformer derating created a severe operating condition for which the AC steady-state power-flow solver could not obtain a converged solution.

This should **not** be described as transient instability because no transient simulation is performed.

---

## 29. Integration Change — Safe Non-Convergence Handling

Severe contingency failures previously produced large solver logs including singular-matrix and Iwamoto messages.

The final implementation:

- suppresses unnecessary solver console spam,
- still records convergence status,
- stores invalid electrical metrics as `NaN`,
- and never reads stale result tables after a failed solve.

This improves reliability without hiding actual failures.

---

## 30. Important System-Level Observation

The contingency study proves that voltage and thermal loading alone are not enough to classify grid condition.

For example, after the line outage:

```text
Vmin ≈ 1.0 pu
line loading ≈ 29.65%
```

which appears healthy, but:

```text
1,247 buses are unsupplied
```

Therefore the final grid-state classifier should consider at least:

```text
power-flow convergence
unsupplied buses / topology state
minimum voltage
maximum line loading
maximum transformer loading
```

This observation is important for the later TinyML / Edge-AI stage.

---

## 31. What Was Preserved vs What Was Changed

| Preserved from Sarvesh | Changed during integration |
|---|---|
| Battery physical model | Connected real Hybrid price forecast |
| PSO algorithm | Hourly → 15-minute price interface |
| PSO objective structure | Fixed schedule export dimensionality |
| SOC constraints | Memory-safe SimBench profile loading |
| Battery sign convention | Correct `_pload` and `_qload` handling |
| Battery location | Added annual-peak regression validation |
| Scenario definitions | Added BEFORE/AFTER convergence checks |
| Contingency helper functions | Prevented stale power-flow results |
| 12 scenario classes | Isolated each contingency |
| 25 samples per class | Fresh network for final contingency test |
| Full SimBench network | Safe non-convergence handling |
| 5 MW support rule | Solver log suppression |
| Core PSO logic | Notebook rename to avoid file collision |

### Summary

The core Sarvesh algorithm was **not rewritten**.

The changes were primarily:

```text
integration
correctness
memory efficiency
validation
failure-safe handling
reproducibility
```

---

## 32. Final Key Numbers

```text
Hybrid price forecasting R²     = 0.9636

PSO peak import:
19.3592 MW → 14.3565 MW

Peak reduction                  ≈ 25.84%

PSO final SOC                   ≈ 47.50%
PSO equivalent cycles           ≈ 0.252

PSO cost                        ≈ €15,026.38
No-battery cost                 ≈ €15,059.03

Scenario cases                  = 300
Scenario types                  = 12

Before PF failures              = 0
After PF failures               = 0

Emergency battery activations   = 9 / 300
Activation rate                 = 3%

Line outage unsupplied buses    = 1247
Switch outage unsupplied buses  = 1247

Generator-loss grid import      ≈ 21.088 MW

50% transformer derating        = non-convergent
```

---

## 33. Files Produced

Important integrated files:

```text
digital_twin/sarvesh_lib.py
digital_twin/battery_model.ipynb
digital_twin/pso_battery_optimizer.ipynb
digital_twin/battery_scenario_validation.ipynb
```

Important outputs:

```text
digital_twin/output/battery_config.json
digital_twin/output/price_forecast_day.csv
digital_twin/output/battery_schedule_pso_96.csv
digital_twin/output/battery_pso_powerflow_validation.csv
digital_twin/output/battery_impact_comparison.csv
digital_twin/output/scenario_library.csv
digital_twin/output/scenario_battery_results.csv
digital_twin/output/contingency_results.csv
```

---

## 34. Recommended Execution Order

Run the notebooks in this order:

```text
1. price_hybrid.ipynb
        ↓
2. battery_model.ipynb
        ↓
3. pso_battery_optimizer.ipynb
        ↓
4. battery_scenario_validation.ipynb
```

Expected interface between price forecasting and PSO:

```text
digital_twin/output/price_forecast_day.csv
```

Expected size:

```text
96 rows
```

---

## 35. Final Conclusion

The PSO battery-energy-management subsystem was successfully integrated with the full SimBench digital twin.

The final system uses a Hybrid XGBoost–BiLSTM electricity-price forecast as the external market signal for PSO scheduling. Full AC validation showed that PSO reduced peak grid import from approximately **19.36 MW to 14.36 MW**, corresponding to a **25.84% peak reduction**, while keeping end-of-day SOC close to its initial value and using substantially fewer equivalent battery cycles than the simple rule-based strategy.

The scenario framework was validated across **300 operating cases**, with all BEFORE and AFTER scenario power flows converging. Contingency analysis demonstrated realistic effects including feeder isolation, generator-loss compensation by the external grid, and non-convergence under severe transformer derating.

Several integration changes were introduced to make the module production-safe for the final project: real Hybrid price coupling, 15-minute price conversion, schedule-export correction, memory-safe SimBench profile loading, correct active/reactive profile suffix handling, regression validation against the annual peak, explicit power-flow convergence checks, isolated contingency testing, and safe non-convergence handling.

These changes preserve Sarvesh's original battery and PSO formulation while making the module compatible, reproducible, memory-efficient, and reliable inside the final Smart Grid Digital Twin architecture.
