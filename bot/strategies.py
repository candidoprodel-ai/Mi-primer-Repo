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


def sma_filter(df, n=200):
    c = df["close"]
    return (c > sma(c, n)).astype(float)


def macd(df, fast=12, slow=26, signal=9):
    c = df["close"]
    line = ema(c, fast) - ema(c, slow)
    return ((line > ema(line, signal)) & (line > 0)).astype(float)


def hold(df):
    return pd.Series(1.0, index=df.index)


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
    "sma_filter": (sma_filter, [dict(n=50), dict(n=100), dict(n=200)]),
    "macd": (macd, [dict(fast=12, slow=26, signal=9), dict(fast=24, slow=52, signal=18)]),
    "hold": (hold, [{}]),
    "cash": (cash, [{}]),
}

# Estrategias que participan del ensamble (hold/sma_filter/macd solo en el torneo)
ENSEMBLE = ["trend", "momentum", "breakout", "mean_reversion", "cash"]


def variant_key(name, params) -> str:
    return name + ("(" + ",".join(f"{k}={v}" for k, v in params.items()) + ")" if params else "")


def all_variants():
    """Todas las combinaciones estrategia × parámetros que compiten en el torneo."""
    return [(variant_key(n, p), n, p) for n, (_, grid) in STRATEGIES.items() for p in grid]


def compute_variant_signals(df) -> pd.DataFrame:
    return pd.DataFrame({k: STRATEGIES[n][0](df, **p) for k, n, p in all_variants()},
                        index=df.index).fillna(0.0)


def default_params():
    return {name: dict(STRATEGIES[name][1][1 if name == "trend" else 0]) for name in ENSEMBLE}


def compute_signals(df, params) -> pd.DataFrame:
    return pd.DataFrame({name: STRATEGIES[name][0](df, **params.get(name, {}))
                         for name in ENSEMBLE}, index=df.index).fillna(0.0)
