"""Genera el panel visual (HTML autocontenido) con precio, operaciones, capital vs
buy & hold, estrategias activas, cambios de estrategia y resultados año por año."""
import json
import math
from pathlib import Path

import pandas as pd

from .adaptive import sharpe

TEMPLATE = Path(__file__).with_name("dashboard_template.html")


def _series(s: pd.Series, max_points: int = 6000):
    if len(s) > max_points:  # muchas velas: resumir a diario para que el panel sea liviano
        s = s.resample("1D").last().dropna()
    return [[int(t.timestamp()), round(float(v), 2)] for t, v in s.items()]


def build(eng, df: pd.DataFrame, mode: str, initial: float) -> dict:
    cfg = eng.cfg
    eq = eng.equity_series()
    if eq.empty:
        eq = pd.Series([initial], index=[df.index[-1]])
    close = df["close"]
    px = close.loc[eq.index[0]:]
    hold = initial * px / px.iloc[0]
    bpy = cfg.bars_per_year

    def kpis(e):
        r = e.pct_change().dropna()
        return {"ret": float(e.iloc[-1] / e.iloc[0] - 1),
                "dd": float((e / e.cummax() - 1).min()),
                "sharpe": round(sharpe(r, bpy), 2)}

    wins = [t for t in eng.trades if t["pnl"] > 0]
    years = []
    for y in sorted(set(eq.index.year)):
        e, h = eq[eq.index.year == y], hold[hold.index.year == y]
        prev_e = eq[eq.index < e.index[0]]
        prev_h = hold[hold.index < h.index[0]]
        e0 = prev_e.iloc[-1] if len(prev_e) else e.iloc[0]
        h0 = prev_h.iloc[-1] if len(prev_h) else h.iloc[0]
        years.append({"year": int(y), "bot": float(e.iloc[-1] / e0 - 1), "hold": float(h.iloc[-1] / h0 - 1)})

    price_start = eq.index[0] - pd.Timedelta(days=30)
    ts = lambda ms: int(ms // 1000)
    return {
        "meta": {"symbol": cfg.symbol, "interval": cfg.interval, "mode": mode,
                 "updated": pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d %H:%M UTC"),
                 "from": str(eq.index[0].date()), "to": str(eq.index[-1].date()),
                 "initial": initial, "halted": bool(eng.halted),
                 "fee": cfg.fee, "slippage": cfg.slippage, "top": cfg.select_top,
                 "window": cfg.select_window_days, "every": cfg.select_every_days},
        "now": {"equity": float(eq.iloc[-1]), "price": float(close.iloc[-1]),
                "units": float(eng.broker.units), "cash": float(eng.broker.cash),
                "exposure": float(eng.broker.units * close.iloc[-1] / eq.iloc[-1]) if eq.iloc[-1] else 0.0},
        "bot": kpis(eq), "hold": kpis(hold),
        "trades_n": len(eng.trades),
        "winrate": len(wins) / len(eng.trades) if eng.trades else None,
        "price": _series(close.loc[price_start:]),
        "equity": _series(eq), "holdEq": _series(hold),
        "fills": [{"t": ts(f["ts"]), "side": f["lado"], "price": f["precio"],
                   "reason": f["motivo"], "strategy": f["estrategia"]} for f in eng.fills[-1500:]],
        "trades": [{**t, "open": ts(t["open"]), "close": ts(t["close"])} for t in eng.trades[-60:]][::-1],
        "switches": [{**s, "ts": ts(s["ts"])} for s in eng.switches[-40:]][::-1],
        "active": list(eng.selected),
        "ranking": eng.ranking[:14],
        "years": years,
    }


def _clean(obj):
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean(v) for v in obj]
    return obj


def write(eng, df, path: str, mode: str, initial: float):
    payload = json.dumps(_clean(build(eng, df, mode, initial)), ensure_ascii=False, default=float)
    html = TEMPLATE.read_text(encoding="utf-8").replace("__DATA__", payload.replace("</", "<\\/"))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(html, encoding="utf-8")
    return path
