from dataclasses import asdict, dataclass

BARS_PER_YEAR = {"15m": 35040, "30m": 17520, "1h": 8760, "4h": 2190, "1d": 365}


@dataclass
class Config:
    symbol: str = "BTCUSDT"
    interval: str = "1h"
    initial_cash: float = 1000.0
    fee: float = 0.001            # 0.1% por operación (Binance spot taker)
    slippage: float = 0.0005      # 0.05% de deslizamiento estimado
    warmup: int = 200             # velas necesarias antes de operar

    # Aprendizaje (ensamble de estrategias)
    eta: float = 0.08             # velocidad de aprendizaje de los pesos
    share: float = 0.01           # "olvido": permite adaptarse a cambios de régimen
    reopt_every: int = 168        # re-optimizar parámetros cada N velas (1 semana en 1h)
    reopt_window: int = 1500      # velas de historia usadas para re-optimizar
    reopt_margin: float = 0.5     # mejora mínima (en t-stat) para cambiar parámetros

    # Gestión de riesgo
    target_vol: float = 0.40      # volatilidad anual objetivo de la cartera
    vol_window: int = 48
    min_signal: float = 0.30      # consenso mínimo para estar comprado
    rebalance_band: float = 0.15  # no rebalancear por cambios chicos (ahorra comisiones)
    atr_mult: float = 3.0         # trailing stop a 3 ATR
    cooldown: int = 6             # velas sin operar después de un stop
    max_drawdown: float = 0.25    # si cae 25% desde el máximo, el bot se frena solo

    @property
    def bars_per_year(self) -> int:
        return BARS_PER_YEAR.get(self.interval, 8760)

    def to_dict(self):
        return asdict(self)
