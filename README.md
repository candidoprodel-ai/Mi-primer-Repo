# Bot de trading adaptativo — BTC

Bot de trading **spot, solo compras, sin apalancamiento**, que en cada momento usa las
estrategias que mejor vienen funcionando y cambia cuando dejan de funcionar.

> ⚠️ Ningún bot garantiza ganancias. Primero **paper trading** (dinero simulado con precios
> reales) durante semanas/meses. Si no le gana a comprar y mantener, no tiene sentido ponerle plata.

## Cómo funciona: el torneo
1. Hay **20 variantes** de estrategias: cruces de medias (EMA), momentum, rupturas (breakout),
   filtro de media (SMA), MACD, reversión a la media, *mantener* y *efectivo*.
2. **Cada 7 días** el bot hace un backtest de todas en el último año (con comisiones).
3. Opera con **las 5 mejores** (Sharpe > 0). Una estrategia activa sale si deja de funcionar
   (Sharpe ≤ 0) o si aparece otra claramente mejor. Si ninguna funciona, queda en efectivo.
4. Tamaño de posición según volatilidad y **freno de emergencia**: si el capital cae 25% desde
   su máximo, cierra todo y pausa 14 días.
5. Todo queda registrado: cada compra/venta, cada cambio de estrategia y por qué.

Modo alternativo: `--mode ensamble` (combina estrategias con pesos que aprenden vela a vela).

## Resultados reales (BTC 2017-2026, velas de 4h, datos de Bitstamp)
| | Bot | Comprar y mantener |
|---|---|---|
| Retorno total | +5.022% | +8.163% |
| Peor caída | **-51%** | -84% |
| Sharpe | **1.32** | 1.01 |

- 2017-2022 (período donde se eligió la configuración): el bot le gana ajustado por riesgo.
- **2023-2026 (período "examen")**: el bot ganó bastante menos que mantener (+63% vs +400%).
- Conclusión honesta: reduce mucho las caídas (2018: -14% vs -73%; 2022: -29% vs -64%) pero se
  pierde parte de las subidas. Detalle año por año en el panel.

## Uso
```bash
pip install -r requirements.txt

python -m bot backtest                  # backtest con los datos reales incluidos → panel_backtest.html
python -m bot backtest --interval 1d    # otras temporalidades: 1h, 4h, 1d
python -m bot paper --loop              # paper trading en tu compu, panel en paper/index.html
python -m bot status
```

### Paper trading 24/7 en GitHub (sin dejar la compu prendida)
El workflow `.github/workflows/paper-trading.yml` corre el bot cada 4 horas en GitHub Actions,
guarda el estado en `paper/` y actualiza el panel `paper/index.html`.
1. Mergeá esta rama a `main` (los cron de GitHub solo corren en la rama principal).
2. En GitHub → *Actions* → *Paper trading* → *Run workflow* para la primera corrida.
3. Para ver el panel desde el celu: *Settings → Pages → Deploy from branch → main / root*, y
   abrí `https://<usuario>.github.io/<repo>/paper/`.

### Dinero real (con autorización de tus padres y cuenta a nombre de un adulto)
```bash
pip install ccxt
export BOT_API_KEY=... BOT_API_SECRET=...   # keys SIN permiso de retiro
python -m bot live --confirm --loop
```

## Tests
```bash
pip install pytest && python -m pytest -q
```
Verifican que ninguna señal mire el futuro, la contabilidad, el aprendizaje, el torneo, que
guardar/retomar dé el mismo resultado, el freno de emergencia y el flujo completo de paper.
