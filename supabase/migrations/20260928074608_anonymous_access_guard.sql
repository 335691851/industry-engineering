CREATE TABLE IF NOT EXISTS engineering.access_buckets (
 key text NOT NULL, window_id bigint NOT NULL, hits integer NOT NULL,
 expires_at timestamptz NOT NULL, PRIMARY KEY (key, window_id)
);
CREATE INDEX IF NOT EXISTS access_buckets_expiry ON engineering.access_buckets(expires_at);
ALTER TABLE engineering.access_buckets ENABLE ROW LEVEL SECURITY;
ALTER TABLE engineering.access_buckets FORCE ROW LEVEL SECURITY;
REVOKE ALL ON engineering.access_buckets FROM PUBLIC, anon, authenticated, engineering_app;
