import json

import numpy as np
import pandas as pd

from bot import data
from bot.adaptive import Ensemble
from bot.broker import PaperBroker
from bot.config import Config
from bot.engine import Engine
from bot.strategies import compute_signals, default_params


def test_signals_do_not_look_ahead():
    df = data.synthetic(1500, seed=1)
    full = compute_signals(df, default_params())
    cut = 1000
    tampered = df.copy()
    tampered.iloc[cut:] *= 1.5  # cambiar el "futuro" no debe cambiar señales pasadas
    part = compute_signals(tampered, default_params())
    pd.testing.assert_frame_equal(full.iloc[:cut], part.iloc[:cut])


def test_paper_broker_accounting():
    b = PaperBroker(1000, fee=0.001, slippage=0.0)
    b.set_target_units(1.0, 100)
    assert np.isclose(b.cash, 1000 - 100 - 0.1)
    b.set_target_units(0.0, 110)
    assert b.units == 0 and np.isclose(b.cash, 1000 - 100.1 + 110 - 0.11)


def test_broker_never_overspends():
    b = PaperBroker(100, fee=0.001, slippage=0.001)
    b.set_target_units(10, 100)
    assert b.cash >= -1e-9


def test_ensemble_learns_winner():
    e = Ensemble(eta=0.1, share=0.01)
    for _ in range(200):
        e.update({"trend": 1.0, "momentum": -1.0})
    assert e.weights["trend"] > 0.5 and e.weights["momentum"] < e.weights["cash"]


def test_resume_from_state_matches_single_run():
    df = data.synthetic(3000, seed=2)
    cfg = Config()
    one = Engine(cfg, PaperBroker(cfg.initial_cash, cfg.fee, cfg.slippage))
    one.process(df)

    a = Engine(cfg, PaperBroker(cfg.initial_cash, cfg.fee, cfg.slippage))
    a.process(df.iloc[:1800])
    st = json.loads(json.dumps(a.state()))  # simula guardar y recargar
    b = Engine(cfg, PaperBroker(st["broker"]["cash"], cfg.fee, cfg.slippage,
                                st["broker"]["units"]), st)
    b.process(df)
    price = df["close"].iloc[-1]
    assert np.isclose(one.broker.equity(price), b.broker.equity(price))
    assert len(one.trades) == len(b.trades)


def test_drawdown_brake_goes_flat():
    df = data.synthetic(1500, seed=3)
    crash = df.copy()
    crash.iloc[800:, :4] = crash.iloc[800:, :4].values * np.linspace(1, 0.2, 700)[:, None]
    cfg = Config(max_drawdown=0.1)
    eng = Engine(cfg, PaperBroker(1000, cfg.fee, cfg.slippage))
    eng.process(crash)
    price = crash["close"].iloc[-1]
    assert eng.broker.units == 0
    assert eng.broker.equity(price) > 1000 * 0.2


def test_event_timestamps_are_real_dates():
    df = data.synthetic(1500, seed=4)
    eng = Engine(Config(), PaperBroker(1000, 0.001, 0.0005))
    eng.process(df)
    assert eng.last_ts == int(df.index[-1].timestamp() * 1000)
