"""Datos de mercado: Binance (API pública, sin key), CSV o sintéticos para pruebas."""
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

BINANCE_HOSTS = ["https://data-api.binance.vision", "https://api.binance.com"]
INTERVAL_SEC = {"15m": 900, "30m": 1800, "1h": 3600, "4h": 14400, "1d": 86400}
BUNDLED = Path(__file__).resolve().parent.parent / "data"
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


def _get(path, params, hosts=BINANCE_HOSTS, tries=3):
    last = None
    for host in hosts:
        for attempt in range(tries):
            try:
                r = requests.get(host + path, params=params, timeout=15)
                r.raise_for_status()
                return r.json()
            except Exception as e:  # red caída, rate limit, bloqueo por país, etc.
                last = e
                if attempt < tries - 1:
                    time.sleep(2 ** attempt)
    raise ConnectionError(f"No pude obtener datos de {hosts[0]}: {last}")


def _split(symbol):
    for q in ("USDT", "USDC", "USD", "EUR"):
        if symbol.endswith(q):
            return symbol[: -len(q)], q
    raise ValueError(f"No reconozco el símbolo {symbol}")


def _frame(rows, interval):
    """rows: [ts_ms, o, h, l, c, v]. Devuelve solo velas cerradas, ordenadas."""
    now_ms = time.time() * 1000
    rows = [r for r in rows if r[0] + INTERVAL_SEC[interval] * 1000 <= now_ms]
    df = pd.DataFrame([[int(r[0]), *map(float, r[1:6])] for r in rows], columns=["ts", *COLS])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df.drop_duplicates("ts").set_index("ts").sort_index()


def fetch_kraken(symbol, interval):
    base, quote = _split(symbol)
    pair = ("XBT" if base == "BTC" else base) + ("USD" if quote == "USDT" else quote)
    res = _get("/0/public/OHLC", {"pair": pair, "interval": INTERVAL_SEC[interval] // 60},
               ["https://api.kraken.com"], 2)
    if res.get("error"):
        raise ConnectionError(f"Kraken: {res['error']}")
    rows = next(v for k, v in res["result"].items() if k != "last")
    return _frame([[r[0] * 1000, r[1], r[2], r[3], r[4], r[6]] for r in rows], interval)


def fetch_okx(symbol, interval):
    base, quote = _split(symbol)
    bar = {"15m": "15m", "30m": "30m", "1h": "1H", "4h": "4H", "1d": "1Dutc"}[interval]
    res = _get("/api/v5/market/candles", {"instId": f"{base}-{quote}", "bar": bar, "limit": 300},
               ["https://www.okx.com"], 2)
    return _frame([[int(r[0]), *r[1:6]] for r in res.get("data", []) if r[-1] == "1"], interval)


def fetch_recent(symbol, interval, bars=1000):
    """Velas recientes probando varias fuentes (Binance puede estar bloqueado según el país)."""
    errors = []
    for name, fn in [("binance", lambda: fetch_binance(symbol, interval, bars)),
                     ("kraken", lambda: fetch_kraken(symbol, interval)),
                     ("okx", lambda: fetch_okx(symbol, interval))]:
        try:
            df = fn()
            if len(df):
                return df, name
        except Exception as e:
            errors.append(f"{name}: {e}")
    raise ConnectionError(" | ".join(errors))


def bundled(symbol, interval):
    """Historia real incluida en el repo (BTC, Bitstamp 2017-2026)."""
    if not symbol.startswith("BTC"):
        return None
    for f in (BUNDLED / f"btc_{interval}.csv", BUNDLED / f"btc_{interval}.csv.gz"):
        if f.exists():
            return load_csv(str(f))
    return None


def update_history(path, symbol, interval):
    """Mantiene un CSV con toda la historia: la guardada + las velas nuevas."""
    p = Path(path)
    hist = load_csv(str(p)) if p.exists() else bundled(symbol, interval)
    recent, source = fetch_recent(symbol, interval)
    df = recent if hist is None else pd.concat([hist, recent])
    df = df[~df.index.duplicated(keep="last")].sort_index()
    p.parent.mkdir(parents=True, exist_ok=True)
    df.round(6).to_csv(p, index_label="ts")
    return df, source


def load_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df.columns = [c.lower() for c in df.columns]
    tcol = next(c for c in ("ts", "timestamp", "date", "time", "open_time") if c in df.columns)
    ts = df[tcol]
    if pd.api.types.is_numeric_dtype(ts):
        unit = "s" if ts.iloc[0] < 1e11 else "ms"
        df["ts"] = pd.to_datetime(ts, unit=unit, utc=True)
    else:
        df["ts"] = pd.to_datetime(ts, utc=True)
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
