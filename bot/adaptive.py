"""El 'cerebro' que aprende: un ensamble de estrategias con pesos que se actualizan
después de cada vela según cuánto ganó o perdió cada estrategia (algoritmo Hedge con
fixed-share). Las que vienen funcionando ganan peso; las que pierden, lo pierden.
El 'olvido' (share) evita que una estrategia quede muerta para siempre si el mercado
cambia de régimen. Incluye 'cash': si todo pierde, el bot aprende a quedarse afuera."""
import math

import pandas as pd

from .strategies import STRATEGIES


class Ensemble:
    def __init__(self, eta: float, share: float, weights: dict | None = None):
        self.eta = eta
        self.share = share
        names = list(STRATEGIES)
        self.weights = {n: 1 / len(names) for n in names}
        if weights:
            self.weights.update({k: v for k, v in weights.items() if k in self.weights})
            self._normalize()

    def _normalize(self):
        total = sum(self.weights.values())
        for k in self.weights:
            self.weights[k] /= total

    def update(self, scores: dict):
        """scores: retorno de cada estrategia en la última vela, normalizado por volatilidad."""
        for k, s in scores.items():
            # clip para que una vela extrema no destruya todo lo aprendido
            self.weights[k] *= math.exp(self.eta * max(-5.0, min(5.0, s)))
        self._normalize()
        n = len(self.weights)
        for k in self.weights:
            self.weights[k] = (1 - self.share) * self.weights[k] + self.share / n

    def combine(self, signals: dict) -> float:
        return sum(self.weights[k] * signals.get(k, 0.0) for k in self.weights)


def strategy_returns(sig: pd.Series, close: pd.Series, cost: float) -> pd.Series:
    pos = sig.shift(1).fillna(0)
    return pos * close.pct_change().fillna(0) - cost * sig.diff().abs().fillna(0)


def tstat(r: pd.Series) -> float:
    sd = r.std()
    if not sd or len(r) < 2 or math.isnan(sd):
        return 0.0
    return float(r.mean() / sd * math.sqrt(len(r)))


def optimize(df: pd.DataFrame, current: dict, cost: float, margin: float) -> dict:
    """Walk-forward: elige, para cada estrategia, los parámetros que mejor funcionaron en
    la historia reciente. Solo cambia si la mejora es clara (evita sobreajuste y vaivenes)."""
    new = {}
    close = df["close"]
    for name, (fn, grid) in STRATEGIES.items():
        cur = current.get(name, grid[0])
        if len(grid) <= 1:
            new[name] = cur
            continue
        cur_score = tstat(strategy_returns(fn(df, **cur), close, cost))
        best, best_score = cur, cur_score
        for p in grid:
            score = tstat(strategy_returns(fn(df, **p), close, cost))
            if score > best_score:
                best, best_score = p, score
        new[name] = dict(best) if best_score > cur_score + margin else cur
    return new
