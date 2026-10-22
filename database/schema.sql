-- =============================================================================
-- FnOQuantAI Database Schema
-- Dedicated schema for NSE F&O Equity Quantitative Analysis & Trading System
-- Idempotent, safe to run multiple times (CREATE TABLE IF NOT EXISTS)
-- =============================================================================

-- 1. Processing Watermarks (Durable progress tracking for new-data-only execution)
CREATE TABLE IF NOT EXISTS fo_processing_watermarks (
    id BIGSERIAL PRIMARY KEY,
    symbol VARCHAR(50) NOT NULL,
    last_processed_cycle_id VARCHAR(64) NOT NULL,
    last_processed_snapshot_id BIGINT NOT NULL,
    last_source_timestamp TIMESTAMPTZ,
    last_retrieved_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_fo_watermark_symbol UNIQUE (symbol)
);
CREATE INDEX IF NOT EXISTS idx_fo_watermarks_symbol ON fo_processing_watermarks (symbol);

-- 2. Quantitative Signals
CREATE TABLE IF NOT EXISTS fo_signals (
    signal_id VARCHAR(64) PRIMARY KEY,
    symbol VARCHAR(50) NOT NULL,
    trading_symbol VARCHAR(100) NOT NULL,
    exchange VARCHAR(10) NOT NULL DEFAULT 'NSE',
    strategy_category VARCHAR(20) NOT NULL, -- INTRADAY, SHORT_TERM, MID_TERM
    direction VARCHAR(10) NOT NULL,          -- BUY, SELL, NO_TRADE
    status VARCHAR(30) NOT NULL DEFAULT 'NEW', -- NEW, ACTIVE, TARGET_HIT, STOP_LOSS_HIT, TIME_EXIT, INVALIDATED, CANCELLED
    entry_reference_price NUMERIC(12, 4) NOT NULL,
    stop_loss_price NUMERIC(12, 4) NOT NULL,
    target_price NUMERIC(12, 4) NOT NULL,
    risk_per_unit NUMERIC(12, 4) NOT NULL,
    reward_per_unit NUMERIC(12, 4) NOT NULL,
    reward_to_risk_ratio NUMERIC(8, 4) NOT NULL,
    risk_percentage NUMERIC(8, 4) NOT NULL,
    total_score NUMERIC(8, 4) NOT NULL,
    feature_contributions JSONB NOT NULL DEFAULT '{}'::jsonb,
    strategy_name VARCHAR(50) NOT NULL,
    strategy_version VARCHAR(20) NOT NULL,
    intended_holding_period VARCHAR(30) NOT NULL,
    source_cycle_id VARCHAR(64) NOT NULL,
    source_snapshot_id BIGINT NOT NULL,
    source_market_timestamp TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_fo_signal_symbol_strategy_cycle UNIQUE (symbol, strategy_category, source_cycle_id)
);
CREATE INDEX IF NOT EXISTS idx_fo_signals_symbol_status ON fo_signals (symbol, status);
CREATE INDEX IF NOT EXISTS idx_fo_signals_category ON fo_signals (strategy_category);
CREATE INDEX IF NOT EXISTS idx_fo_signals_created ON fo_signals (created_at);

-- 3. Signal Lifecycle Events Audit Trail
CREATE TABLE IF NOT EXISTS fo_signal_events (
    id BIGSERIAL PRIMARY KEY,
    signal_id VARCHAR(64) NOT NULL REFERENCES fo_signals(signal_id) ON DELETE CASCADE,
    event_type VARCHAR(30) NOT NULL, -- CREATED, ACTIVATED, TARGET_HIT, STOP_LOSS_HIT, TIME_EXIT, INVALIDATED, CANCELLED
    from_status VARCHAR(30),
    to_status VARCHAR(30) NOT NULL,
    trigger_price NUMERIC(12, 4),
    market_timestamp TIMESTAMPTZ,
    event_timestamp TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    event_details JSONB DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_fo_events_signal ON fo_signal_events (signal_id, event_timestamp);

-- 4. Trade Execution & Outcome Records (Simulated & Real)
CREATE TABLE IF NOT EXISTS fo_trades (
    trade_id VARCHAR(64) PRIMARY KEY,
    signal_id VARCHAR(64) NOT NULL REFERENCES fo_signals(signal_id),
    symbol VARCHAR(50) NOT NULL,
    trading_symbol VARCHAR(100) NOT NULL,
    strategy_category VARCHAR(20) NOT NULL,
    direction VARCHAR(10) NOT NULL,
    is_live_trade BOOLEAN NOT NULL DEFAULT FALSE,
    quantity INT NOT NULL DEFAULT 1,
    allocated_capital_inr NUMERIC(12, 2) NOT NULL DEFAULT 10000.00,
    entry_reference_price NUMERIC(12, 4) NOT NULL,
    exit_reference_price NUMERIC(12, 4),
    actual_entry_fill_price NUMERIC(12, 4),
    actual_exit_fill_price NUMERIC(12, 4),
    gross_pnl NUMERIC(14, 4),
    gross_pnl_percentage NUMERIC(8, 4),
    estimated_transaction_costs NUMERIC(12, 4) DEFAULT 0.0000,
    net_pnl NUMERIC(14, 4),
    net_pnl_percentage NUMERIC(8, 4),
    entry_time TIMESTAMPTZ NOT NULL,
    exit_time TIMESTAMPTZ,
    holding_duration_seconds INT,
    exit_reason VARCHAR(50),
    broker_order_id VARCHAR(100),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_fo_trades_symbol ON fo_trades (symbol);
CREATE INDEX IF NOT EXISTS idx_fo_trades_signal ON fo_trades (signal_id);
CREATE INDEX IF NOT EXISTS idx_fo_trades_entry_time ON fo_trades (entry_time);

-- 5. Risk-Control & Pre-Execution Safety Events
CREATE TABLE IF NOT EXISTS fo_risk_events (
    id BIGSERIAL PRIMARY KEY,
    event_type VARCHAR(50) NOT NULL, -- PRICE_DEVIATION_EXCEEDED, MARKET_CLOSED_REJECTION, INSUFFICIENT_LIQUIDITY, STALE_DATA_REJECTION, POSITION_LIMIT_REACHED
    symbol VARCHAR(50) NOT NULL,
    signal_id VARCHAR(64),
    attempted_price NUMERIC(12, 4),
    market_quote_price NUMERIC(12, 4),
    deviation_points NUMERIC(12, 4),
    deviation_percentage NUMERIC(8, 4),
    threshold_allowed NUMERIC(8, 4),
    details JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_fo_risk_events_symbol ON fo_risk_events (symbol, created_at);

-- 6. Worker Execution History & Health Telemetry
CREATE TABLE IF NOT EXISTS fo_worker_executions (
    id BIGSERIAL PRIMARY KEY,
    worker_name VARCHAR(50) NOT NULL, -- signal_worker, open_call_worker
    cycle_id VARCHAR(64),
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    duration_seconds NUMERIC(8, 3),
    records_examined INT DEFAULT 0,
    new_data_accepted INT DEFAULT 0,
    signals_generated INT DEFAULT 0,
    active_calls_monitored INT DEFAULT 0,
    exit_events_triggered INT DEFAULT 0,
    status VARCHAR(20) NOT NULL DEFAULT 'SUCCESS', -- SUCCESS, FAILURE, PARTIAL
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_fo_worker_started ON fo_worker_executions (started_at DESC);
