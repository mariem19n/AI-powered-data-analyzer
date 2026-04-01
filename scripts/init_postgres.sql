-- ============================================================
-- Init PostgreSQL — AI-Powered Data Analyzer
-- Exécuté automatiquement au premier lancement du conteneur
--
-- Sources de données :
--   1. Alpha Vantage  → Données OHLCV crypto (source principale)
--   2. CoinGecko      → Enrichissement market cap & métadonnées
--   3. FRED           → Indicateurs macroéconomiques
--   4. GDELT          → Événements & sentiment médiatique
--
-- Schéma :
--   dim_crypto              → Référentiel cryptomonnaies
--   dim_time                → Calendrier
--   dim_fred_series         → Référentiel séries FRED
--   fact_crypto_daily       → OHLCV journalier (partitionnée)
--   fact_fred_observation   → Observations économiques
--   fact_gdelt_events       → Événements & sentiment
--   stg_daily_metrics       → Métriques dérivées crypto
--   agg_monthly_crypto      → Agrégation mensuelle crypto
-- ============================================================


-- ==========================
-- 1. UTILISATEUR READ-ONLY (Sandbox Layer 5)
-- ==========================

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'sandbox_reader') THEN
        CREATE ROLE sandbox_reader WITH LOGIN PASSWORD 'sandbox_readonly_pwd';
    END IF;
END
$$;

GRANT CONNECT ON DATABASE analyzer_db TO sandbox_reader;
GRANT USAGE ON SCHEMA public TO sandbox_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO sandbox_reader;


-- ==========================
-- 2. TABLE DE SANTÉ
-- ==========================

CREATE TABLE IF NOT EXISTS _health_check (
    id SERIAL PRIMARY KEY,
    status VARCHAR(10) DEFAULT 'ok',
    checked_at TIMESTAMP DEFAULT NOW()
);

INSERT INTO _health_check (status) VALUES ('ok');


-- ============================================================
-- DIMENSIONS
-- ============================================================

-- ==========================
-- 3. DIMENSION — CRYPTOMONNAIES
-- ==========================

CREATE TABLE IF NOT EXISTS dim_crypto (
    crypto_id SERIAL PRIMARY KEY,
    symbol VARCHAR(10) NOT NULL UNIQUE,
    name VARCHAR(100) NOT NULL,
    alpha_vantage_key VARCHAR(20) NOT NULL,
    coingecko_key VARCHAR(50),
    category VARCHAR(50) DEFAULT 'Cryptocurrency',
    launch_date DATE,
    description TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

INSERT INTO dim_crypto (symbol, name, alpha_vantage_key, coingecko_key, category, launch_date) VALUES
    ('BTC',  'Bitcoin',       'BTC',  'bitcoin',        'Layer 1',      '2009-01-03'),
    ('ETH',  'Ethereum',      'ETH',  'ethereum',       'Layer 1',      '2015-07-30'),
    ('LTC',  'Litecoin',      'LTC',  'litecoin',       'Payment',      '2011-10-07'),
    ('SOL',  'Solana',        'SOL',  'solana',         'Layer 1',      '2020-03-16'),
    ('XRP',  'Ripple',        'XRP',  'ripple',         'Payment',      '2012-01-01'),
    ('ADA',  'Cardano',       'ADA',  'cardano',        'Layer 1',      '2017-09-29'),
    ('DOT',  'Polkadot',      'DOT',  'polkadot',       'Layer 0',      '2020-05-26'),
    ('DOGE', 'Dogecoin',      'DOGE', 'dogecoin',       'Meme',         '2013-12-06'),
    ('AVAX', 'Avalanche',     'AVAX', 'avalanche-2',    'Layer 1',      '2020-09-21'),
    ('LINK', 'Chainlink',     'LINK', 'chainlink',      'Oracle',       '2017-09-19')
ON CONFLICT (symbol) DO NOTHING;


-- ==========================
-- 4. DIMENSION — CALENDRIER
-- ==========================

CREATE TABLE IF NOT EXISTS dim_time (
    date_id DATE PRIMARY KEY,
    year INT NOT NULL,
    quarter INT NOT NULL,
    month INT NOT NULL,
    month_name VARCHAR(20) NOT NULL,
    week INT NOT NULL,
    day_of_week INT NOT NULL,
    day_name VARCHAR(20) NOT NULL,
    is_weekend BOOLEAN NOT NULL,
    is_holiday BOOLEAN DEFAULT FALSE
);

-- Calendrier de 2009 à 2030
INSERT INTO dim_time (date_id, year, quarter, month, month_name, week, day_of_week, day_name, is_weekend, is_holiday)
SELECT
    d::DATE AS date_id,
    EXTRACT(YEAR FROM d)::INT AS year,
    EXTRACT(QUARTER FROM d)::INT AS quarter,
    EXTRACT(MONTH FROM d)::INT AS month,
    TRIM(TO_CHAR(d, 'Month')) AS month_name,
    EXTRACT(WEEK FROM d)::INT AS week,
    EXTRACT(DOW FROM d)::INT AS day_of_week,
    TRIM(TO_CHAR(d, 'Day')) AS day_name,
    EXTRACT(DOW FROM d) IN (0, 6) AS is_weekend,
    FALSE AS is_holiday
FROM generate_series('2009-01-01'::DATE, '2030-12-31'::DATE, '1 day'::INTERVAL) AS d
ON CONFLICT DO NOTHING;


-- ==========================
-- 5. DIMENSION — SÉRIES FRED
-- ==========================

CREATE TABLE IF NOT EXISTS dim_fred_series (
    series_id SERIAL PRIMARY KEY,
    fred_code VARCHAR(50) NOT NULL UNIQUE,
    name VARCHAR(200) NOT NULL,
    description TEXT,
    frequency VARCHAR(20) NOT NULL DEFAULT 'daily',
    units VARCHAR(100),
    category VARCHAR(100),
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Séries macroéconomiques pertinentes pour l'analyse crypto
INSERT INTO dim_fred_series (fred_code, name, frequency, units, category) VALUES
    ('DFF',       'Federal Funds Effective Rate',           'daily',   'Percent',                     'Interest Rates'),
    ('DGS10',     'Treasury Yield 10-Year',                 'daily',   'Percent',                     'Interest Rates'),
    ('DGS2',      'Treasury Yield 2-Year',                  'daily',   'Percent',                     'Interest Rates'),
    ('DTWEXBGS',  'Trade Weighted US Dollar Index',         'daily',   'Index',                       'Exchange Rates'),
    ('DCOILWTICO', 'Crude Oil Price WTI',                   'daily',   'Dollars per Barrel',          'Commodities'),
    ('GOLDAMGBD228NLBM', 'Gold Price London Fix',           'daily',   'Dollars per Troy Ounce',      'Commodities'),
    ('SP500',     'S&P 500 Index',                          'daily',   'Index',                       'Stock Market'),
    ('VIXCLS',    'CBOE Volatility Index (VIX)',            'daily',   'Index',                       'Volatility'),
    ('CPIAUCSL',  'Consumer Price Index (CPI)',             'monthly', 'Index 1982-1984=100',         'Inflation'),
    ('UNRATE',    'Unemployment Rate',                      'monthly', 'Percent',                     'Employment'),
    ('M2SL',      'M2 Money Stock',                         'monthly', 'Billions of Dollars',         'Money Supply'),
    ('GDP',       'Gross Domestic Product',                  'quarterly','Billions of Dollars',        'GDP')
ON CONFLICT (fred_code) DO NOTHING;


-- ============================================================
-- TABLES DE FAITS
-- ============================================================

-- ==========================
-- 6. FAIT — OHLCV CRYPTO JOURNALIER (partitionnée)
-- ==========================

CREATE TABLE IF NOT EXISTS fact_crypto_daily (
    id BIGSERIAL,
    crypto_id INT NOT NULL REFERENCES dim_crypto(crypto_id),
    symbol VARCHAR(10) NOT NULL,
    date DATE NOT NULL,
    open_usd DECIMAL(18, 8) NOT NULL,
    high_usd DECIMAL(18, 8) NOT NULL,
    low_usd DECIMAL(18, 8) NOT NULL,
    close_usd DECIMAL(18, 8) NOT NULL,
    volume DECIMAL(24, 8) NOT NULL DEFAULT 0,
    market_cap_usd DECIMAL(24, 2),
    source VARCHAR(20) DEFAULT 'alpha_vantage',
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(crypto_id, date)
) PARTITION BY LIST (crypto_id);

-- Partitions (une par crypto)
CREATE TABLE IF NOT EXISTS fact_crypto_daily_btc  PARTITION OF fact_crypto_daily FOR VALUES IN (1);
CREATE TABLE IF NOT EXISTS fact_crypto_daily_eth  PARTITION OF fact_crypto_daily FOR VALUES IN (2);
CREATE TABLE IF NOT EXISTS fact_crypto_daily_bnb  PARTITION OF fact_crypto_daily FOR VALUES IN (3);
CREATE TABLE IF NOT EXISTS fact_crypto_daily_sol  PARTITION OF fact_crypto_daily FOR VALUES IN (4);
CREATE TABLE IF NOT EXISTS fact_crypto_daily_xrp  PARTITION OF fact_crypto_daily FOR VALUES IN (5);
CREATE TABLE IF NOT EXISTS fact_crypto_daily_ada  PARTITION OF fact_crypto_daily FOR VALUES IN (6);
CREATE TABLE IF NOT EXISTS fact_crypto_daily_dot  PARTITION OF fact_crypto_daily FOR VALUES IN (7);
CREATE TABLE IF NOT EXISTS fact_crypto_daily_doge PARTITION OF fact_crypto_daily FOR VALUES IN (8);
CREATE TABLE IF NOT EXISTS fact_crypto_daily_avax PARTITION OF fact_crypto_daily FOR VALUES IN (9);
CREATE TABLE IF NOT EXISTS fact_crypto_daily_link PARTITION OF fact_crypto_daily FOR VALUES IN (10);


-- ==========================
-- 7. FAIT — OBSERVATIONS FRED
-- ==========================

CREATE TABLE IF NOT EXISTS fact_fred_observation (
    id BIGSERIAL PRIMARY KEY,
    series_id INT NOT NULL REFERENCES dim_fred_series(series_id),
    fred_code VARCHAR(50) NOT NULL,
    date DATE NOT NULL,
    value DECIMAL(18, 6),
    source VARCHAR(20) DEFAULT 'fred',
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(fred_code, date)
);


-- ==========================
-- 8. FAIT — ÉVÉNEMENTS GDELT
-- ==========================

CREATE TABLE IF NOT EXISTS fact_gdelt_events (
    id BIGSERIAL PRIMARY KEY,
    date DATE NOT NULL,
    title TEXT,
    url TEXT,
    source_domain VARCHAR(200),
    source_country VARCHAR(10),
    language VARCHAR(10),
    tone DECIMAL(8, 4),
    positive_score DECIMAL(8, 4),
    negative_score DECIMAL(8, 4),
    polarity DECIMAL(8, 4),
    word_count INT,
    theme VARCHAR(200),
    keyword VARCHAR(200),
    created_at TIMESTAMP DEFAULT NOW()
);


-- ============================================================
-- TABLES STAGING & AGRÉGATION
-- ============================================================

-- ==========================
-- 9. STAGING — MÉTRIQUES JOURNALIÈRES CRYPTO
-- ==========================

CREATE TABLE IF NOT EXISTS stg_daily_metrics (
    metric_id BIGSERIAL PRIMARY KEY,
    crypto_id INT NOT NULL REFERENCES dim_crypto(crypto_id),
    symbol VARCHAR(10) NOT NULL,
    date DATE NOT NULL,
    close_usd DECIMAL(18, 8) NOT NULL,
    prev_close_usd DECIMAL(18, 8),
    daily_change_pct DECIMAL(10, 4),
    daily_range_usd DECIMAL(18, 8),
    volatility_pct DECIMAL(10, 4),
    volume DECIMAL(24, 8),
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(crypto_id, date)
);


-- ==========================
-- 10. AGRÉGATION — MENSUELLE CRYPTO
-- ==========================

CREATE TABLE IF NOT EXISTS agg_monthly_crypto (
    agg_id BIGSERIAL PRIMARY KEY,
    crypto_id INT NOT NULL REFERENCES dim_crypto(crypto_id),
    symbol VARCHAR(10) NOT NULL,
    year INT NOT NULL,
    month INT NOT NULL,
    open_usd DECIMAL(18, 8),
    close_usd DECIMAL(18, 8),
    high_usd DECIMAL(18, 8),
    low_usd DECIMAL(18, 8),
    avg_close_usd DECIMAL(18, 8),
    total_volume DECIMAL(24, 8),
    monthly_change_pct DECIMAL(10, 4),
    avg_daily_volatility DECIMAL(10, 4),
    trading_days INT,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(crypto_id, year, month)
);


-- ==========================
-- 11. AGRÉGATION — SENTIMENT QUOTIDIEN GDELT
-- ==========================

CREATE TABLE IF NOT EXISTS agg_daily_sentiment (
    id BIGSERIAL PRIMARY KEY,
    date DATE NOT NULL,
    keyword VARCHAR(200) NOT NULL,
    article_count INT DEFAULT 0,
    avg_tone DECIMAL(8, 4),
    avg_positive DECIMAL(8, 4),
    avg_negative DECIMAL(8, 4),
    avg_polarity DECIMAL(8, 4),
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(date, keyword)
);


-- ============================================================
-- INDEX POUR PERFORMANCE
-- ============================================================

-- Fact crypto
CREATE INDEX IF NOT EXISTS idx_fact_crypto_date ON fact_crypto_daily(date);
CREATE INDEX IF NOT EXISTS idx_fact_crypto_symbol ON fact_crypto_daily(symbol);
CREATE INDEX IF NOT EXISTS idx_fact_crypto_id_date ON fact_crypto_daily(crypto_id, date);

-- Fact FRED
CREATE INDEX IF NOT EXISTS idx_fred_obs_code ON fact_fred_observation(fred_code);
CREATE INDEX IF NOT EXISTS idx_fred_obs_date ON fact_fred_observation(date);
CREATE INDEX IF NOT EXISTS idx_fred_obs_code_date ON fact_fred_observation(fred_code, date);

-- Fact GDELT
CREATE INDEX IF NOT EXISTS idx_gdelt_date ON fact_gdelt_events(date);
CREATE INDEX IF NOT EXISTS idx_gdelt_keyword ON fact_gdelt_events(keyword);
CREATE INDEX IF NOT EXISTS idx_gdelt_theme ON fact_gdelt_events(theme);
CREATE INDEX IF NOT EXISTS idx_gdelt_tone ON fact_gdelt_events(tone);

-- Staging
CREATE INDEX IF NOT EXISTS idx_stg_metrics_date ON stg_daily_metrics(date);
CREATE INDEX IF NOT EXISTS idx_stg_metrics_crypto ON stg_daily_metrics(crypto_id);

-- Agrégation crypto
CREATE INDEX IF NOT EXISTS idx_agg_monthly_crypto ON agg_monthly_crypto(crypto_id);
CREATE INDEX IF NOT EXISTS idx_agg_monthly_year_month ON agg_monthly_crypto(year, month);

-- Agrégation sentiment
CREATE INDEX IF NOT EXISTS idx_agg_sentiment_date ON agg_daily_sentiment(date);
CREATE INDEX IF NOT EXISTS idx_agg_sentiment_keyword ON agg_daily_sentiment(keyword);

-- Dimensions
CREATE INDEX IF NOT EXISTS idx_dim_crypto_symbol ON dim_crypto(symbol);
CREATE INDEX IF NOT EXISTS idx_dim_crypto_active ON dim_crypto(is_active);
CREATE INDEX IF NOT EXISTS idx_dim_time_year ON dim_time(year);
CREATE INDEX IF NOT EXISTS idx_dim_time_month ON dim_time(year, month);
CREATE INDEX IF NOT EXISTS idx_dim_fred_code ON dim_fred_series(fred_code);


-- ==========================
-- 12. PERMISSIONS READ-ONLY POUR LE SANDBOX
-- ==========================

GRANT SELECT ON ALL TABLES IN SCHEMA public TO sandbox_reader;