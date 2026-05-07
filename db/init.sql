-- ================================================================
-- Auto-Bidding Bot — PostgreSQL Schema
-- Run automatically on first startup via docker-entrypoint-initdb.d
-- ================================================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- n8n needs its own DB
CREATE DATABASE n8n WITH OWNER bidbot;

-- ── interactions ─────────────────────────────────────────────
-- Primary deduplication + audit log table
CREATE TABLE IF NOT EXISTS interactions (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    platform        VARCHAR(20)  NOT NULL CHECK (platform IN ('linkedin', 'x')),
    post_id         TEXT         NOT NULL,
    post_url        TEXT,
    post_content    TEXT,
    author_name     TEXT,
    account_id      TEXT         NOT NULL,  -- which account was used to bid
    status          VARCHAR(20)  NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'posted', 'skipped', 'failed', 'duplicate')),
    ai_model        TEXT,
    ai_prompt       TEXT,
    ai_response     TEXT,
    comment_posted  TEXT,
    error_message   TEXT,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    posted_at       TIMESTAMPTZ,
    UNIQUE (platform, post_id)               -- hard dedup guarantee
);

CREATE INDEX idx_interactions_platform     ON interactions(platform);
CREATE INDEX idx_interactions_status       ON interactions(status);
CREATE INDEX idx_interactions_created_at   ON interactions(created_at DESC);

-- ── daily_counters ───────────────────────────────────────────
-- Tracks per-account, per-day action limits
CREATE TABLE IF NOT EXISTS daily_counters (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    account_id  TEXT         NOT NULL,
    platform    VARCHAR(20)  NOT NULL,
    action_date DATE         NOT NULL DEFAULT CURRENT_DATE,
    count       INTEGER      NOT NULL DEFAULT 0,
    UNIQUE (account_id, platform, action_date)
);

-- ── circuit_breaker_state ────────────────────────────────────
-- Tracks consecutive failures for auto-halt logic
CREATE TABLE IF NOT EXISTS circuit_breaker_state (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    platform        VARCHAR(20)  NOT NULL UNIQUE,
    failure_count   INTEGER      NOT NULL DEFAULT 0,
    tripped         BOOLEAN      NOT NULL DEFAULT FALSE,
    last_failure_at TIMESTAMPTZ,
    tripped_at      TIMESTAMPTZ,
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

INSERT INTO circuit_breaker_state (platform) VALUES ('linkedin'), ('x')
ON CONFLICT DO NOTHING;

-- ── skills_profile ───────────────────────────────────────────
-- User's skills, for injecting into AI prompts (RAG context)
CREATE TABLE IF NOT EXISTS skills_profile (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    category    TEXT NOT NULL,
    skill       TEXT NOT NULL,
    proficiency VARCHAR(20) DEFAULT 'expert',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Seed with default skills (edit via dashboard)
INSERT INTO skills_profile (category, skill, proficiency) VALUES
    ('languages',   'Python',           'expert'),
    ('languages',   'TypeScript',       'expert'),
    ('frameworks',  'FastAPI',          'expert'),
    ('frameworks',  'React',            'advanced'),
    ('tools',       'n8n',              'expert'),
    ('tools',       'Docker',           'expert'),
    ('tools',       'PostgreSQL',       'expert'),
    ('ai',          'LangChain',        'advanced'),
    ('ai',          'OpenAI API',       'expert'),
    ('ai',          'Groq',             'expert'),
    ('platforms',   'AWS',              'advanced'),
    ('specialty',   'Automation',       'expert'),
    ('specialty',   'Web Scraping',     'expert'),
    ('specialty',   'API Integration',  'expert')
ON CONFLICT DO NOTHING;

-- ── keywords ─────────────────────────────────────────────────
-- Configurable search keywords per platform
CREATE TABLE IF NOT EXISTS keywords (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    platform    VARCHAR(20)  NOT NULL,
    keyword     TEXT         NOT NULL,
    enabled     BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (platform, keyword)
);

INSERT INTO keywords (platform, keyword) VALUES
    ('linkedin', 'hiring freelance developer'),
    ('linkedin', 'looking for freelancer'),
    ('linkedin', 'need python developer'),
    ('linkedin', 'react developer needed'),
    ('linkedin', 'automation developer contract'),
    ('linkedin', 'api integration freelancer'),
    ('x',        'looking for a developer'),
    ('x',        'hiring freelancer'),
    ('x',        'need a developer'),
    ('x',        'python freelancer'),
    ('x',        'build automation for me')
ON CONFLICT DO NOTHING;

-- ── system_config ────────────────────────────────────────────
-- Runtime config editable without redeployment
CREATE TABLE IF NOT EXISTS system_config (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    description TEXT,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO system_config (key, value, description) VALUES
    ('linkedin_daily_limit',    '12',       'Max LinkedIn comments per day'),
    ('x_daily_limit',           '7',        'Max X replies per day'),
    ('min_delay_minutes',       '8',        'Minimum minutes between actions'),
    ('max_delay_minutes',       '25',       'Maximum minutes between actions'),
    ('business_hours_start',    '09:00',    'Business hours start (HH:MM, local)'),
    ('business_hours_end',      '18:00',    'Business hours end (HH:MM, local)'),
    ('circuit_breaker_threshold','3',       'Consecutive failures before circuit trip'),
    ('groq_model',              'llama-3.3-70b-versatile', 'Primary Groq model'),
    ('openrouter_model',        'anthropic/claude-3-haiku', 'OpenRouter fallback model'),
    ('ai_temperature',          '0.75',     'LLM temperature for bid generation')
ON CONFLICT DO NOTHING;
