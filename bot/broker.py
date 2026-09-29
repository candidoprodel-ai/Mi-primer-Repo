"""Brokers: PaperBroker (simulado, por defecto) y LiveBroker (dinero real vía ccxt)."""


class PaperBroker:
    def __init__(self, cash: float, fee: float, slippage: float, units: float = 0.0):
        self.cash, self.units = cash, units
        self.fee, self.slippage = fee, slippage

    def equity(self, price: float) -> float:
        return self.cash + self.units * price

    def set_target_units(self, target: float, price: float) -> dict | None:
        delta = target - self.units
        if abs(delta) * price < 1e-9:
            return None
        fill = price * (1 + self.slippage) if delta > 0 else price * (1 - self.slippage)
        if delta > 0:  # nunca gastar más de lo que hay
            delta = min(delta, self.cash / (fill * (1 + self.fee)))
        fee = abs(delta) * fill * self.fee
        self.cash -= delta * fill + fee
        self.units += delta
        if abs(self.units) < 1e-12:
            self.units = 0.0
        return {"qty": delta, "price": fill, "fee": fee}

    def to_dict(self):
        return {"cash": self.cash, "units": self.units}


class LiveBroker:
    """Opera de verdad en un exchange. Requiere `pip install ccxt` y API keys.
    Usá keys SIN permiso de retiro. Probalo antes en paper durante semanas."""

    def __init__(self, exchange: str, symbol: str, api_key: str, secret: str, fee: float):
        import ccxt

        self.ex = getattr(ccxt, exchange)({"apiKey": api_key, "secret": secret,
                                           "enableRateLimit": True})
        self.ex.load_markets()
        self.market = self.ex.market(self._ccxt_symbol(symbol))
        self.symbol = self.market["symbol"]
        self.base, self.quote = self.market["base"], self.market["quote"]
        self.fee, self.slippage = fee, 0.0
        self._sync()

    def _ccxt_symbol(self, s):
        if "/" in s:
            return s
        for q in ("USDT", "USDC", "BUSD", "BTC", "ETH", "EUR", "ARS"):
            if s.endswith(q):
                return f"{s[:-len(q)]}/{q}"
        raise ValueError(f"No reconozco el símbolo {s}")

    def _sync(self):
        bal = self.ex.fetch_balance()
        self.cash = float(bal["free"].get(self.quote, 0) or 0)
        self.units = float(bal["total"].get(self.base, 0) or 0)

    def equity(self, price: float) -> float:
        self._sync()
        return self.cash + self.units * price

    def set_target_units(self, target: float, price: float) -> dict | None:
        self._sync()
        delta = target - self.units
        if delta > 0:
            delta = min(delta, self.cash * 0.995 / price)
        min_cost = (self.market.get("limits", {}).get("cost", {}) or {}).get("min") or 10
        if abs(delta) * price < min_cost:
            return None
        amount = float(self.ex.amount_to_precision(self.symbol, abs(delta)))
        side = "buy" if delta > 0 else "sell"
        order = self.ex.create_order(self.symbol, "market", side, amount)
        fill = float(order.get("average") or price)
        fee = float((order.get("fee") or {}).get("cost") or amount * fill * self.fee)
        self._sync()
        return {"qty": amount if delta > 0 else -amount, "price": fill, "fee": fee}

    def to_dict(self):
        return {"cash": self.cash, "units": self.units}
