"""Motor principal. El MISMO código corre en backtest, paper y live, así lo que
probás en el backtest es exactamente lo que se ejecuta después."""
import math

import numpy as np
import pandas as pd

from .adaptive import Ensemble, optimize, sharpe
from .config import Config
from .indicators import atr
from .strategies import ENSEMBLE, compute_signals, compute_variant_signals, default_params


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
        self.halted = s.get("halted", 0)         # velas que faltan de freno (0 = activo)
        self.bars_since_reopt = s.get("bars_since_reopt", 0)
        self.bars_since_select = s.get("bars_since_select", 10**9)
        self.selected = s.get("selected") or []  # estrategias activas (modo torneo)
        if isinstance(self.selected, str):
            self.selected = [self.selected]
        self.ranking = s.get("ranking", [])      # último ranking del torneo
        self.switches = s.get("switches", [])    # historial de cambios de estrategia
        self.avg_cost = s.get("avg_cost", 0.0)
        self.trade_pnl = s.get("trade_pnl", 0.0)
        self.trade_open = s.get("trade_open")
        self.trades = s.get("trades", [])
        self.fills = s.get("fills", [])
        self.equity_curve: list = s.get("equity", [])  # [[ts_ms, capital], ...]
        self.events: list[str] = []

    # ---------- persistencia ----------
    def state(self) -> dict:
        return {"weights": self.ensemble.weights, "params": self.params,
                "last_ts": self.last_ts, "stop": self.stop, "cooldown": self.cooldown,
                "peak": self.peak, "halted": self.halted,
                "bars_since_reopt": self.bars_since_reopt,
                "bars_since_select": self.bars_since_select, "selected": self.selected,
                "ranking": self.ranking, "switches": self.switches[-300:],
                "avg_cost": self.avg_cost, "trade_pnl": self.trade_pnl,
                "trade_open": self.trade_open, "trades": self.trades[-1000:],
                "fills": self.fills[-2000:], "equity": self.equity_curve[-20000:], "broker": self.broker.to_dict()}

    # ---------- torneo ----------
    def _select(self, R: pd.DataFrame, i: int, ts: int, date):
        """Backtest móvil de TODAS las variantes en la ventana reciente. Se queda con las
        `select_top` mejores (Sharpe > 0). Una estrategia activa solo se reemplaza si dejó de
        funcionar o si hay otra claramente mejor (margen), para no cambiar por ruido."""
        cfg = self.cfg
        w = R.iloc[max(0, i + 1 - cfg.days(cfg.select_window_days)): i + 1]
        scores = {k: sharpe(w[k], cfg.bars_per_year) for k in R.columns}
        rets = (1 + w).prod() - 1
        self.ranking = sorted(({"estrategia": k, "sharpe": round(v, 2),
                                "retorno": round(float(rets[k]), 4)} for k, v in scores.items()),
                              key=lambda x: -x["sharpe"])
        ranked = [r["estrategia"] for r in self.ranking if r["sharpe"] > 0 and r["estrategia"] != "cash"]
        keep, new = [], list(self.selected)
        cutoff = scores[ranked[cfg.select_top - 1]] if len(ranked) >= cfg.select_top else 0.0
        for k in self.selected:
            if k in scores and scores[k] > 0 and scores[k] >= cutoff - cfg.select_margin:
                keep.append(k)
        for k in ranked:
            if len(keep) >= cfg.select_top:
                break
            if k not in keep:
                keep.append(k)
        if set(keep) == set(new):
            return
        out, inn = [k for k in new if k not in keep], [k for k in keep if k not in new]
        reason = "inicio" if not new else "; ".join(
            [f"sale {k} (Sharpe {scores.get(k, 0):.2f})" for k in out] +
            [f"entra {k} (Sharpe {scores[k]:.2f})" for k in inn])
        if not keep:
            reason += " → ninguna funciona, queda en efectivo"
        self.selected = keep
        self.switches.append({"ts": ts, "sale": out, "entra": inn, "activas": keep, "motivo": reason})
        self.events.append(f"{date} CAMBIO DE ESTRATEGIA: {reason}")

    # ---------- loop ----------
    def process(self, df: pd.DataFrame):
        """Procesa todas las velas de df posteriores a last_ts."""
        cfg = self.cfg
        ts_ms = np.asarray(df.index.as_unit("ms").asi8)
        start = 0 if self.last_ts is None else int(np.searchsorted(ts_ms, self.last_ts, "right"))
        start = max(start, cfg.warmup, 2)
        if start >= len(df):
            return
        cost = cfg.fee + cfg.slippage
        rets = df["close"].pct_change()
        vol = rets.rolling(cfg.vol_window).std()
        atr_s = atr(df).values
        o, l, c = (df[k].values for k in ("open", "low", "close"))

        sig = compute_signals(df, self.params)
        S = sig[ENSEMBLE].values
        if cfg.mode == "torneo":
            V = compute_variant_signals(df)
            R = V.shift(1).fillna(0) * rets.fillna(0).values[:, None] - cost * V.diff().abs().fillna(0)
            cols = {k: j for j, k in enumerate(V.columns)}
            Vv = V.values

        for i in range(start, len(df)):
            price, t, date = c[i], int(ts_ms[i]), df.index[i]

            # 1) trailing stop
            if cfg.use_stop and self.broker.units > 0 and self.stop and l[i] <= self.stop:
                self._trade(0.0, min(o[i], self.stop), t, "STOP")
                self.stop, self.cooldown = None, cfg.cooldown

            v = vol.iloc[i - 1]
            if cfg.mode == "torneo":
                # 2a) cada semana: backtest de todas las estrategias y elegir la mejor
                self.bars_since_select += 1
                if self.bars_since_select >= cfg.days(cfg.select_every_days):
                    self.bars_since_select = 0
                    self._select(R, i, t, date)
                act = [cols[k] for k in self.selected if k in cols]
                signal = float(Vv[i, act].sum()) / cfg.select_top if act else 0.0
            else:
                # 2b) ensamble: premiar/castigar cada estrategia por su resultado en esta vela
                if v and not math.isnan(v):
                    r = c[i] / c[i - 1] - 1
                    self.ensemble.update({n: (S[i - 1, j] * r - cost * abs(S[i - 1, j] - S[i - 2, j])) / v
                                          for j, n in enumerate(ENSEMBLE)})
                self.bars_since_reopt += 1
                if self.bars_since_reopt >= cfg.reopt_every:
                    self.bars_since_reopt = 0
                    window = df.iloc[max(0, i + 1 - cfg.reopt_window): i + 1]
                    new = optimize(window, self.params, cost, cfg.reopt_margin)
                    if new != self.params:
                        self.events.append(f"{date} re-optimizado: {[k for k in new if new[k] != self.params.get(k)]}")
                        self.params = new
                        S = compute_signals(df, self.params)[ENSEMBLE].values
                combined = float(np.dot([self.ensemble.weights[n] for n in ENSEMBLE], S[i]))
                signal = combined if combined >= cfg.min_signal else 0.0

            # 3) freno de emergencia por drawdown (temporal)
            eq = self.broker.equity(price)
            self.peak = max(self.peak, eq)
            if not self.halted and self.peak > 0 and eq < self.peak * (1 - cfg.max_drawdown):
                self.halted = cfg.days(cfg.halt_days)
                self.events.append(f"{date} FRENO: caída > {cfg.max_drawdown:.0%}, pausa {cfg.halt_days:g} días")
                if self.broker.units > 0:
                    self._trade(0.0, price, t, "FRENO")
                self.stop = None

            if self.halted:
                self.halted -= 1
                if not self.halted:
                    self.peak = self.broker.equity(price)  # arranca de nuevo desde acá
            else:
                # 4) exposición = señal × ajuste por volatilidad
                ann_vol = (v or 0) * math.sqrt(cfg.bars_per_year)
                scale = min(1.0, cfg.target_vol / ann_vol) if ann_vol > 0 else 0.0
                target = signal * scale
                if self.cooldown > 0:
                    self.cooldown -= 1
                    target = 0.0
                eq = self.broker.equity(price)
                current = self.broker.units * price / eq if eq > 0 else 0.0
                if abs(target - current) > cfg.rebalance_band or (target == 0 and current > 0):
                    self._trade(target * eq / price, price, t, "SEÑAL")

                # 5) actualizar trailing stop
                if self.broker.units > 0:
                    self.stop = max(self.stop or 0.0, price - cfg.atr_mult * atr_s[i])
                else:
                    self.stop = None

            self.last_ts = t
            self.equity_curve.append([t, round(self.broker.equity(price), 4)])

    def equity_series(self) -> pd.Series:
        if not self.equity_curve:
            return pd.Series(dtype=float)
        ts, eq = zip(*self.equity_curve)
        return pd.Series(eq, index=pd.to_datetime(ts, unit="ms", utc=True))

    def _strategy_label(self):
        return " + ".join(self.selected) if self.cfg.mode == "torneo" else "ensamble"

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
                self.trade_open = {"ts": t, "price": p, "estrategia": self._strategy_label()}
        else:
            self.trade_pnl += (p - self.avg_cost) * (-q) - fee
        side = "COMPRA" if q > 0 else "VENTA"
        self.fills.append({"ts": t, "lado": side, "precio": round(p, 2), "cantidad": abs(q),
                           "motivo": reason, "estrategia": self._strategy_label()})
        self.events.append(f"{pd.Timestamp(t, unit='ms', tz='UTC')} {reason} {side} {abs(q):.6f} @ {p:.2f}")
        if self.broker.units <= 0:
            op = self.trade_open or {"ts": t, "price": p, "estrategia": self._strategy_label()}
            self.trades.append({"open": op["ts"], "close": t, "entrada": round(op["price"], 2),
                                "salida": round(p, 2), "pnl": round(self.trade_pnl, 4),
                                "estrategia": op["estrategia"], "motivo": reason})
            self.trade_pnl, self.avg_cost, self.trade_open = 0.0, 0.0, None
