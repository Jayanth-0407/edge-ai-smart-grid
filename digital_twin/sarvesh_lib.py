"""
sarvesh_lib.py  --  Sarvesh's modules for the Smart Grid Digital Twin
Battery model + PSO optimiser + scenario / contingency helpers.

Sign convention (same as pandapower storage):
    p_batt_mw > 0  -> battery CHARGING  (extra load on grid)
    p_batt_mw < 0  -> battery DISCHARGING (supports grid)
"""
from dataclasses import dataclass, asdict
import numpy as np
import pandas as pd

DT_H = 0.25      # 15-minute resolution
T = 96           # steps per day


# ----------------------------------------------------------------------------
# 1. BATTERY MODEL
# ----------------------------------------------------------------------------
@dataclass
class BatteryConfig:
    bus: int = -1                 # pandapower bus index (set after choosing location)
    p_max_mw: float = 5.0         # max charge AND discharge power
    e_cap_mwh: float = 20.0       # usable energy capacity
    soc_min: float = 0.10         # 10 %
    soc_max: float = 0.90         # 90 %
    soc_init: float = 0.50
    eta_c: float = 0.95           # charging efficiency
    eta_d: float = 0.95           # discharging efficiency

    @property
    def e_min(self):  return self.soc_min * self.e_cap_mwh
    @property
    def e_max(self):  return self.soc_max * self.e_cap_mwh
    @property
    def e_init(self): return self.soc_init * self.e_cap_mwh


def soc_next(E, p, cfg: BatteryConfig, dt=DT_H):
    """Energy (MWh) after one step. Works for scalars or numpy arrays."""
    p = np.asarray(p, dtype=float)
    return E + np.where(p >= 0, cfg.eta_c * p * dt, p * dt / cfg.eta_d)


def feasible_power(E, p_req, cfg: BatteryConfig, dt=DT_H):
    """Clip a requested power so power and SOC limits are never violated."""
    p_ch_max = (cfg.e_max - E) / (cfg.eta_c * dt)        # room to charge
    p_dis_max = -(E - cfg.e_min) * cfg.eta_d / dt         # energy available (negative)
    hi = np.minimum(cfg.p_max_mw, p_ch_max)
    lo = np.maximum(-cfg.p_max_mw, p_dis_max)
    return np.clip(p_req, lo, np.maximum(lo, hi))


def simulate_schedule(p_req, cfg: BatteryConfig, E0=None, dt=DT_H):
    """
    Repair + simulate schedules.  p_req: (N, T) or (T,).
    Returns p_feasible, E (N, T+1).  Guarantees SOC/power limits (hard repair).
    """
    p_req = np.atleast_2d(np.asarray(p_req, dtype=float))
    N, Tn = p_req.shape
    E = np.zeros((N, Tn + 1))
    E[:, 0] = cfg.e_init if E0 is None else E0
    p = np.zeros_like(p_req)
    for t in range(Tn):
        p[:, t] = feasible_power(E[:, t], p_req[:, t], cfg, dt)
        E[:, t + 1] = soc_next(E[:, t], p[:, t], cfg, dt)
    return p, E


def mode_of(p, tol=1e-3):
    return np.where(p > tol, "CHARGE", np.where(p < -tol, "DISCHARGE", "IDLE"))


# ----------------------------------------------------------------------------
# 2. SURROGATE (fast) MODEL FOR PSO
#    Full AC power flow on 10,458 buses is too slow inside a PSO loop
#    (60 particles x 150 iterations x 96 steps).  PSO therefore uses
#    quadratic/linear fits calibrated from REAL pandapower runs, and the final
#    schedule is re-validated with full AC power flow.
# ----------------------------------------------------------------------------
@dataclass
class Surrogate:
    base_import_mw: np.ndarray    # (T,) grid import WITHOUT battery
    price: np.ndarray             # (T,) EUR/MWh
    loss_coef: np.ndarray         # polyfit deg 2:  loss_mw = a*imp^2 + b*imp + c
    vmin_coef: np.ndarray         # polyfit deg 1:  vmin_pu = d*imp + e
    v_limit: float = 0.95


def fit_surrogate(imports, losses, vmins, base_import, price, v_limit=0.95):
    loss_coef = np.polyfit(imports, losses, 2)
    vmin_coef = np.polyfit(imports, vmins, 1)
    return Surrogate(np.asarray(base_import), np.asarray(price), loss_coef, vmin_coef, v_limit)


def evaluate(p, sur: Surrogate, cfg: BatteryConfig, E, dt=DT_H):
    """Return dict of metric arrays (N,) for feasible schedules p (N,T)."""
    imp = sur.base_import_mw[None, :] + p                      # grid import with battery
    cost = (sur.price[None, :] * imp * dt).sum(1)              # EUR
    peak = imp.max(1)                                          # MW
    loss = np.polyval(sur.loss_coef, imp)                      # MW
    loss_mwh = (loss * dt).sum(1)
    vmin = np.polyval(sur.vmin_coef, imp)
    vviol = (np.maximum(0.0, sur.v_limit - vmin) ** 2).sum(1)
    soc_end_dev = ((E[:, -1] - cfg.e_init) / cfg.e_cap_mwh) ** 2
    throughput = (np.abs(p) * dt).sum(1) / cfg.e_cap_mwh       # equivalent cycles*2
    return dict(cost=cost, peak=peak, loss_mwh=loss_mwh, vmin_min=vmin.min(1),
                vviol=vviol, soc_end_dev=soc_end_dev, throughput=throughput)


DEFAULT_WEIGHTS = dict(cost=1.0, peak=0.6, loss=0.3, volt=50.0, soc_end=2.0, wear=0.02)


def make_fitness(sur: Surrogate, cfg: BatteryConfig, weights=None):
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    p0, E0 = simulate_schedule(np.zeros(T), cfg)
    m0 = evaluate(p0, sur, cfg, E0)
    c0, k0, l0 = abs(m0["cost"][0]) + 1e-9, m0["peak"][0] + 1e-9, m0["loss_mwh"][0] + 1e-9

    def fitness(P):
        p, E = simulate_schedule(P, cfg)
        m = evaluate(p, sur, cfg, E)
        f = (w["cost"] * m["cost"] / c0 + w["peak"] * m["peak"] / k0 +
             w["loss"] * m["loss_mwh"] / l0 + w["volt"] * m["vviol"] * 1e3 +
             w["soc_end"] * m["soc_end_dev"] + w["wear"] * m["throughput"])
        return f, p
    return fitness


# ----------------------------------------------------------------------------
# 3. PARTICLE SWARM OPTIMISATION
# ----------------------------------------------------------------------------
def pso(fitness, dim, lb, ub, init_particles=None, n_particles=60, iters=150,
        w_max=0.9, w_min=0.4, c1=1.6, c2=1.6, seed=42, verbose=True):
    """
    fitness(P) -> (f[N], P_repaired[N,dim]).  Minimises f.
    Particle = 96 battery power set-points (MW).  Repaired positions are written
    back (Lamarckian repair) so every particle stays feasible.
    """
    rng = np.random.default_rng(seed)
    X = rng.uniform(lb, ub, (n_particles, dim))
    if init_particles is not None:                       # seed good starting guesses
        for i, s in enumerate(init_particles[:n_particles]):
            X[i] = s
    vmax = 0.2 * (ub - lb)
    V = rng.uniform(-vmax, vmax, (n_particles, dim))
    f, X = fitness(X)
    pbest, pbest_f = X.copy(), f.copy()
    g = pbest_f.argmin()
    gbest, gbest_f = pbest[g].copy(), pbest_f[g]
    history = [gbest_f]
    for it in range(iters):
        w = w_max - (w_max - w_min) * it / max(1, iters - 1)
        r1, r2 = rng.random((n_particles, dim)), rng.random((n_particles, dim))
        V = w * V + c1 * r1 * (pbest - X) + c2 * r2 * (gbest - X)
        V = np.clip(V, -vmax, vmax)
        X = np.clip(X + V, lb, ub)
        f, X = fitness(X)
        better = f < pbest_f
        pbest[better], pbest_f[better] = X[better], f[better]
        g = pbest_f.argmin()
        if pbest_f[g] < gbest_f:
            gbest, gbest_f = pbest[g].copy(), pbest_f[g]
        history.append(gbest_f)
        if verbose and (it % 25 == 0 or it == iters - 1):
            print(f"iter {it:3d}  best fitness = {gbest_f:.5f}")
    return gbest, gbest_f, np.array(history)


def rule_based_schedule(price, cfg: BatteryConfig, lo_q=0.30, hi_q=0.70):
    """'Simple strategy' baseline: charge in cheap hours, discharge in expensive hours."""
    lo, hi = np.quantile(price, lo_q), np.quantile(price, hi_q)
    p = np.where(price <= lo, cfg.p_max_mw, np.where(price >= hi, -cfg.p_max_mw, 0.0))
    return p


# ----------------------------------------------------------------------------
# 4. pandapower / SimBench helpers  (imports kept local so the pure-numpy part
#    above can be used/tested without pandapower)
# ----------------------------------------------------------------------------
def apply_profile_step(net, profiles, step):
    """Write SimBench profile values of time-step `step` into the net (positional index)."""
    for (elm, param), df in profiles.items():
        if df.shape[1] > 0 and elm in net and len(net[elm]):
            net[elm].loc[df.columns, param] = df.iloc[step].values


def choose_battery_bus(net, mode="mv_substation"):
    """
    mode='mv_substation' : 10 kV bus on the LV side of the 110/10 kV transformer
    mode='heaviest_mv'   : 10 kV bus with the largest connected load
    """
    if mode == "mv_substation":
        hv = net.trafo[net.trafo.vn_hv_kv >= 100]
        if len(hv):
            return int(hv.lv_bus.iloc[0])
    mv = net.bus[(net.bus.vn_kv > 5) & (net.bus.vn_kv < 30)].index
    load_by_bus = net.load[net.load.bus.isin(mv)].groupby("bus").p_mw.sum()
    return int(load_by_bus.idxmax())


def add_battery(net, cfg: BatteryConfig, name="BESS_1"):
    import pandapower as pp
    if cfg.bus < 0:
        cfg.bus = choose_battery_bus(net)
    idx = pp.create_storage(net, bus=cfg.bus, p_mw=0.0, max_e_mwh=cfg.e_cap_mwh,
                            soc_percent=cfg.soc_init * 100, min_e_mwh=cfg.e_min,
                            max_p_mw=cfg.p_max_mw, min_p_mw=-cfg.p_max_mw, name=name)
    return idx


def set_battery_power(net, sidx, p_mw):
    net.storage.at[sidx, "p_mw"] = float(p_mw)


def run_pf(net):
    import pandapower as pp
    try:
        pp.runpp(net, algorithm="nr", init="results" if net.get("converged", False) else "auto",
                 numba=True, max_iteration=30)
        return True
    except Exception:
        try:
            pp.runpp(net, algorithm="iwamoto_nr", init="flat", max_iteration=50)
            return True
        except Exception:
            return False


def grid_metrics(net):
    """Steady-state security metrics from AC power flow results (NaN-safe for islands)."""
    vm = net.res_bus.vm_pu
    ll = net.res_line.loading_percent
    tl = net.res_trafo.loading_percent
    return dict(
        v_min_pu=float(np.nanmin(vm)), v_max_pu=float(np.nanmax(vm)), v_mean_pu=float(np.nanmean(vm)),
        worst_bus=int(vm.idxmin()),
        max_line_loading_pct=float(np.nanmax(ll)), worst_line=int(ll.idxmax()),
        max_trafo_loading_pct=float(np.nanmax(tl)), worst_transformer=int(tl.idxmax()),
        grid_import_mw=float(net.res_ext_grid.p_mw.sum()),
        line_losses_mw=float(net.res_line.pl_mw.sum()),
        transformer_losses_mw=float(net.res_trafo.pl_mw.sum()),
        voltage_violation_count=int(((vm < 0.95) | (vm > 1.05)).sum()),
        overload_count=int((ll > 100).sum() + (tl > 100).sum()),
        unsupplied_buses=int(vm.isna().sum()),
    )


def run_day(net, profiles, day_start_step, p_batt=None, sidx=None, extra_hook=None):
    """Run 96 AC power flows for one day. p_batt: (96,) MW schedule or None."""
    rows = []
    for t in range(T):
        apply_profile_step(net, profiles, day_start_step + t)
        if extra_hook is not None:
            extra_hook(net, t)
        if sidx is not None:
            set_battery_power(net, sidx, 0.0 if p_batt is None else p_batt[t])
        ok = run_pf(net)
        row = dict(t=t, converged=ok)
        if ok:
            row.update(grid_metrics(net))
        rows.append(row)
    return pd.DataFrame(rows)


def calibrate_surrogate_from_pf(net, profiles, day_start_step, sidx, cfg, price,
                                sample_every=4):
    """Run real AC power flows to (a) get base import curve, (b) fit loss & Vmin surrogates."""
    base_imp = np.zeros(T)
    imps, losses, vmins = [], [], []
    for t in range(T):
        apply_profile_step(net, profiles, day_start_step + t)
        set_battery_power(net, sidx, 0.0)
        run_pf(net)
        m = grid_metrics(net)
        base_imp[t] = m["grid_import_mw"]
        if t % sample_every == 0:
            levels = [0.0, cfg.p_max_mw, -cfg.p_max_mw]
            for lv in levels:
                set_battery_power(net, sidx, lv)
                if run_pf(net):
                    mm = grid_metrics(net)
                    imps.append(mm["grid_import_mw"])
                    losses.append(mm["line_losses_mw"] + mm["transformer_losses_mw"])
                    vmins.append(mm["v_min_pu"])
    set_battery_power(net, sidx, 0.0)
    return fit_surrogate(imps, losses, vmins, base_imp, price)


# ----------------------------------------------------------------------------
# 5. SCENARIOS + CONTINGENCIES
# ----------------------------------------------------------------------------
# hours: (start_hour, end_hour) window to sample profile_step from
SCENARIO_LIBRARY = {
    "NORMAL_WEEKDAY":     dict(hours=(10, 16), load=1.0, pv=1.0, price=1.0, soc=0.50, weekday=True),
    "MORNING_PEAK":       dict(hours=(7, 9),   load=1.3, pv=0.4, price=1.2, soc=0.60, weekday=True),
    "HIGH_DEMAND":        dict(hours=(17, 20), load=2.0, pv=0.2, price=1.4, soc=0.60, weekday=True),
    "HIGH_SOLAR":         dict(hours=(11, 14), load=0.9, pv=1.3, price=0.6, soc=0.40, weekday=False),
    "LOW_RENEWABLE":      dict(hours=(17, 21), load=1.2, pv=0.1, price=1.3, soc=0.55, weekday=True),
    "PRICE_SPIKE":        dict(hours=(18, 21), load=1.3, pv=0.2, price=3.0, soc=0.70, weekday=True),
    "LOW_BATTERY_SOC":    dict(hours=(17, 21), load=1.5, pv=0.2, price=1.8, soc=0.15, weekday=True),
    "HIGH_BATTERY_SOC":   dict(hours=(17, 21), load=1.5, pv=0.2, price=1.8, soc=0.90, weekday=True),
    "RENEWABLE_SURPLUS":  dict(hours=(11, 14), load=0.7, pv=1.5, price=0.4, soc=0.30, weekday=False),
    "LINE_OUTAGE":        dict(hours=(17, 20), load=1.5, pv=0.3, price=1.5, soc=0.60, weekday=True, outage="line"),
    "TRANSFORMER_STRESS": dict(hours=(17, 20), load=2.0, pv=0.2, price=1.5, soc=0.60, weekday=True, outage="trafo"),
    "COMBINED_EXTREME":   dict(hours=(18, 20), load=2.5, pv=0.1, price=3.0, soc=0.50, weekday=True, outage="line"),
}


def sample_profile_steps(name, n=25, seed=0):
    """Reproducible profile_step identifiers (15-min steps of the 35,136-step year)."""
    cfg = SCENARIO_LIBRARY[name]
    rng = np.random.default_rng(sum(ord(c) for c in name) * 1000 + seed)   # reproducible
    rows = []
    while len(rows) < n:
        day = int(rng.integers(0, 366))
        dow = (day + 5) % 7                      # 2016-01-01 was a Friday -> Mon=0 ... (approx.)
        is_weekday = dow < 5
        if cfg["weekday"] != is_weekday:
            continue
        h0, h1 = cfg["hours"]
        step_in_day = int(rng.integers(h0 * 4, h1 * 4))
        rows.append(dict(scenario=name, sample=len(rows), profile_step=day * 96 + step_in_day,
                         day_of_year=day, hour=step_in_day / 4))
    return pd.DataFrame(rows)


def build_scenario_table(n=25, seed=0):
    parts = []
    for name, c in SCENARIO_LIBRARY.items():
        df = sample_profile_steps(name, n, seed)
        for k in ("load", "pv", "price", "soc"):
            df[f"{k}_mult" if k != "soc" else "battery_soc"] = c[k]
        df["outage"] = c.get("outage", "none")
        df["scenario_id"] = [f"{name}_{i:02d}" for i in df["sample"]]
        parts.append(df)
    return pd.concat(parts, ignore_index=True)


def scale_injections(net, load_mult=1.0, pv_mult=1.0):
    """Scale CURRENT loads / sgens (call after apply_profile_step)."""
    net.load["p_mw"] *= load_mult
    net.load["q_mvar"] *= load_mult
    if len(net.sgen):
        net.sgen["p_mw"] *= pv_mult
        net.sgen["q_mvar"] *= pv_mult


# --- contingencies: all change real topology / ratings, then pandapower is re-run
def line_outage(net, line_idx):
    net.line.at[line_idx, "in_service"] = False
    return ("line", int(line_idx))


def open_switch(net, switch_idx):
    net.switch.at[switch_idx, "closed"] = False
    return ("switch", int(switch_idx))


def derate_transformer(net, trafo_idx, factor=0.6):
    net.trafo.at[trafo_idx, "sn_mva"] *= factor
    return ("trafo_derate", int(trafo_idx), factor)


def remove_generator(net, sgen_idx):
    net.sgen.at[sgen_idx, "in_service"] = False
    return ("sgen", int(sgen_idx))


def pick_critical_line(net, voltage_class="mv"):
    """Most loaded line of the requested voltage class from the latest power-flow result."""
    from_bus_vn = net.bus.loc[net.line.from_bus, "vn_kv"].values
    mask = (from_bus_vn > 5) if voltage_class == "mv" else (from_bus_vn < 1)
    ll = net.res_line.loading_percent[mask]
    return int(ll.idxmax())


def pick_loaded_trafo(net):
    return int(net.res_trafo.loading_percent.idxmax())


def apply_scenario_row(net, profiles, row, sidx=None, battery_cfg=None):
    """Apply one scenario-table row to the digital twin (before power flow)."""
    apply_profile_step(net, profiles, int(row["profile_step"]))
    scale_injections(net, row["load_mult"], row["pv_mult"])
    if sidx is not None and battery_cfg is not None:
        net.storage.at[sidx, "soc_percent"] = row["battery_soc"] * 100
    info = None
    if row["outage"] in ("line", "trafo"):
        run_pf(net)                                       # find the real critical asset first
        if row["outage"] == "line":
            info = line_outage(net, pick_critical_line(net, "mv"))
        else:
            info = derate_transformer(net, pick_loaded_trafo(net), 0.6)
    return info


def reset_contingencies(net, snapshot):
    """Restore topology/ratings from a dict saved by snapshot_state()."""
    net.line["in_service"] = snapshot["line_in_service"]
    net.switch["closed"] = snapshot["switch_closed"]
    net.trafo["sn_mva"] = snapshot["trafo_sn"]
    net.sgen["in_service"] = snapshot["sgen_in_service"]


def snapshot_state(net):
    return dict(line_in_service=net.line["in_service"].copy(), switch_closed=net.switch["closed"].copy(),
                trafo_sn=net.trafo["sn_mva"].copy(), sgen_in_service=net.sgen["in_service"].copy())


# ----------------------------------------------------------------------------
# 6. INTEGRATION INTERFACE (for Sujit's digital-twin core and Sanjay's fuzzy controller)
# ----------------------------------------------------------------------------
def battery_dispatch(p_scheduled, E_now, cfg: BatteryConfig, fuzzy_cmd_mw=None):
    """
    Final battery power for ONE step.
    p_scheduled : PSO day-ahead set-point (MW)
    fuzzy_cmd_mw: optional real-time override from Sanjay's controller
                  (None = follow PSO, otherwise this value replaces it)
    The result is always repaired to respect SOC and power limits.
    """
    p_req = p_scheduled if fuzzy_cmd_mw is None else fuzzy_cmd_mw
    p = float(feasible_power(E_now, p_req, cfg))
    return p, float(soc_next(E_now, p, cfg))


def schedule_to_dataframe(p, E, price, cfg, start_ts=None):
    p = np.asarray(p).squeeze()          # Force one 96-step schedule to 1-D
    E = np.asarray(E).squeeze()          # Force 97 SOC-energy points to 1-D
    price = np.asarray(price).squeeze()  # Force 96 prices to 1-D

    assert len(p) == T and len(price) == T
    assert len(E) == T + 1

    ts = pd.date_range(start_ts or "2016-01-01", periods=T, freq="15min")
    return pd.DataFrame(dict(
        step=np.arange(T), timestamp=ts, price=price,
        battery_power_mw=p,
        battery_soc=E[1:] / cfg.e_cap_mwh * 100,
        battery_mode=mode_of(p),
    ))