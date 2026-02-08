-- Trading System Database Schema
-- v6.0: 거래 내역 및 지표 기록 포함

-- Create schema
CREATE SCHEMA IF NOT EXISTS trading;
SET search_path TO trading, public;

-- ENUMs
DO $$ BEGIN
    CREATE TYPE strategytype AS ENUM ('CORE', 'SATELLITE', 'ATTACK', 'DIP_SCALPER', 'PULLBACK', 'REBOUND', 'IGNITION', 'SURGE');
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

DO $$ BEGIN
    CREATE TYPE orderside AS ENUM ('BUY', 'SELL');
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

DO $$ BEGIN
    CREATE TYPE ordertype AS ENUM ('MARKET', 'LIMIT');
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

DO $$ BEGIN
    CREATE TYPE orderstatus AS ENUM ('NEW', 'PENDING', 'SENT', 'ACK', 'PARTIAL', 'FILLED', 'CANCELED', 'REJECTED', 'SYNCED');
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

DO $$ BEGIN
    CREATE TYPE eventlevel AS ENUM ('INFO', 'WARNING', 'ERROR', 'CRITICAL');
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

DO $$ BEGIN
    CREATE TYPE eventtype AS ENUM ('SYSTEM', 'ORDER', 'RISK', 'STRATEGY', 'CONNECTION', 'MODE_CHANGE', 'ATTACK', 'ATTACK_MODE_CHANGE', 'ATTACK_SIGNAL', 'ATTACK_ENTRY', 'ATTACK_EXIT', 'SATELLITE', 'SATELLITE_EXIT', 'PULLBACK', 'FILTER', 'SURGE', 'IGNITION', 'REBOUND', 'DIP_SCALPER');
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

DO $$ BEGIN
    CREATE TYPE tradingmode AS ENUM ('NORMAL', 'SAFE', 'HALT');
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

-- Orders table
CREATE TABLE IF NOT EXISTS orders (
    id SERIAL PRIMARY KEY,
    order_id VARCHAR(64) NOT NULL UNIQUE,
    exchange_order_id VARCHAR(64),
    symbol VARCHAR(20) NOT NULL,
    strategy strategytype NOT NULL,
    side orderside NOT NULL,
    order_type ordertype NOT NULL,
    quantity FLOAT NOT NULL,
    price FLOAT,
    filled_quantity FLOAT NOT NULL DEFAULT 0.0,
    avg_fill_price FLOAT,
    status orderstatus NOT NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_orders_symbol ON orders (symbol);
CREATE INDEX IF NOT EXISTS ix_orders_status ON orders (status);

-- Positions table
CREATE TABLE IF NOT EXISTS positions (
    id SERIAL PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,
    strategy strategytype NOT NULL,
    side orderside NOT NULL,
    quantity FLOAT NOT NULL,
    avg_price FLOAT NOT NULL,
    realized_pnl FLOAT NOT NULL DEFAULT 0.0,
    leverage FLOAT NOT NULL DEFAULT 1.0,
    is_open BOOLEAN NOT NULL DEFAULT TRUE,
    opened_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    closed_at TIMESTAMP WITHOUT TIME ZONE
);

CREATE INDEX IF NOT EXISTS ix_positions_symbol ON positions (symbol);
CREATE INDEX IF NOT EXISTS ix_positions_is_open ON positions (is_open);

-- Trades table
CREATE TABLE IF NOT EXISTS trades (
    id SERIAL PRIMARY KEY,
    order_id VARCHAR(64) NOT NULL,
    symbol VARCHAR(20) NOT NULL,
    strategy strategytype NOT NULL,
    side orderside NOT NULL,
    quantity FLOAT NOT NULL,
    price FLOAT NOT NULL,
    fee FLOAT NOT NULL DEFAULT 0.0,
    fee_asset VARCHAR(10) NOT NULL DEFAULT 'KRW',
    realized_pnl FLOAT NOT NULL DEFAULT 0.0,
    executed_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_trades_order_id ON trades (order_id);
CREATE INDEX IF NOT EXISTS ix_trades_symbol ON trades (symbol);

-- Events table
CREATE TABLE IF NOT EXISTS events (
    id SERIAL PRIMARY KEY,
    event_id VARCHAR(64) NOT NULL UNIQUE,
    timestamp TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    level eventlevel NOT NULL,
    event_type eventtype NOT NULL,
    message TEXT NOT NULL,
    details TEXT
);

CREATE INDEX IF NOT EXISTS ix_events_level ON events (level);
CREATE INDEX IF NOT EXISTS ix_events_event_type ON events (event_type);
CREATE INDEX IF NOT EXISTS ix_events_timestamp ON events (timestamp);

-- Mode history table
CREATE TABLE IF NOT EXISTS mode_history (
    id SERIAL PRIMARY KEY,
    mode tradingmode NOT NULL,
    reason TEXT NOT NULL,
    changed_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_mode_history_changed_at ON mode_history (changed_at);

-- Daily stats table
CREATE TABLE IF NOT EXISTS daily_stats (
    id SERIAL PRIMARY KEY,
    date VARCHAR(10) NOT NULL UNIQUE,
    starting_equity FLOAT NOT NULL,
    ending_equity FLOAT NOT NULL DEFAULT 0.0,
    pnl FLOAT NOT NULL DEFAULT 0.0,
    core_pnl FLOAT NOT NULL DEFAULT 0.0,
    satellite_pnl FLOAT NOT NULL DEFAULT 0.0,
    fees FLOAT NOT NULL DEFAULT 0.0,
    trades_count INTEGER NOT NULL DEFAULT 0,
    max_drawdown FLOAT NOT NULL DEFAULT 0.0
);

-- Hourly stats table
CREATE TABLE IF NOT EXISTS hourly_stats (
    id SERIAL PRIMARY KEY,
    date VARCHAR(10) NOT NULL,
    hour INTEGER NOT NULL,
    pnl FLOAT NOT NULL DEFAULT 0.0,
    core_pnl FLOAT NOT NULL DEFAULT 0.0,
    satellite_pnl FLOAT NOT NULL DEFAULT 0.0,
    trades_count INTEGER NOT NULL DEFAULT 0,
    volume FLOAT NOT NULL DEFAULT 0.0
);

CREATE INDEX IF NOT EXISTS ix_hourly_stats_date ON hourly_stats (date);
CREATE INDEX IF NOT EXISTS ix_hourly_stats_hour ON hourly_stats (hour);

-- Symbol stats table
CREATE TABLE IF NOT EXISTS symbol_stats (
    id SERIAL PRIMARY KEY,
    date VARCHAR(10) NOT NULL,
    symbol VARCHAR(20) NOT NULL,
    strategy strategytype NOT NULL,
    realized_pnl FLOAT NOT NULL DEFAULT 0.0,
    unrealized_pnl FLOAT NOT NULL DEFAULT 0.0,
    trades_count INTEGER NOT NULL DEFAULT 0,
    volume FLOAT NOT NULL DEFAULT 0.0,
    win_count INTEGER NOT NULL DEFAULT 0,
    loss_count INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS ix_symbol_stats_date ON symbol_stats (date);
CREATE INDEX IF NOT EXISTS ix_symbol_stats_symbol ON symbol_stats (symbol);

-- Equity snapshots table
CREATE TABLE IF NOT EXISTS equity_snapshots (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    equity FLOAT NOT NULL,
    unrealized_pnl FLOAT NOT NULL DEFAULT 0.0,
    realized_pnl FLOAT NOT NULL DEFAULT 0.0
);

CREATE INDEX IF NOT EXISTS ix_equity_snapshots_timestamp ON equity_snapshots (timestamp);

-- Position ledger table
CREATE TABLE IF NOT EXISTS position_ledger (
    id SERIAL PRIMARY KEY,
    position_id VARCHAR(64) NOT NULL UNIQUE,
    strategy_id VARCHAR(20) NOT NULL,
    symbol VARCHAR(20) NOT NULL,
    side VARCHAR(10) NOT NULL,
    quantity FLOAT NOT NULL,
    avg_entry_price FLOAT NOT NULL,
    realized_pnl FLOAT NOT NULL DEFAULT 0.0,
    total_fees FLOAT NOT NULL DEFAULT 0.0,
    status VARCHAR(20) NOT NULL,
    entry_time TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    last_fill_time TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    fill_count INTEGER NOT NULL DEFAULT 1,
    stop_price FLOAT,
    initial_stop_distance FLOAT NOT NULL DEFAULT 0.0,
    metadata_json TEXT
);

CREATE INDEX IF NOT EXISTS ix_position_ledger_strategy_id ON position_ledger (strategy_id);
CREATE INDEX IF NOT EXISTS ix_position_ledger_symbol ON position_ledger (symbol);
CREATE INDEX IF NOT EXISTS ix_position_ledger_status ON position_ledger (status);

-- Execution costs table
CREATE TABLE IF NOT EXISTS execution_costs (
    id SERIAL PRIMARY KEY,
    order_id VARCHAR(64) NOT NULL,
    order_type VARCHAR(20) NOT NULL,
    symbol VARCHAR(20) NOT NULL,
    strategy_id VARCHAR(20) NOT NULL,
    side VARCHAR(10) NOT NULL,
    requested_price FLOAT NOT NULL,
    filled_price FLOAT NOT NULL,
    filled_qty FLOAT NOT NULL,
    timestamp TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    fee_krw FLOAT NOT NULL,
    slippage_bps FLOAT NOT NULL,
    spread_bps_at_fill FLOAT NOT NULL DEFAULT 0.0,
    notional_krw FLOAT NOT NULL,
    total_cost_krw FLOAT NOT NULL,
    cost_pct FLOAT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_execution_costs_order_id ON execution_costs (order_id);
CREATE INDEX IF NOT EXISTS ix_execution_costs_symbol ON execution_costs (symbol);
CREATE INDEX IF NOT EXISTS ix_execution_costs_strategy_id ON execution_costs (strategy_id);
CREATE INDEX IF NOT EXISTS ix_execution_costs_timestamp ON execution_costs (timestamp);

-- Trade details table (v6.0: 지표 기록)
CREATE TABLE IF NOT EXISTS trade_details (
    id SERIAL PRIMARY KEY,
    trade_id VARCHAR(64) NOT NULL UNIQUE,
    symbol VARCHAR(20) NOT NULL,
    strategy VARCHAR(20) NOT NULL,
    entry_time TIMESTAMP WITH TIME ZONE NOT NULL,
    exit_time TIMESTAMP WITH TIME ZONE,
    entry_price FLOAT NOT NULL,
    exit_price FLOAT,
    quantity FLOAT NOT NULL,
    pnl FLOAT NOT NULL DEFAULT 0.0,
    pnl_pct FLOAT NOT NULL DEFAULT 0.0,
    exit_reason VARCHAR(50),
    indicators_json JSON,
    hold_duration_sec INTEGER,
    max_profit_pct FLOAT NOT NULL DEFAULT 0.0,
    max_drawdown_pct FLOAT NOT NULL DEFAULT 0.0,
    strategy_score FLOAT,
    strategy_level INTEGER,
    trade_date VARCHAR(10) NOT NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_trade_details_symbol ON trade_details (symbol);
CREATE INDEX IF NOT EXISTS ix_trade_details_strategy ON trade_details (strategy);
CREATE INDEX IF NOT EXISTS ix_trade_details_entry_time ON trade_details (entry_time);
CREATE INDEX IF NOT EXISTS ix_trade_details_trade_date ON trade_details (trade_date);

-- Strategy daily PnL table (v6.0: 전략별 일별 수익)
CREATE TABLE IF NOT EXISTS strategy_daily_pnl (
    id SERIAL PRIMARY KEY,
    date VARCHAR(10) NOT NULL,
    strategy VARCHAR(20) NOT NULL,
    total_pnl FLOAT NOT NULL DEFAULT 0.0,
    total_pnl_pct FLOAT NOT NULL DEFAULT 0.0,
    trade_count INTEGER NOT NULL DEFAULT 0,
    win_count INTEGER NOT NULL DEFAULT 0,
    loss_count INTEGER NOT NULL DEFAULT 0,
    win_rate FLOAT NOT NULL DEFAULT 0.0,
    total_volume FLOAT NOT NULL DEFAULT 0.0,
    avg_trade_size FLOAT NOT NULL DEFAULT 0.0,
    avg_win_pct FLOAT NOT NULL DEFAULT 0.0,
    avg_loss_pct FLOAT NOT NULL DEFAULT 0.0,
    max_win_pct FLOAT NOT NULL DEFAULT 0.0,
    max_loss_pct FLOAT NOT NULL DEFAULT 0.0,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_strategy_daily_pnl_date ON strategy_daily_pnl (date);
CREATE INDEX IF NOT EXISTS ix_strategy_daily_pnl_strategy ON strategy_daily_pnl (strategy);

-- Grant permissions
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA trading TO postgres;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA trading TO postgres;
