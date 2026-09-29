# Bot de trading adaptativo

Bot de trading **spot, solo compras (sin apalancamiento)** que aprende a medida que pasan las velas.

> ⚠️ **Ningún bot garantiza ganancias.** Usalo primero en `paper` (dinero simulado) durante semanas.
> Si en paper no le gana a *buy & hold* después de costos, no tiene sentido ponerle plata real.

## Cómo "aprende"
1. **Ensamble de 5 estrategias** (tendencia EMA, momentum, breakout, reversión a la media y *cash*).
2. **Pesos adaptativos (Hedge)**: después de cada vela, las estrategias que ganaron suben de peso y las que perdieron bajan. Con un "olvido" para adaptarse cuando cambia el mercado. Si todas pierden, *cash* gana peso y el bot se queda afuera.
3. **Re-optimización walk-forward**: cada semana re-elige los parámetros de cada estrategia usando solo datos pasados, y solo cambia si la mejora es clara (anti-sobreajuste).
4. **El estado se guarda** (`state.json`), así lo aprendido se conserva entre ejecuciones.

## Gestión de riesgo
- Tamaño de posición según volatilidad (objetivo 40% anual).
- Trailing stop a 3 ATR + pausa de 6 velas después de un stop.
- **Freno de emergencia**: si el capital cae 25% desde su máximo, cierra todo y se detiene (`reset-halt` para reactivarlo).
- Comisiones (0.1%) y slippage incluidos en todo.

## Uso
```bash
pip install -r requirements.txt

# 1) Backtest con datos reales de Binance (sin API key)
python -m bot backtest --symbol BTCUSDT --interval 1h --bars 8000

# 2) Paper trading (una pasada, o --loop para que corra solo cada vela)
python -m bot paper --symbol BTCUSDT --loop
python -m bot status

# 3) Real (requiere ser mayor de edad en el exchange, keys SIN permiso de retiro)
pip install ccxt
export BOT_API_KEY=... BOT_API_SECRET=...
python -m bot live --symbol BTCUSDT --confirm --loop
```
Otras opciones: `--csv datos.csv`, `--synthetic`, `--cash 500`, `--out equity.csv`.

## Tests
```bash
pip install pytest && python -m pytest -q
```
Incluyen: que ninguna señal mire el futuro, contabilidad del broker, que el ensamble aprenda, que guardar/retomar el estado dé el mismo resultado y que el freno de emergencia funcione.
