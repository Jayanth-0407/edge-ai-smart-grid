# Fuzzy Controller Integration Documentation

## Smart Grid Fusion Controller — Sanjay Module

**Module owner:** Sanjay  
**Integration owner:** Sujit / team integration  
**Final role:** Supervisory fuzzy demand-response controller between the prediction/optimization layer and the closed-loop SimBench digital twin.

---

## 1. Purpose

This document records Sanjay's original fuzzy-controller contribution, the integration changes made by the team, and the final validation results.

The key distinction is:

- **Sanjay's original work:** fuzzy membership functions, Mamdani rule system, defuzzification, EV/battery/flexible-load commands, grid-state classification, PSO/fuzzy arbitration, fallback, response curves, replay/validation framework.
- **Integration changes:** eighth `stability_risk` input, two stability-support rules, final demand-response action labels, memory-safe SimBench profile loading, updated replay/validation interfaces, and final full-grid validation.

The original fuzzy-control design is preserved. The integration changes were added only to align it with the updated project architecture.

---

## 2. Position in the Final Project

```text
Public / real datasets
        ↓
Selected prediction models
        ↓
SimBench + pandapower digital twin
        ↓
Jayanth: EV profiles + GA optimization
        +
Sarvesh: battery model + PSO optimization
        ↓
Sanjay: fuzzy supervisory controller
        ↓
EV limit + battery arbitration + flexible-load reduction
        ↓
NO ACTION / LOAD SHIFT / LOAD DEFER / LOAD SHED
        ↓
Apply commands to digital twin
        ↓
Controller OFF vs ON AC power-flow validation
        ↓
Unified dataset
        ↓
TinyML → ESP32 → Dashboard
```

The fuzzy layer is a **supervisory control layer**, not another forecasting model.

---

## 3. Final Fuzzy Inputs

The integrated controller uses eight inputs.

| # | Input | Unit / range | Source | Purpose |
|---|---|---|---|---|
| 1 | `v_min_pu` | 0.85–1.10 pu | SimBench/pandapower | Voltage stress |
| 2 | `line_loading_pct` | 0–150 % | SimBench/pandapower | Feeder thermal stress |
| 3 | `trafo_loading_pct` | 0–150 % | SimBench/pandapower | Transformer stress |
| 4 | `price_eur_mwh` | 0–120 EUR/MWh | Hybrid price forecast | Economic signal |
| 5 | `ev_load_mw` | 0–3 MW | Jayanth EV/GA module | EV charging severity |
| 6 | `renewable_mw` | 0–14 MW | Digital twin | Renewable condition |
| 7 | `battery_soc_pct` | 0–100 % | Sarvesh battery/PSO module | Battery protection/availability |
| 8 | `stability_risk` | 0–1 | Selected grid-stability ML model | Supporting instability-risk signal |

`stability_risk` is a supporting signal. It does not replace AC power-flow measurements.

---

## 4. Input Membership Functions

### Minimum voltage

| Set | Trapezoid |
|---|---|
| LOW | `[0.85, 0.85, 0.92, 0.96]` |
| NORMAL | `[0.94, 0.97, 1.03, 1.05]` |
| HIGH | `[1.04, 1.06, 1.10, 1.10]` |

### Line loading

| Set | Trapezoid (%) |
|---|---|
| LOW | `[0, 0, 30, 50]` |
| NORMAL | `[30, 50, 70, 85]` |
| HIGH | `[70, 85, 95, 105]` |
| OVERLOADED | `[95, 105, 150, 150]` |

### Transformer loading

Same regions as line loading.

### Electricity price

| Set | Trapezoid (EUR/MWh) |
|---|---|
| LOW | `[0, 0, 40, 50]` |
| NORMAL | `[40, 50, 62, 72]` |
| HIGH | `[62, 72, 82, 90]` |
| SPIKE | `[82, 90, 120, 120]` |

### EV load

| Set | Trapezoid (MW) |
|---|---|
| LOW | `[0, 0, 0.5, 1.0]` |
| NORMAL | `[0.5, 1.0, 1.5, 2.0]` |
| HIGH | `[1.5, 2.0, 3.0, 3.0]` |

### Renewable generation

| Set | Trapezoid (MW) |
|---|---|
| LOW | `[0, 0, 2, 4]` |
| MEDIUM | `[2, 4, 6, 8]` |
| HIGH | `[6, 8, 14, 14]` |

### Battery SOC

| Set | Trapezoid (%) |
|---|---|
| LOW | `[0, 0, 15, 30]` |
| NORMAL | `[20, 35, 65, 80]` |
| HIGH | `[70, 85, 100, 100]` |

### Stability risk — added during integration

| Set | Trapezoid |
|---|---|
| LOW | `[0.0, 0.0, 0.30, 0.50]` |
| MEDIUM | `[0.30, 0.50, 0.50, 0.70]` |
| HIGH | `[0.50, 0.70, 1.00, 1.00]` |

---

## 5. Fuzzy Outputs

### EV charging limit

| Set | Trapezoid (%) |
|---|---|
| LOW | `[0, 0, 20, 40]` |
| MEDIUM | `[25, 50, 50, 75]` |
| HIGH | `[60, 85, 100, 100]` |

Calibrated final command range: **10–100%**.

### Battery command

| Set | Trapezoid |
|---|---|
| DISCHARGE | `[-1, -1, -0.7, -0.2]` |
| IDLE | `[-0.3, 0, 0, 0.3]` |
| CHARGE | `[0.2, 0.7, 1, 1]` |

Command sign:

```text
+ = charge
- = discharge
```

### Flexible-load reduction

| Set | Trapezoid (%) |
|---|---|
| NONE | `[0, 0, 2, 6]` |
| SMALL | `[4, 12, 12, 20]` |
| LARGE | `[15, 24, 30, 30]` |

### Grid-state score

| Set | Trapezoid |
|---|---|
| NORMAL | `[0, 0, 0.2, 0.4]` |
| STRESSED | `[0.3, 0.5, 0.5, 0.7]` |
| CRITICAL | `[0.6, 0.8, 1, 1]` |

---

## 6. Fuzzy Inference

The controller is Mamdani-style.

```text
AND → minimum
OR  → maximum
NOT → 1 - membership
SUM → bounded sum, capped at 1
```

Defuzzification uses:

1. clipping,
2. max aggregation,
3. centroid,
4. linear calibration to the desired command range.

Calibration is required because a raw centroid of an extreme fuzzy set does not naturally reach the exact output endpoint.

---

## 7. Original Rule Groups

Sanjay's original executable notebook contained 24 rules.

| Group | Purpose | Count |
|---|---|---:|
| A | Critical/stressed grid | 7 |
| B | Preventive EV control | 1 |
| C | High-price response | 4 |
| D | Normal operation | 6 |
| E | Renewable surplus / over-voltage | 4 |
| F | Battery protection | 2 |
| **Total** |  | **24** |

Important original behavior:

- overload or low-voltage/high-loading conditions reduce EV charging and flexible load,
- stressed grids request battery discharge when SOC permits,
- high price can discharge the battery and shift flexible demand,
- renewable surplus / high voltage encourages battery charging,
- low battery SOC blocks discharge.

---

## 8. Stability-Risk Integration

### Problem

The updated project requires the selected stability-model risk to enter the fuzzy supervisor. The executable notebook originally had only seven inputs.

### Changes

1. Added `stability_risk`.
2. Added aliases:
   - `stability_risk`
   - `instability_probability`
   - `stability_probability`
3. Added `STABILITY_HIGH`.
4. Changed the strongest NORMAL rules so they do not fire when stability risk is HIGH.
5. Added rules G1 and G2.

### G1

```text
IF stability risk is HIGH
AND the physical grid is stressed
THEN:
    EV limit = LOW
    flex reduction = LARGE
    grid state = CRITICAL
weight = 0.8
```

### G2

```text
IF stability risk is HIGH
AND the physical grid is healthy
THEN:
    EV limit = MEDIUM
    grid state = STRESSED
weight = 0.7
```

Final rule count:

```text
A 7
B 1
C 4
D 6
E 4
F 2
G 2
Total = 26
```

Validation:

```text
Rule base OK: 26 rules, all references valid, every output set is used.
```

---

## 9. Stability-Fusion Validation

A dedicated test changed only the ML risk signal.

```text
Healthy grid + stability_risk = 0.90
```

Result:

```text
ev_limit_pct        ≈ 54.45%
battery_cmd          = 0
flex_reduction_pct   = 0
grid_state_score     = 0.50
grid_state           = STRESSED
```

This confirms the intended behavior:

- high ML risk alone warns the controller,
- healthy power-flow measurements prevent a false CRITICAL declaration.

---

## 10. Final Demand-Response Action

The updated project requires:

```text
NO ACTION
LOAD SHIFT
LOAD DEFER
LOAD SHED
```

The final mapping is:

```text
CRITICAL
→ LOAD SHED

STRESSED
→ LOAD DEFER

NORMAL + meaningful economic-control command
→ LOAD SHIFT

NORMAL + effectively no intervention
→ NO ACTION
```

A meaningful economic-control command is considered present when:

```text
ev_limit_pct < 98%
OR
flex_reduction_pct > 2%
```

An earlier hard-coded `price >= 72` decision was removed because it conflicted with the fuzzy price membership functions. At 69.1 EUR/MWh the fuzzy controller was already requesting real demand shifting, so the action label must follow the fuzzy output rather than a second hard threshold.

---

## 11. PSO–Fuzzy Battery Arbitration

Sarvesh's PSO is the planned battery schedule.

Sanjay's fuzzy layer is the real-time supervisor.

```text
Healthy / low-stress grid
→ follow PSO

Grid needs support
→ fuzzy may override PSO
```

The project uses:

```text
BATTERY_OVERRIDE_SCORE = 0.25
```

The final battery request is repaired by Sarvesh's physical battery model.

Battery-limit validation:

```text
Fuzzy request       = -5.00 MW
Battery energy      = 2.2 MWh
Minimum energy      = 2.0 MWh
Allowed command     ≈ -0.76 MW
Energy after        = 2.00 MWh
```

Result:

```text
Battery repair check passed.
```

---

## 12. Flexible-Load Assumption

The controller uses:

```text
FLEX_SHARE = 0.30
```

This means 30% of non-EV demand is assumed to be flexible.

Therefore:

```text
flex_reduction_pct = 30%
```

does not mean total demand drops by 30%.

The maximum effective reduction of non-EV demand is approximately:

```text
30% flexible share × 30% command = 9%
```

This is a project assumption and must be reported as such.

---

## 13. Safe Fallback

If an input is missing, invalid, or non-numeric, the controller returns a conservative fallback rather than crashing.

Fallback behavior includes:

```text
EV limit = 50%
battery fuzzy command = idle
flex reduction = 10%
grid state = CRITICAL
fallback = True
```

At the final action layer, fallback is treated conservatively as `LOAD SHED`.

---

## 14. 300-State Replay

The controller was replayed on 300 previously generated real AC power-flow states.

Replay inputs came from:

- `scenario_battery_results.csv`,
- `scenario_library.csv`,
- `price_forecast_day.csv`,
- Jayanth's optimized EV profile,
- scenario renewable information,
- stored battery SOC.

Because the historical 300-row replay does not contain actual stability-model inference, the replay uses:

```text
stability_risk = 0.0
```

only as a neutral placeholder.

### Bug found

After adding the eighth input, all 300 rows initially entered fallback because `stability_risk` was missing.

Symptoms:

```text
100% CRITICAL
mean EV limit = 50%
mean flex cut = 10%
```

The assertion:

```text
assert not replay["fallback"].any()
```

correctly exposed the problem.

### Fix

Added neutral `stability_risk=0.0` to `replay_state()`.

### Final replay behavior

The 300 stored states were electrically mild and remained NORMAL.

Representative extremes:

```text
max line loading  ≈ 72%
max trafo loading ≈ 75%
minimum voltage   ≈ 1.0 pu
```

Actions were mainly `LOAD SHIFT` or `NO ACTION`.

This replay is not the final stress proof; the full SimBench OFF-vs-ON test is.

---

## 15. Memory-Safe Full-Grid Fix

### Original issue

The old loader expanded the full annual profile:

```text
35,136 timestamps × 11,542 loads
```

This caused multi-gigabyte memory pressure.

### Correct fix

Keep the full SimBench electrical network, but load only the required day:

```text
DAY_START = 32928
96 × 15-min steps
```

Result:

```text
load P : (96, 11542)
load Q : (96, 11542)
sgen P : (96, 806)

first : 09.12.2016 00:00
last  : 09.12.2016 23:45
```

The full **10,458-bus network remains intact**. Only unnecessary time-series expansion was removed.

---

## 16. Real Full-Grid Stress Smoke Test

Validation point:

```text
timestamp        = 09.12.2016 18:15
load             = 4.0×
renewables       = 0.2×
EV load          ≈ 0.205 MW
PSO battery      = -5 MW
price            ≈ 69.088 EUR/MWh
stability risk   = 0.0 for standalone test
```

Controller input:

```text
v_min_pu          0.858
line loading      143.568%
trafo loading     140.411%
price             69.088 EUR/MWh
EV load           0.205 MW
renewable         0.344 MW
battery SOC       50%
stability risk    0.0
```

Controller output:

```text
grid state        CRITICAL
score             1.0
action            LOAD SHED
EV limit          10%
battery           -5 MW
battery source    fuzzy
flex reduction    30%
fallback          False
```

Electrical result:

| Metric | OFF | ON | Change |
|---|---:|---:|---:|
| Max line loading | 143.568% | 128.018% | -15.550 pp |
| Max trafo loading | 140.411% | 124.046% | -16.365 pp |
| Total losses | 4.351 MW | 3.523 MW | -0.828 MW |
| Grid import | 81.714 MW | 73.277 MW | -8.437 MW |
| Violation count | 9884 | 8661 | -1223 |
| Minimum voltage | 0.858 pu | 0.879 pu | +0.022 pu |

Every tracked metric improved.

The extreme case is **mitigated, not fully restored**.

---

## 17. Final Five-Scenario Validation

Scenarios:

1. `NORMAL_CONTROL`
2. `EV_HEAVY_2X`
3. `EV_HEAVY_3X`
4. `DEMAND_STRESS_4X`
5. `COMBINED_EXTREME`

All OFF and ON power flows converged.

### Commands

| Scenario | State | EV limit | Battery | Flex reduction | Action |
|---|---|---:|---:|---:|---|
| NORMAL_CONTROL | NORMAL | 95.43% | -5.0 MW | 10.56% | LOAD SHIFT |
| EV_HEAVY_2X | NORMAL | 95.43% | -5.0 MW | 10.56% | LOAD SHIFT |
| EV_HEAVY_3X | NORMAL | 95.43% | -5.0 MW | 10.56% | LOAD SHIFT |
| DEMAND_STRESS_4X | CRITICAL | 10.00% | -5.0 MW | 30.00% | LOAD SHED |
| COMBINED_EXTREME | CRITICAL | 42.95% | -5.0 MW | 18.08% | LOAD SHED |

A NORMAL state can still produce `LOAD SHIFT` because economic response can occur while the grid remains electrically healthy.

---

## 18. OFF vs ON Results

### OFF

| Scenario | Line % | Trafo % | Loss MW | Import MW | Violations | Vmin |
|---|---:|---:|---:|---:|---:|---:|
| NORMAL_CONTROL | 32.49 | 34.51 | 0.46 | 14.57 | 0 | 0.99 |
| EV_HEAVY_2X | 35.72 | 37.79 | 0.46 | 14.77 | 0 | 0.99 |
| EV_HEAVY_3X | 39.28 | 41.10 | 0.47 | 14.98 | 0 | 0.99 |
| DEMAND_STRESS_4X | 143.57 | 140.41 | 4.35 | 81.71 | 9884 | 0.86 |
| COMBINED_EXTREME | 81.89 | 86.96 | 1.39 | 40.79 | 8 | 0.95 |

### ON

| Scenario | Line % | Trafo % | Loss MW | Import MW | Violations | Vmin |
|---|---:|---:|---:|---:|---:|---:|
| NORMAL_CONTROL | 31.44 | 33.36 | 0.44 | 13.89 | 0 | 1.00 |
| EV_HEAVY_2X | 34.46 | 36.49 | 0.45 | 14.09 | 0 | 0.99 |
| EV_HEAVY_3X | 37.85 | 39.64 | 0.45 | 14.29 | 0 | 0.99 |
| DEMAND_STRESS_4X | 128.02 | 124.05 | 3.52 | 73.28 | 8661 | 0.88 |
| COMBINED_EXTREME | 75.09 | 78.46 | 1.25 | 38.05 | 0 | 0.96 |

Summary:

```text
max line loading       improved in 5/5
max trafo loading      improved in 5/5
total losses           improved in 5/5
grid import            improved in 5/5
minimum voltage        improved in 5/5
violation count        improved where violations existed; worse in 0
```

---

## 19. Demand Sweep

| Load | Vmin OFF | Vmin ON | Line OFF | Line ON | Trafo OFF | Trafo ON | EV limit | State |
|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 1.0× | 0.99 | 1.00 | 32.50% | 31.44% | 34.51% | 33.36% | 95.43% | NORMAL |
| 1.5× | 0.98 | 0.98 | 49.16% | 47.55% | 50.47% | 48.79% | 95.43% | NORMAL |
| 2.0× | 0.96 | 0.96 | 66.38% | 64.10% | 66.79% | 63.96% | 77.80% | NORMAL |
| 2.5× | 0.94 | 0.94 | 84.25% | 77.96% | 83.52% | 76.12% | 33.02% | CRITICAL |
| 3.0× | 0.91 | 0.92 | 102.92% | 93.89% | 101.22% | 91.35% | 22.04% | CRITICAL |
| 3.5× | 0.89 | 0.90 | 122.60% | 109.78% | 120.22% | 106.49% | 10.00% | CRITICAL |
| 4.0× | 0.86 | 0.88 | 143.57% | 128.02% | 140.41% | 124.05% | 10.00% | CRITICAL |

At `3.0×`, both line and transformer loading are brought below 100%.

At `4.0×`, the controller still helps substantially but cannot fully restore the grid.

---

## 20. Ablation Study

Ablation on `DEMAND_STRESS_4X`:

| Variant | Line % | Trafo % | Loss MW | Import MW | Violations | Vmin |
|---|---:|---:|---:|---:|---:|---:|
| Controller OFF | 143.57 | 140.41 | 4.35 | 81.71 | 9884 | 0.86 |
| EV limit only | 143.25 | 138.68 | 4.33 | 81.51 | 9830 | 0.86 |
| Flexible load only | 128.33 | 125.73 | 3.54 | 73.48 | 8696 | 0.88 |
| Battery only | 143.57 | 140.41 | 4.35 | 81.71 | 9884 | 0.86 |
| All three | **128.02** | **124.05** | **3.52** | **73.28** | **8661** | **0.88** |

Interpretation:

- flexible-load control dominates this 4× base-demand stress case,
- EV-only effect is small because EV load at this instant is only about 0.20 MW,
- battery-only is unchanged because PSO was already commanding `-5 MW`, exactly what fuzzy also wanted,
- all three gives the best overall result.

---

## 21. Jayanth GA Handoff

```text
ACN EV data
→ EV profile generation
→ GA optimized EV schedule
→ current EV load
→ Sanjay fuzzy supervisor
→ additional EV limit if required
```

GA and fuzzy are not duplicates:

- **GA:** planned EV scheduling across the horizon.
- **Fuzzy:** supervisory correction based on the current grid condition.

Final EV load is conceptually:

```text
GA-planned EV load × fuzzy ev_factor
```

---

## 22. Sarvesh PSO Handoff

```text
price + base load
→ PSO battery plan
→ fuzzy supervisor
→ follow PSO or override under stress
→ battery physical repair
→ final battery command
```

This preserves the distinction between optimization and real-time supervision.

---

## 23. Final Controller Interface

The final controller returns:

```text
ev_limit_pct
ev_factor
battery_mw
battery_source
battery_energy_next_mwh
flex_reduction_pct
flex_factor
grid_state
grid_state_score
action
fallback
```

These values are suitable for:

- the unified integration engine,
- final dataset generation,
- dashboard display,
- later TinyML dataset labeling.

---

## 24. Original vs Integration Changes

| Area | Original | Integration change |
|---|---|---|
| Inputs | 7 executable inputs | Added `stability_risk` |
| Rules | 24, A–F | Added G1/G2 → 26 total |
| Normal rules | Could fire during high ML risk | D1/D5 blocked by high stability risk |
| Stability-model handoff | Missing | Added aliases and supporting fusion |
| Final DR action | Missing | Added NO ACTION / SHIFT / DEFER / SHED |
| Economic label | Not present | Derived from fuzzy outputs |
| 300-state replay | Broke after 8th input | Added neutral `stability_risk=0.0` |
| SimBench profiles | Full annual expansion | Memory-safe 96-step day slice |
| Electrical network | Full | Full network preserved |
| `before_after()` | No stability-risk handoff | Added parameter |
| Validation CSV | No final action | Added action |
| Battery repair | Present | Preserved and validated |

---

## 25. Output Files

Typical outputs:

```text
output/fuzzy_input_membership.png
output/fuzzy_output_membership.png
output/fuzzy_rule_base.csv
output/controller_scenario_replay.csv
output/controller_scenario_replay.png
output/controller_validation.csv
output/controller_commands.csv
output/controller_validation.png
output/controller_sweep.csv
output/controller_sweep.png
output/controller_ablation.csv
```

---

## 26. Known Limitations

1. **Extreme cases may remain unsafe.** The 4× demand case improves but still violates voltage/thermal limits.
2. **Flexible-load availability is assumed.** `FLEX_SHARE = 0.30` is not directly measured from SimBench.
3. **Legacy replay uses neutral stability risk.** Real stability-model inference must be supplied by the final integration engine.
4. **Price is an external market signal.** It is not claimed to be timestamp-synchronized with the selected SimBench day.
5. **Outage health needs service-availability metrics.** A line outage can reduce loading while disconnecting buses.
6. **NORMAL/STRESSED/CRITICAL are supervisory operating labels, not transient/frequency-stability proof.**

For the final unified dataset/TinyML stage, also consider:

```text
power_flow_converged
unsupplied_buses
topology / outage status
```

because voltage/loading alone can look healthy after a feeder is disconnected.

---

## 27. TinyML / ESP32 Relationship

The fuzzy controller stays on the PC in the main closed-loop simulation.

After GA + PSO + fuzzy integration, a separate compact ANN will be trained from the unified digital-twin dataset.

Candidate ESP32 inputs:

```text
v_min_pu
max_line_loading_pct
max_trafo_loading_pct
grid_import_mw
renewable_mw
ev_load_mw
battery_soc_pct
price_eur_mwh
```

The ESP32 receives measurements, performs TinyML inference locally, and returns:

```text
NORMAL / WARNING / CRITICAL
```

to the dashboard.

---

## 28. Dashboard Requirements

Show:

### Inputs
- Vmin
- line loading
- transformer loading
- price
- EV load
- renewable generation
- battery SOC
- stability risk

### Fuzzy outputs
- state + score
- final DR action
- EV limit
- battery command/source
- flexible-load reduction
- fallback status

### Validation
- OFF vs ON Vmin
- OFF vs ON line loading
- OFF vs ON transformer loading
- losses
- import
- violations

### Evidence plots
- membership functions
- response curves
- OFF-vs-ON chart
- demand sweep
- ablation

---

## 29. Recommended Run Order

```text
1. Membership functions
2. Rule base + validation
3. Defuzzification tests
4. Stability-risk test
5. Interface tests
6. Battery-repair test
7. 300-state replay
8. Memory-safe one-day SimBench load
9. Real stress smoke test
10. Five-scenario validation
11. Demand sweep
12. Ablation
13. Save CSV/PNG outputs
14. Freeze controller logic
15. Integrate through integration_engine.py
```

---

## 30. Viva Explanation

> The fuzzy controller is the supervisory layer of our smart-grid system. GA plans EV charging and PSO plans battery operation, while the fuzzy controller observes the actual grid condition and decides whether those plans should be restricted or overridden. It uses voltage, line loading, transformer loading, price, EV load, renewable generation, battery SOC and a supporting stability-risk signal. Mamdani inference produces continuous EV, battery and flexible-load commands, which are then mapped to NO ACTION, LOAD SHIFT, LOAD DEFER or LOAD SHED. We apply those commands back to the full 10,458-bus SimBench network and rerun AC power flow. In the 4× demand case, line loading falls from 143.57% to 128.02%, transformer loading from 140.41% to 124.05%, grid import from 81.71 MW to 73.28 MW, and Vmin improves from 0.858 pu to about 0.879 pu. The controller mitigates the extreme case but does not falsely claim complete restoration.

---

## 31. Final Status

```text
Fuzzy core                           VALIDATED
8-input interface                    VALIDATED
26-rule base                         VALIDATED
Stability-risk fusion                VALIDATED
DR action mapping                    VALIDATED
PSO/fuzzy arbitration                VALIDATED
Battery physical-limit repair        VALIDATED
300-state replay                     VALIDATED
Memory-safe full-grid loading        VALIDATED
Real SimBench stress test            VALIDATED
Five-scenario OFF-vs-ON test         VALIDATED
Demand sweep                         VALIDATED
Ablation                             VALIDATED
```

**The fuzzy-controller logic should now be frozen. The next step is the unified GA + PSO + fuzzy integration engine and final dataset generation for TinyML and the dashboard.**
