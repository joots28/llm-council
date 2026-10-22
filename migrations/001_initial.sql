-- Idempotent initial schema. No existing tables or data are dropped.
CREATE TABLE IF NOT EXISTS nexus_api_clients (
    client_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    disabled_at TIMESTAMPTZ NULL
);

CREATE TABLE IF NOT EXISTS nexus_api_keys (
    key_id UUID PRIMARY KEY,
    client_id TEXT NOT NULL REFERENCES nexus_api_clients(client_id),
    key_hash CHAR(64) NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    revoked_at TIMESTAMPTZ NULL,
    last_used_at TIMESTAMPTZ NULL
);
CREATE INDEX IF NOT EXISTS idx_nexus_api_keys_client_active
    ON nexus_api_keys(client_id) WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS nexus_conversations (
    id UUID PRIMARY KEY,
    client_id TEXT NOT NULL REFERENCES nexus_api_clients(client_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    title TEXT NOT NULL DEFAULT 'New Conversation',
    messages JSONB NOT NULL DEFAULT '[]'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_nexus_conversations_client_created
    ON nexus_conversations(client_id, created_at DESC);

CREATE TABLE IF NOT EXISTS nexus_usage_events (
    id BIGSERIAL PRIMARY KEY,
    client_id TEXT NULL,
    api_key_id UUID NULL,
    method TEXT NOT NULL,
    route TEXT NOT NULL,
    status_code INTEGER NOT NULL,
    duration_ms INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_nexus_usage_client_created
    ON nexus_usage_events(client_id, created_at DESC);

CREATE TABLE IF NOT EXISTS nexus_schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
INSERT INTO nexus_schema_migrations(version)
VALUES ('001_initial')
ON CONFLICT (version) DO NOTHING;
