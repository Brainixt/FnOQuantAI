# FnOQuantAI - Quantitative Trading Engine for NSE F&O Equities

FnOQuantAI is an institutional-grade algorithmic quantitative engine developed for **NSE F&O-eligible underlying equities**. It is built as an architectural counterpart to `ComoddityQuantAI`, reusing proven quantitative scoring, multi-layer risk management, monotonic data checkpoints, and autonomous background workers while tailoring universe dynamics, market schedules, holding periods, and cash equity execution.

---

## 1. Key Principles & Regulatory Compliance

- **Universe**: NSE F&O-eligible equities (~213 underlying stocks, e.g., `RELIANCE`, `TCS`, `INFY`).
- **Input vs. Execution**: Uses F&O analytics (Open Interest, Volume ratio, momentum, ATR) as analytical inputs, but executes **underlying NSE cash equity shares ONLY** (`exchange_segment="nse_cm"`, symbol `{symbol}-EQ`). Strictly **NO futures or options order execution**.
- **Share / Product Type Policy**:
  - **BUY Orders**: Dispatched with Share Type **Normal** (`NRML` / Delivery / Carry-Forward).
  - **SELL Orders**: Dispatched with Share Type **MIS** (Intraday Short).
  - **Exit Orders**: Automatically square off using the matching product type of the original position (Normal for long exits, MIS for short covers) to ensure positions net out cleanly at the broker without creating orphan mismatches.
- **Square-Off & Exit Policy**:
  - **Sell Type (MIS Short)**: **Mandatory square-off at 15:00 IST** to exit before broker RMS auto-squareoff penalties.
  - **Buy Type (Normal Long)**: **Exempt from mandatory square-off**. Positions continue running across trading sessions until either Stop-Loss or Target is hit.
- **Fixed Capital Sizing**: Configured at **Rs 10,000 per trade** (`quantity = max(1, int(CAPITAL_PER_TRADE_INR // ltp))`).
- **Deviation & Freshness Safeguards**: 
  - Max price deviation gate: **0.25%** (or 5 ticks, tick = Rs 0.05).
  - Quote freshness gate: **<= 360 seconds** max observation age.
  - Rejects inverted bid-ask spreads or zero/negative LTP.
- **Fail-Safe Mode**: `LIVE_TRADING_ENABLED = True` (or configured via environment). When set to `False`, paper simulation records virtual fills with realistic 1-tick slippage modeling.
- **Zero Mutation Isolation**: Write barrier in `database/db_manager.py` prevents any accidental writes to `market_equity_snapshots` or `ComoddityQuantAI` tables.

---

## 2. Quantitative Strategies

| Strategy | Direction | Horizon | Share Type | Stop-Loss | Target | Entry Cutoff | Exit / Expiry Rule |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Intraday (Sell)** | SELL | Same Day | MIS | 1.5 x ATR | 2.5 x ATR | 15:00 IST | Mandatory square-off at 15:00 IST |
| **Intraday (Buy)** | BUY | Multi-Session | Normal (Delivery) | 1.5 x ATR | 2.5 x ATR | 15:00 IST | No mandatory square-off; continues till SL or Target |
| **Short-Term** | BUY Only | 1-5 Days | Normal (Delivery) | 2.5 x ATR | 4.5 x ATR | 15:00 IST | Max 5 trading days; <= 2 days to F&O expiry buffer |
| **Mid-Term** | BUY Only | 5-30 Days | Normal (Delivery) | 4.0 x ATR | 8.0 x ATR | 15:00 IST | Max 30 trading days; <= 7 days to F&O expiry buffer |

### Scoring Matrix (100 Points Max)
- **Trend & Moving Average Alignment**: EMA 5 vs EMA 15 slope and directional crossover.
- **RSI Momentum**: Oversold pullbacks in uptrend for Longs, overbought in downtrend for Shorts.
- **Rate of Change (ROC)**: 5-period price velocity confirmation.
- **Volume & Open Interest Surge**: Volume ratio >= 1.25x 20-period average; positive OI expansion for institutional accumulation.
- **Risk-Reward Ratio**: Minimum 1.5:1 required, optimal >= 1.8:1.

---

## 3. System Architecture

```
                    CommonModule (PostgreSQL Read Replica / Primary)
                                           |
                               [market_equity_snapshots]
                                           |
                         +-----------------+-----------------+
                         |      ObservationValidator         | (Freshness <= 360s, LTP > 0)
                         +-----------------+-----------------+
                                           |
                         +-----------------+-----------------+
                         |         FeatureEngine             | (EMA, RSI, ATR, ROC, OI, Volume)
                         +-----------------+-----------------+
                                           |
                         +-----------------+-----------------+
                         |       QuantScoringEngine          | (Intraday, Short-Term, Mid-Term)
                         +-----------------+-----------------+
                                           |
                         +-----------------+-----------------+
                         |        SignalRepository           | (Idempotent deduplication)
                         +-----------------+-----------------+
                                           |
                         +-----------------+-----------------+
                         |        ExecutionEngine            | (Rs 10k Sizing, 0.25% Deviation Gate)
                         +--------+-----------------+--------+
                                  |                 |
                             (LIVE=False)       (LIVE=True)
                                  |                 |
                         +--------v--------+ +------v--------+
                         |   Paper Order   | |   Kotak Neo   |
                         |   Simulation    | |   nse_cm API  |
                         +-----------------+ +---------------+
```

---

## 4. Database Schema (`fo_*`)

All tables reside in PostgreSQL (`neondb`) with dedicated write protection:

1. `fo_processing_watermarks`: Monotonic snapshot checkpoint tracker ensuring fresh-data processing without duplicate scans.
2. `fo_signals`: Quant signals with entry reference price, stop loss, target, score, and horizon.
3. `fo_signal_events`: Audit trail for state transitions (`GENERATED`, `ORDER_PLACED`, `FILLED`, `REJECTED`, `EXPIRED`, `INTRADAY_SQUARE_OFF`).
4. `fo_trades`: Executed trade lifecycle, broker order IDs, actual fill prices, quantities, and realized P&L.
5. `fo_risk_events`: Audit trail for deviation rejections, stale quotes, or session rule violations.
6. `fo_worker_executions`: Execution health and cycle timing telemetry.

---

## 5. Directory Structure

```
D:\SM_Server\MarketTool\FnOQuantAI\
|-- config.py                     # Primary configuration and risk thresholds
|-- fo_config.py                  # Proxy configuration module
|-- main.py                       # Unified CLI entrypoint
|-- serve_dashboard.py            # Local HTTP analytics server (Port 8001)
|-- index.html                    # Real-time quantitative monitoring UI
|-- README.md                     # Comprehensive documentation
|-- run_daemon.bat                # Continuous background worker runner
|-- run_status.bat                # System and database health status checker
|-- run_report.bat                # Daily performance analytics report generator
|-- run_dashboard.bat             # UI Dashboard launcher
|-- database/
|   |-- db_manager.py             # Read/write connection pool & write barrier
|   `-- schema.sql                # DDL migration for fo_* tables
|-- data/
|   |-- common_db_reader.py       # Read-only query layer for market snapshots (with replica fallback)
|   |-- freshness.py              # Age, spread, and quote validation
|   `-- market_repository.py      # Watermark checkpoints & snapshot loader
|-- signals/
|   |-- feature_engine.py         # Technical & F&O derivative indicator calculations
|   |-- quant_engine.py           # Multi-horizon candidate evaluation & scoring
|   |-- strategy_intraday.py      # Same-day MIS Sell / Multi-session Normal Buy model
|   |-- strategy_short_term.py    # 1-5 day Normal Long delivery model
|   |-- strategy_mid_term.py      # 5-30 day Normal Long delivery model
|   `-- signal_repository.py      # Signal persistence & event transitions
|-- risk/
|   `-- risk_manager.py           # NSE session gates, Rs 10k sizing, deviation protection
|-- execution/
|   |-- order_dispatcher.py       # Paper simulator & Kotak Neo nse_cm dispatcher (Normal/MIS)
|   `-- execution_engine.py       # Order lifecycle coordinator & square-off matching
|-- workers/
|   |-- signal_worker.py          # Background signal generation daemon
|   `-- open_call_worker.py       # Background TP/SL/Duration & 15:00 Sell square-off daemon
|-- reporting/
|   |-- daily_summary.py          # CLI tabular summary generator
|   |-- performance_analytics.py  # P&L, Sharpe, Win Rate, and Drawdown metrics
|   `-- dashboard.py              # JSON data generator for web UI
`-- tests/
    |-- test_database_foundation.py (10 tests)
    |-- test_freshness.py           (11 tests)
    |-- test_quant_engine.py        (8 tests)
    |-- test_signal_repository.py   (1 test)
    |-- test_execution.py           (8 tests)
    |-- test_risk_manager.py        (4 tests)
    |-- test_workers.py             (6 tests)
    |-- test_reporting.py           (4 tests)
    `-- test_cli.py                 (7 tests)
```

---

## 6. How to Run

### Daily Background Daemon
Runs both `signal_worker` and `open_call_worker` in a continuous loop:
```bat
.\run_daemon.bat
```

### System Health & Status
Checks database connectivity, open trades, signal counts, and watermarks:
```bat
.\run_status.bat
```

### Daily Summary & Performance Report
Outputs tabular daily summary to terminal or exports as JSON:
```bat
.\run_report.bat
python main.py report --export json
```

### Web Dashboard
Starts local web dashboard on port **8001** (accessible at `http://localhost:8001`):
```bat
.\run_dashboard.bat
```

---

## 7. Testing & Verification

Run the entire unit and integration test suite:
```powershell
python -m unittest discover -s tests -p "test_*.py" -v
```

Status: **59 / 59 Tests Passing (100% OK)**
