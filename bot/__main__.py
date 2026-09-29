"""Uso:
  python -m bot backtest [--symbol BTCUSDT --interval 1h --bars 8000 | --csv f.csv | --synthetic]
  python -m bot paper   [--symbol ... --state state.json --loop]
  python -m bot live    --exchange binance --confirm   (keys en BOT_API_KEY / BOT_API_SECRET)
  python -m bot status  [--state state.json]
  python -m bot reset-halt [--state state.json]
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

import pandas as pd

from . import data
from .broker import LiveBroker, PaperBroker
from .config import Config
from .engine import Engine
from .metrics import summary

INTERVAL_SEC = {"15m": 900, "30m": 1800, "1h": 3600, "4h": 14400, "1d": 86400}


def load_df(a, cfg):
    if a.csv:
        return data.load_csv(a.csv)
    if a.synthetic:
        return data.synthetic(a.bars, seed=a.seed)
    return data.fetch_binance(cfg.symbol, cfg.interval, a.bars)


def cmd_backtest(a, cfg):
    df = load_df(a, cfg)
    print(f"Datos: {len(df)} velas  {df.index[0]} → {df.index[-1]}")
    eng = Engine(cfg, PaperBroker(cfg.initial_cash, cfg.fee, cfg.slippage))
    eng.process(df)
    eq = pd.Series(dict(eng.equity_curve))
    print("\n=== Resultado (walk-forward, sin mirar el futuro) ===")
    for k, v in summary(eq, eng.trades, cfg.bars_per_year, df["close"]).items():
        print(f"  {k:<22} {v}")
    print(f"  {'Capital final':<22} {eq.iloc[-1]:.2f} (inicial {cfg.initial_cash:.2f})")
    print("\nPesos aprendidos:", {k: round(v, 3) for k, v in eng.ensemble.weights.items()})
    print("Parámetros finales:", eng.params)
    if a.out:
        eq.rename("equity").to_csv(a.out)
        print(f"Curva de capital guardada en {a.out}")


def _load_state(path):
    p = Path(path)
    return json.loads(p.read_text()) if p.exists() else None


def _save_state(path, eng, cfg):
    st = eng.state()
    st["config"] = cfg.to_dict()
    tmp = Path(path + ".tmp")
    tmp.write_text(json.dumps(st, indent=1, default=str))
    tmp.replace(path)


def _run_forward(a, cfg, make_broker):
    state = _load_state(a.state)
    if state and "config" in state:  # mantener la config con la que arrancó
        cfg = Config(**{**state["config"], "symbol": cfg.symbol, "interval": cfg.interval})
    broker = make_broker(state, cfg)
    eng = Engine(cfg, broker, state)
    fresh = state is None
    while True:
        try:
            df = data.fetch_binance(cfg.symbol, cfg.interval, max(cfg.reopt_window, 1000))
            if fresh:
                # primera vez: "entrenar" el ensamble con la historia sin operar
                # (luego arranca con capital intacto pero con pesos aprendidos)
                train = Engine(cfg, PaperBroker(cfg.initial_cash, cfg.fee, cfg.slippage))
                train.process(df)
                eng.ensemble.weights, eng.params = train.ensemble.weights, train.params
                eng.last_ts = train.last_ts
                fresh = False
                print("Ensamble pre-entrenado con la historia:",
                      {k: round(v, 3) for k, v in eng.ensemble.weights.items()})
            eng.process(df)
            for e in eng.events:
                print(e)
            eng.events.clear()
            price = df["close"].iloc[-1]
            print(f"[{pd.Timestamp.utcnow():%Y-%m-%d %H:%M}] precio {price:.2f} | capital "
                  f"{eng.broker.equity(price):.2f} | {'FRENADO' if eng.halted else 'activo'}")
            _save_state(a.state, eng, cfg)
        except Exception as e:
            print(f"Error: {e} (reintento en 60s)")
            if not a.loop:
                raise
        if not a.loop:
            break
        step = INTERVAL_SEC.get(cfg.interval, 3600)
        time.sleep(step - time.time() % step + 5)  # despertar justo después del cierre de vela


def cmd_paper(a, cfg):
    def mk(state, cfg):
        b = (state or {}).get("broker", {})
        return PaperBroker(b.get("cash", cfg.initial_cash), cfg.fee, cfg.slippage, b.get("units", 0.0))
    _run_forward(a, cfg, mk)


def cmd_live(a, cfg):
    if not a.confirm:
        sys.exit("Modo real requiere --confirm. Probalo antes en paper varias semanas.")
    key, sec = os.environ.get("BOT_API_KEY"), os.environ.get("BOT_API_SECRET")
    if not key or not sec:
        sys.exit("Faltan BOT_API_KEY / BOT_API_SECRET en el entorno.")
    _run_forward(a, cfg, lambda s, c: LiveBroker(a.exchange, c.symbol, key, sec, c.fee))


def cmd_status(a, cfg):
    st = _load_state(a.state)
    if not st:
        sys.exit("No hay estado todavía.")
    trades = st["trades"]
    pnl = sum(t["pnl"] for t in trades)
    print(json.dumps({"broker": st["broker"], "frenado": st["halted"], "operaciones": len(trades),
                      "pnl_realizado": round(pnl, 2),
                      "pesos": {k: round(v, 3) for k, v in st["weights"].items()},
                      "parametros": st["params"]}, indent=2, ensure_ascii=False))


def cmd_reset_halt(a, cfg):
    st = _load_state(a.state)
    st["halted"], st["peak"] = False, 0.0
    Path(a.state).write_text(json.dumps(st, indent=1))
    print("Freno reseteado.")


def main(argv=None):
    p = argparse.ArgumentParser(prog="bot")
    p.add_argument("command", choices=["backtest", "paper", "live", "status", "reset-halt"])
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--interval", default="1h", choices=list(INTERVAL_SEC))
    p.add_argument("--bars", type=int, default=8000)
    p.add_argument("--csv")
    p.add_argument("--synthetic", action="store_true")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--cash", type=float, default=1000.0)
    p.add_argument("--out")
    p.add_argument("--state", default="state.json")
    p.add_argument("--loop", action="store_true")
    p.add_argument("--exchange", default="binance")
    p.add_argument("--confirm", action="store_true")
    a = p.parse_args(argv)
    cfg = Config(symbol=a.symbol, interval=a.interval, initial_cash=a.cash)
    {"backtest": cmd_backtest, "paper": cmd_paper, "live": cmd_live,
     "status": cmd_status, "reset-halt": cmd_reset_halt}[a.command](a, cfg)


if __name__ == "__main__":
    main()
