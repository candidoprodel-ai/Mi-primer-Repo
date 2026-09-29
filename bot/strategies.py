"""Estrategias base. Cada una devuelve la exposición deseada (0 = afuera, 1 = comprado)
al cierre de cada vela, usando SOLO información pasada (sin mirar el futuro)."""
import numpy as np
import pandas as pd

from .indicators import ema, rsi, sma


def _latch(enter, exit_) -> np.ndarray:
    out = np.zeros(len(enter))
    pos = 0
    for i, (e, x) in enumerate(zip(enter, exit_)):
        if pos == 0 and e:
            pos = 1
        elif pos == 1 and x:
            pos = 0
        out[i] = pos
    return out


def trend(df, fast=20, slow=50):
    c = df["close"]
    return (ema(c, fast) > ema(c, slow)).astype(float)


def momentum(df, lookback=48):
    return (df["close"].pct_change(lookback) > 0).astype(float)


def breakout(df, n=48):
    c = df["close"]
    hi = df["high"].rolling(n).max().shift(1)
    lo = df["low"].rolling(max(2, n // 2)).min().shift(1)
    return pd.Series(_latch((c > hi).values, (c < lo).values), index=df.index)


def mean_reversion(df, n=20, k=2.0):
    c = df["close"]
    m = sma(c, n)
    lower = m - k * c.rolling(n).std()
    enter = (c < lower) & (rsi(c, 14) < 30)
    return pd.Series(_latch(enter.values, (c > m).values), index=df.index)


def cash(df):
    return pd.Series(0.0, index=df.index)


# nombre -> (función, grilla de parámetros para re-optimizar)
STRATEGIES = {
    "trend": (trend, [dict(fast=10, slow=30), dict(fast=20, slow=50),
                      dict(fast=50, slow=200), dict(fast=12, slow=26)]),
    "momentum": (momentum, [dict(lookback=24), dict(lookback=48),
                            dict(lookback=96), dict(lookback=168)]),
    "breakout": (breakout, [dict(n=24), dict(n=48), dict(n=96)]),
    "mean_reversion": (mean_reversion, [dict(n=20, k=2.0), dict(n=20, k=2.5), dict(n=50, k=2.0)]),
    "cash": (cash, [{}]),
}


def default_params():
    return {name: dict(grid[0]) if name != "trend" else dict(grid[1])
            for name, (_, grid) in STRATEGIES.items()}


def compute_signals(df, params) -> pd.DataFrame:
    return pd.DataFrame({name: fn(df, **params.get(name, {}))
                         for name, (fn, _) in STRATEGIES.items()}, index=df.index).fillna(0.0)
