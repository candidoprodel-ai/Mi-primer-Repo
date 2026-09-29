import math

import pandas as pd


def summary(equity: pd.Series, trades: list, bars_per_year: int, close: pd.Series) -> dict:
    r = equity.pct_change().dropna()
    years = max(len(equity) / bars_per_year, 1e-9)
    total = equity.iloc[-1] / equity.iloc[0] - 1
    dd = (equity / equity.cummax() - 1).min()
    bh = close.loc[equity.index]
    bh_total = bh.iloc[-1] / bh.iloc[0] - 1
    bh_dd = (bh / bh.cummax() - 1).min()
    wins = [t for t in trades if t["pnl"] > 0]
    gross_win = sum(t["pnl"] for t in wins)
    gross_loss = -sum(t["pnl"] for t in trades if t["pnl"] <= 0)
    return {
        "Retorno total": f"{total:+.1%}",
        "Retorno anual (CAGR)": f"{(1 + total) ** (1 / years) - 1:+.1%}",
        "Sharpe": f"{r.mean() / r.std() * math.sqrt(bars_per_year):.2f}" if r.std() else "n/a",
        "Máx. drawdown": f"{dd:.1%}",
        "Operaciones": len(trades),
        "% ganadoras": f"{len(wins) / len(trades):.0%}" if trades else "n/a",
        "Profit factor": f"{gross_win / gross_loss:.2f}" if gross_loss else "n/a",
        "Buy & hold (retorno)": f"{bh_total:+.1%}",
        "Buy & hold (máx. DD)": f"{bh_dd:.1%}",
    }
