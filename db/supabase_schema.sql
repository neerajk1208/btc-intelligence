-- Run this in Supabase SQL Editor to create the tables

-- Spreads table - logs every price check (every 3 seconds)
CREATE TABLE IF NOT EXISTS spreads (
    id BIGSERIAL PRIMARY KEY,
    ts BIGINT NOT NULL,                    -- Unix timestamp in milliseconds
    hl_price DOUBLE PRECISION NOT NULL,
    def_price DOUBLE PRECISION NOT NULL,
    spread_bps DOUBLE PRECISION NOT NULL,
    mode TEXT NOT NULL,                     -- WAITING_ENTRY or IN_POSITION
    def_latency_ms DOUBLE PRECISION,
    hl_price_age_ms DOUBLE PRECISION,
    price_gap_ms DOUBLE PRECISION,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index for time-based queries
CREATE INDEX IF NOT EXISTS idx_spreads_ts ON spreads(ts);

-- Trades table - logs every entry/exit execution
CREATE TABLE IF NOT EXISTS trades (
    id BIGSERIAL PRIMARY KEY,
    ts BIGINT NOT NULL,                    -- Unix timestamp in milliseconds
    cycle_id INTEGER NOT NULL,
    side TEXT NOT NULL,                     -- ENTRY or EXIT
    expected_spread_bps DOUBLE PRECISION,
    actual_spread_bps DOUBLE PRECISION,
    slippage_bps DOUBLE PRECISION,
    hl_price DOUBLE PRECISION,
    def_price DOUBLE PRECISION,
    order_size_usd DOUBLE PRECISION,
    def_fill_amount DOUBLE PRECISION,
    hl_fill_amount DOUBLE PRECISION,
    def_latency_ms DOUBLE PRECISION,
    hl_latency_ms DOUBLE PRECISION,
    total_exec_ms DOUBLE PRECISION,
    success INTEGER,                        -- 1 or 0
    error TEXT,
    gross_pnl DOUBLE PRECISION,
    net_pnl DOUBLE PRECISION,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_trades_ts ON trades(ts);
CREATE INDEX IF NOT EXISTS idx_trades_cycle ON trades(cycle_id);
CREATE INDEX IF NOT EXISTS idx_trades_side ON trades(side);

-- Enable Row Level Security (optional but recommended)
ALTER TABLE spreads ENABLE ROW LEVEL SECURITY;
ALTER TABLE trades ENABLE ROW LEVEL SECURITY;

-- Allow all operations for authenticated users (using service key)
CREATE POLICY "Enable all for service key" ON spreads FOR ALL USING (true);
CREATE POLICY "Enable all for service key" ON trades FOR ALL USING (true);
