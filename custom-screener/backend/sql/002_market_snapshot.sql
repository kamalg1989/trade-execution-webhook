-- Custom Screener: daily market breadth / regime. Plain table (one row/day).
CREATE TABLE IF NOT EXISTS market_snapshot (
  id                          BIGSERIAL PRIMARY KEY,
  snapshot_date               DATE NOT NULL UNIQUE,
  total_stocks                INT,
  eligible_stocks             INT,
  count_above_50sma           INT,
  count_above_200sma          INT,
  count_below_50sma           INT,
  count_below_200sma          INT,
  count_within_15pct_52w_high INT,
  count_within_10pct_52w_high INT,
  count_within_15pct_52w_low  INT,
  count_within_10pct_52w_low  INT,
  count_new_52w_high          INT,
  count_new_52w_low           INT,
  count_moved_gt_4_5pct_1d    INT,
  count_moved_gt_20pct_1m     INT,
  count_moved_gt_60pct_3m     INT,
  count_moved_gt_100pct_6m    INT,
  regime                      VARCHAR(30),
  trend_score                 NUMERIC(4,2),
  breadth_score               NUMERIC(4,2),
  is_complete                 BOOLEAN DEFAULT FALSE,
  created_at                  TIMESTAMPTZ DEFAULT NOW(),
  updated_at                  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ms_date ON market_snapshot (snapshot_date);
