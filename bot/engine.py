"""Motor principal. El MISMO código corre en backtest, paper y live, así lo que
probás en el backtest es exactamente lo que se ejecuta después."""
import math

import numpy as np
import pandas as pd

from .adaptive import Ensemble, optimize
from .config import Config
from .indicators import atr
from .strategies import STRATEGIES, compute_signals, default_params


class Engine:
    def __init__(self, cfg: Config, broker, state: dict | None = None):
        s = state or {}
        self.cfg, self.broker = cfg, broker
        self.ensemble = Ensemble(cfg.eta, cfg.share, s.get("weights"))
        self.params = s.get("params") or default_params()
        self.last_ts = s.get("last_ts")          # ms de la última vela procesada
        self.stop = s.get("stop")
        self.cooldown = s.get("cooldown", 0)
        self.peak = s.get("peak", 0.0)
        self.halted = s.get("halted", False)
        self.bars_since_reopt = s.get("bars_since_reopt", 0)
        self.avg_cost = s.get("avg_cost", 0.0)
        self.trade_pnl = s.get("trade_pnl", 0.0)
        self.trade_open_ts = s.get("trade_open_ts")
        self.trades = s.get("trades", [])
        self.equity_curve: list[tuple] = []
        self.events: list[str] = []

    # ---------- persistencia ----------
    def state(self) -> dict:
        return {"weights": self.ensemble.weights, "params": self.params,
                "last_ts": self.last_ts, "stop": self.stop, "cooldown": self.cooldown,
                "peak": self.peak, "halted": self.halted,
                "bars_since_reopt": self.bars_since_reopt, "avg_cost": self.avg_cost,
                "trade_pnl": self.trade_pnl, "trade_open_ts": self.trade_open_ts,
                "trades": self.trades[-500:], "broker": self.broker.to_dict()}

    # ---------- loop ----------
    def process(self, df: pd.DataFrame):
        """Procesa todas las velas de df posteriores a last_ts."""
        cfg = self.cfg
        ts_ms = np.asarray(df.index.as_unit("ms").asi8)
        start = 0 if self.last_ts is None else int(np.searchsorted(ts_ms, self.last_ts, "right"))
        start = max(start, cfg.warmup, 2)
        if start >= len(df):
            return
        sig = compute_signals(df, self.params)
        rets = df["close"].pct_change()
        vol = rets.rolling(cfg.vol_window).std()
        atr_s = atr(df)
        o, h, l, c = (df[k].values for k in ("open", "high", "low", "close"))
        cost = cfg.fee + cfg.slippage
        names = list(STRATEGIES)
        S = sig[names].values

        for i in range(start, len(df)):
            price, t = c[i], int(ts_ms[i])

            # 1) trailing stop
            if self.broker.units > 0 and self.stop and l[i] <= self.stop:
                self._trade(0.0, min(o[i], self.stop), t, "STOP")
                self.stop, self.cooldown = None, cfg.cooldown

            # 2) aprendizaje: premiar/castigar cada estrategia por su resultado en esta vela
            v = vol.iloc[i - 1]
            if v and not math.isnan(v):
                r = c[i] / c[i - 1] - 1
                scores = {n: (S[i - 1, j] * r - cost * abs(S[i - 1, j] - S[i - 2, j])) / v
                          for j, n in enumerate(names)}
                self.ensemble.update(scores)

            # 3) re-optimización periódica de parámetros (solo con datos pasados)
            self.bars_since_reopt += 1
            if self.bars_since_reopt >= cfg.reopt_every:
                self.bars_since_reopt = 0
                window = df.iloc[max(0, i + 1 - cfg.reopt_window): i + 1]
                new = optimize(window, self.params, cost, cfg.reopt_margin)
                if new != self.params:
                    changed = [k for k in new if new[k] != self.params.get(k)]
                    self.events.append(f"{df.index[i]} re-optimizado: {changed}")
                    self.params = new
                    sig = compute_signals(df, self.params)
                    S = sig[names].values

            # 4) freno de emergencia por drawdown
            eq = self.broker.equity(price)
            self.peak = max(self.peak, eq)
            if not self.halted and self.peak > 0 and eq < self.peak * (1 - cfg.max_drawdown):
                self.halted = True
                self.events.append(f"{df.index[i]} FRENO: drawdown > {cfg.max_drawdown:.0%}")
                if self.broker.units > 0:
                    self._trade(0.0, price, t, "HALT")

            if not self.halted:
                # 5) exposición objetivo = consenso del ensamble × ajuste por volatilidad
                combined = float(np.dot([self.ensemble.weights[n] for n in names], S[i]))
                ann_vol = (v or 0) * math.sqrt(cfg.bars_per_year)
                scale = min(1.0, cfg.target_vol / ann_vol) if ann_vol > 0 else 0.0
                target = combined * scale if combined >= cfg.min_signal else 0.0
                if self.cooldown > 0:
                    self.cooldown -= 1
                    target = 0.0
                eq = self.broker.equity(price)
                current = self.broker.units * price / eq if eq > 0 else 0.0
                if abs(target - current) > cfg.rebalance_band or (target == 0 and current > 0):
                    self._trade(target * eq / price, price, t, "SIGNAL")

                # 6) actualizar trailing stop
                if self.broker.units > 0:
                    new_stop = price - cfg.atr_mult * atr_s.iloc[i]
                    self.stop = max(self.stop or 0.0, new_stop)
                else:
                    self.stop = None

            self.last_ts = t
            self.equity_curve.append((df.index[i], self.broker.equity(price)))

    def _trade(self, target_units: float, price: float, t: int, reason: str):
        was_flat = self.broker.units <= 0
        fill = self.broker.set_target_units(target_units, price)
        if not fill:
            return
        q, p, fee = fill["qty"], fill["price"], fill["fee"]
        if q > 0:
            held = self.broker.units - q
            self.avg_cost = (self.avg_cost * held + p * q) / self.broker.units
            self.trade_pnl -= fee
            if was_flat:
                self.trade_open_ts = t
        else:
            self.trade_pnl += (p - self.avg_cost) * (-q) - fee
        self.events.append(f"{pd.Timestamp(t, unit='ms', tz='UTC')} {reason} "
                           f"{'COMPRA' if q > 0 else 'VENTA'} {abs(q):.6f} @ {p:.2f}")
        if self.broker.units <= 0:
            self.trades.append({"open": self.trade_open_ts, "close": t,
                                "pnl": round(self.trade_pnl, 4), "reason": reason})
            self.trade_pnl, self.avg_cost, self.trade_open_ts = 0.0, 0.0, None
