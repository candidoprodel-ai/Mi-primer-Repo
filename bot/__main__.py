"""Uso:
  python -m bot backtest [--interval 4h] [--online | --csv f.csv | --synthetic] [--dashboard panel.html]
  python -m bot paper    [--state paper/state.json --dashboard paper/index.html] [--loop]
  python -m bot live     --exchange binance --confirm   (keys en BOT_API_KEY / BOT_API_SECRET)
  python -m bot status   [--state paper/state.json]
  python -m bot reset-halt [--state paper/state.json]
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

import pandas as pd

from . import data, report
from .broker import LiveBroker, PaperBroker
from .config import Config
from .engine import Engine
from .metrics import summary

# Qué se descarta del estado pre-entrenado al empezar a operar con plata (simulada o real)
FRESH_KEYS = ("broker", "trades", "fills", "equity", "switches", "peak", "stop", "cooldown",
              "halted", "avg_cost", "trade_pnl", "trade_open")


def load_df(a, cfg):
    if a.csv:
        return data.load_csv(a.csv)
    if a.synthetic:
        return data.synthetic(a.bars, seed=a.seed)
    if not a.online:
        df = data.bundled(cfg.symbol, cfg.interval)
        if df is not None:
            return df.iloc[-a.bars:] if a.bars else df
    return data.fetch_binance(cfg.symbol, cfg.interval, a.bars or 8000)


def cmd_backtest(a, cfg):
    df = load_df(a, cfg)
    print(f"Datos: {len(df)} velas  {df.index[0]} → {df.index[-1]}")
    eng = Engine(cfg, PaperBroker(cfg.initial_cash, cfg.fee, cfg.slippage))
    eng.process(df)
    eq = eng.equity_series()
    print("\n=== Resultado (walk-forward, sin mirar el futuro) ===")
    for k, v in summary(eq, eng.trades, cfg.bars_per_year, df["close"]).items():
        print(f"  {k:<22} {v}")
    print(f"  {'Capital final':<22} {eq.iloc[-1]:.2f} (inicial {cfg.initial_cash:.2f})")
    print("\nEstrategias activas al final:", eng.selected if cfg.mode == "torneo" else eng.ensemble.weights)
    print(f"Cambios de estrategia: {len(eng.switches)}")
    if a.dashboard:
        report.write(eng, df, a.dashboard, "backtest", cfg.initial_cash)
        print(f"Panel guardado en {a.dashboard} (abrilo con el navegador)")


def _load_state(path):
    p = Path(path)
    return json.loads(p.read_text()) if p.exists() else None


def _save_state(path, eng, cfg):
    st = eng.state()
    st["config"] = cfg.to_dict()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(path + ".tmp")
    tmp.write_text(json.dumps(st, default=str))
    tmp.replace(path)


def _run_forward(a, cfg, make_broker, mode):
    state = _load_state(a.state)
    if state and "config" in state:  # mantener la config con la que arrancó
        cfg = Config(**state["config"])
    history = a.history or str(Path(a.state).with_name(f"history_{cfg.symbol}_{cfg.interval}.csv"))
    eng = None
    while True:
        try:
            df, source = data.update_history(history, cfg.symbol, cfg.interval)
            if eng is None:
                if state is None:
                    # Primera vez: el bot "estudia" toda la historia (torneo, pesos) sin operar,
                    # y arranca a operar desde la última vela con el capital intacto.
                    train = Engine(cfg, PaperBroker(cfg.initial_cash, cfg.fee, cfg.slippage))
                    train.process(df)
                    state = {k: v for k, v in train.state().items() if k not in FRESH_KEYS}
                    print(f"Estudié {len(df)} velas. Estrategias activas: {train.selected}")
                eng = Engine(cfg, make_broker(state, cfg), state)
            eng.process(df)
            for e in eng.events:
                print(e)
            eng.events.clear()
            price = df["close"].iloc[-1]
            if not eng.equity_curve:  # primer arranque: registrar el capital inicial
                eng.equity_curve.append([int(df.index[-1].timestamp() * 1000), eng.broker.equity(price)])
            print(f"[{pd.Timestamp.now(tz='UTC'):%Y-%m-%d %H:%M}] datos de {source} | última vela "
                  f"{df.index[-1]} | precio {price:.2f} | capital {eng.broker.equity(price):.2f} | "
                  f"{'FRENADO' if eng.halted else 'activo'} | activas: {eng.selected}")
            _save_state(a.state, eng, cfg)
            if a.dashboard:
                report.write(eng, df, a.dashboard, mode, cfg.initial_cash)
        except Exception as e:
            print(f"Error: {e}")
            if not a.loop:
                raise
        if not a.loop:
            break
        step = data.INTERVAL_SEC.get(cfg.interval, 3600)
        time.sleep(step - time.time() % step + 10)  # despertar justo después del cierre de vela


def cmd_paper(a, cfg):
    def mk(state, cfg):
        b = (state or {}).get("broker", {})
        return PaperBroker(b.get("cash", cfg.initial_cash), cfg.fee, cfg.slippage, b.get("units", 0.0))
    _run_forward(a, cfg, mk, "paper")


def cmd_live(a, cfg):
    if not a.confirm:
        sys.exit("Modo real requiere --confirm. Probalo antes en paper varias semanas.")
    key, sec = os.environ.get("BOT_API_KEY"), os.environ.get("BOT_API_SECRET")
    if not key or not sec:
        sys.exit("Faltan BOT_API_KEY / BOT_API_SECRET en el entorno.")
    _run_forward(a, cfg, lambda s, c: LiveBroker(a.exchange, c.symbol, key, sec, c.fee), "live")


def cmd_status(a, cfg):
    st = _load_state(a.state)
    if not st:
        sys.exit("No hay estado todavía.")
    trades = st["trades"]
    print(json.dumps({"broker": st["broker"], "frenado": bool(st["halted"]),
                      "operaciones": len(trades),
                      "pnl_realizado": round(sum(t["pnl"] for t in trades), 2),
                      "estrategias_activas": st.get("selected"),
                      "ultimos_cambios": st.get("switches", [])[-3:]}, indent=2, ensure_ascii=False))


def cmd_reset_halt(a, cfg):
    st = _load_state(a.state)
    st["halted"], st["peak"] = 0, 0.0
    Path(a.state).write_text(json.dumps(st))
    print("Freno reseteado.")


def main(argv=None):
    p = argparse.ArgumentParser(prog="bot")
    p.add_argument("command", choices=["backtest", "paper", "live", "status", "reset-halt"])
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--interval", default="4h", choices=list(data.INTERVAL_SEC))
    p.add_argument("--mode", default="torneo", choices=["torneo", "ensamble"])
    p.add_argument("--bars", type=int, default=0, help="últimas N velas (0 = todas)")
    p.add_argument("--online", action="store_true", help="bajar datos de Binance en vez de usar los del repo")
    p.add_argument("--csv")
    p.add_argument("--synthetic", action="store_true")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--cash", type=float, default=1000.0)
    p.add_argument("--dashboard", default=None)
    p.add_argument("--state", default="paper/state.json")
    p.add_argument("--history", default=None)
    p.add_argument("--loop", action="store_true")
    p.add_argument("--exchange", default="binance")
    p.add_argument("--confirm", action="store_true")
    a = p.parse_args(argv)
    if a.synthetic and not a.bars:
        a.bars = 5000
    if a.dashboard is None:
        a.dashboard = "panel_backtest.html" if a.command == "backtest" else str(Path(a.state).with_name("index.html"))
    cfg = Config(symbol=a.symbol, interval=a.interval, initial_cash=a.cash, mode=a.mode)
    {"backtest": cmd_backtest, "paper": cmd_paper, "live": cmd_live,
     "status": cmd_status, "reset-halt": cmd_reset_halt}[a.command](a, cfg)


if __name__ == "__main__":
    main()
