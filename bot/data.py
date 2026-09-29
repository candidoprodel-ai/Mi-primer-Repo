"""Datos de mercado: Binance (API pública, sin key), CSV o sintéticos para pruebas."""
import time

import numpy as np
import pandas as pd
import requests

BINANCE_HOSTS = ["https://data-api.binance.vision", "https://api.binance.com"]
COLS = ["open", "high", "low", "close", "volume"]


def fetch_binance(symbol: str, interval: str, bars: int = 1000) -> pd.DataFrame:
    """Descarga las últimas `bars` velas CERRADAS (pagina de a 1000)."""
    rows, end = [], None
    while len(rows) < bars:
        params = {"symbol": symbol, "interval": interval, "limit": min(1000, bars - len(rows))}
        if end is not None:
            params["endTime"] = end
        batch = _get("/api/v3/klines", params)
        if not batch:
            break
        rows = batch + rows
        end = batch[0][0] - 1
        if len(batch) < params["limit"]:
            break
    now_ms = int(time.time() * 1000)
    rows = [r for r in rows if r[6] < now_ms]  # descartar la vela que sigue abierta
    df = pd.DataFrame([[r[0], *map(float, r[1:6])] for r in rows], columns=["ts", *COLS])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df.drop_duplicates("ts").set_index("ts").sort_index()


def _get(path, params):
    last = None
    for host in BINANCE_HOSTS:
        for attempt in range(3):
            try:
                r = requests.get(host + path, params=params, timeout=15)
                r.raise_for_status()
                return r.json()
            except Exception as e:  # red caída, rate limit, etc.
                last = e
                time.sleep(2 ** attempt)
    raise ConnectionError(f"No pude obtener datos de Binance: {last}")


def load_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df.columns = [c.lower() for c in df.columns]
    tcol = next(c for c in ("ts", "timestamp", "date", "time", "open_time") if c in df.columns)
    ts = df[tcol]
    df["ts"] = pd.to_datetime(ts, unit="ms", utc=True) if np.issubdtype(ts.dtype, np.number) \
        else pd.to_datetime(ts, utc=True)
    if "volume" not in df.columns:
        df["volume"] = 0.0
    return df.set_index("ts")[COLS].astype(float).sort_index()


def synthetic(bars: int = 5000, seed: int = 0, start: float = 30000.0) -> pd.DataFrame:
    """Mercado falso con regímenes (alcista, bajista, lateral) para testear el bot."""
    rng = np.random.default_rng(seed)
    rets = []
    while len(rets) < bars:
        n = int(rng.integers(200, 900))
        regime = rng.choice(["up", "down", "range"], p=[0.4, 0.3, 0.3])
        vol = rng.choice([0.005, 0.008, 0.012])
        if regime == "range":
            x, seg = 0.0, []
            for _ in range(n):  # proceso que vuelve a la media
                r = -0.05 * x + rng.normal(0, vol)
                x += r
                seg.append(r)
            rets += seg
        else:
            drift = 0.0006 if regime == "up" else -0.0005
            rets += list(rng.normal(drift, vol, n))
    rets = np.array(rets[:bars])
    close = start * np.exp(np.cumsum(rets))
    open_ = np.concatenate([[start], close[:-1]])
    wig = np.abs(rng.normal(0, 0.004, bars))
    high = np.maximum(open_, close) * (1 + wig)
    low = np.minimum(open_, close) * (1 - wig)
    idx = pd.date_range("2022-01-01", periods=bars, freq="h", tz="UTC")
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close,
                         "volume": rng.uniform(100, 1000, bars)}, index=idx)
