-- Additive migration. Original construction plans/observations are not modified.
BEGIN;
CREATE SCHEMA IF NOT EXISTS analytics_catalog;
CREATE SCHEMA IF NOT EXISTS analytics_runtime;

CREATE TABLE IF NOT EXISTS analytics_catalog.rule_set (
    version text PRIMARY KEY,
    source_sha256 text NOT NULL CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
    catalog_version text NOT NULL UNIQUE,
    matrix_json jsonb NOT NULL,
    imported_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE IF NOT EXISTS analytics_catalog.profile_set (
    version text PRIMARY KEY,
    source_sha256 text NOT NULL CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
    metadata_json jsonb NOT NULL,
    source_snapshot jsonb NOT NULL,
    imported_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE IF NOT EXISTS analytics_catalog.visual_profile (
    profile_set_version text NOT NULL REFERENCES analytics_catalog.profile_set(version),
    stage_id integer NOT NULL,
    profile_json text NOT NULL CHECK (jsonb_typeof(profile_json::jsonb) = 'object'),
    PRIMARY KEY (profile_set_version, stage_id)
);

CREATE TABLE IF NOT EXISTS analytics_runtime.image (
    sha256 text PRIMARY KEY CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    data bytea NOT NULL CHECK (octet_length(data) <= 25000000)
);
CREATE TABLE IF NOT EXISTS analytics_runtime.frame_identity (
    site_id text NOT NULL, camera_id text NOT NULL, image_id text NOT NULL,
    sha256 text NOT NULL, observed_at timestamptz NOT NULL,
    PRIMARY KEY (site_id, camera_id, image_id)
);
CREATE TABLE IF NOT EXISTS analytics_runtime.plan_snapshot (
    site_id text NOT NULL, stream_code text NOT NULL, plan_id text NOT NULL, revision_id text NOT NULL,
    fingerprint text NOT NULL, payload jsonb NOT NULL,
    PRIMARY KEY (site_id, stream_code, plan_id, revision_id)
);
CREATE TABLE IF NOT EXISTS analytics_runtime.step_identity (
    site_id text NOT NULL, stream_code text NOT NULL, step_key text NOT NULL, stage_id integer NOT NULL,
    PRIMARY KEY (site_id, stream_code, step_key)
);
CREATE TABLE IF NOT EXISTS analytics_runtime.history_snapshot (
    site_id text NOT NULL, stream_code text NOT NULL, snapshot_id text NOT NULL,
    fingerprint text NOT NULL, payload jsonb NOT NULL,
    PRIMARY KEY (site_id, stream_code, snapshot_id)
);
CREATE TABLE IF NOT EXISTS analytics_runtime.progress_identity (
    site_id text NOT NULL, stream_code text NOT NULL, event_id text NOT NULL,
    fingerprint text NOT NULL, payload jsonb NOT NULL,
    PRIMARY KEY (site_id, stream_code, event_id)
);
CREATE TABLE IF NOT EXISTS analytics_runtime.request (
    service text NOT NULL CHECK (service IN ('deterministic','vlm_llm')),
    site_id text NOT NULL, request_id text NOT NULL,
    analysis_id uuid NOT NULL UNIQUE,
    fingerprint text NOT NULL,
    state text NOT NULL CHECK (state IN ('running','succeeded','failed','unknown')),
    metadata_text text NOT NULL,
    image_sha256 text NOT NULL REFERENCES analytics_runtime.image(sha256),
    versions jsonb NOT NULL,
    gateway_started boolean NOT NULL DEFAULT false,
    response jsonb,
    http_status integer,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (service, site_id, request_id)
);
CREATE TABLE IF NOT EXISTS analytics_runtime.attempt (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    service text NOT NULL, site_id text NOT NULL, request_id text NOT NULL,
    phase text NOT NULL, payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (service,site_id,request_id) REFERENCES analytics_runtime.request(service,site_id,request_id)
);

DO $roles$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='lct_analytics_deterministic') THEN
        CREATE ROLE lct_analytics_deterministic NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='lct_analytics_vlm') THEN
        CREATE ROLE lct_analytics_vlm NOLOGIN;
    END IF;
END
$roles$;
GRANT USAGE ON SCHEMA analytics_catalog, analytics_runtime TO lct_analytics_deterministic, lct_analytics_vlm;
GRANT SELECT ON ALL TABLES IN SCHEMA analytics_catalog TO lct_analytics_deterministic, lct_analytics_vlm;
GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA analytics_runtime TO lct_analytics_deterministic, lct_analytics_vlm;
GRANT UPDATE ON analytics_runtime.request TO lct_analytics_deterministic, lct_analytics_vlm;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA analytics_runtime TO lct_analytics_deterministic, lct_analytics_vlm;
ALTER TABLE analytics_runtime.request ENABLE ROW LEVEL SECURITY;
ALTER TABLE analytics_runtime.attempt ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS deterministic_request ON analytics_runtime.request;
CREATE POLICY deterministic_request ON analytics_runtime.request TO lct_analytics_deterministic USING (service='deterministic') WITH CHECK (service='deterministic');
DROP POLICY IF EXISTS vlm_request ON analytics_runtime.request;
CREATE POLICY vlm_request ON analytics_runtime.request TO lct_analytics_vlm USING (service='vlm_llm') WITH CHECK (service='vlm_llm');
DROP POLICY IF EXISTS deterministic_attempt ON analytics_runtime.attempt;
CREATE POLICY deterministic_attempt ON analytics_runtime.attempt TO lct_analytics_deterministic USING (service='deterministic') WITH CHECK (service='deterministic');
DROP POLICY IF EXISTS vlm_attempt ON analytics_runtime.attempt;
CREATE POLICY vlm_attempt ON analytics_runtime.attempt TO lct_analytics_vlm USING (service='vlm_llm') WITH CHECK (service='vlm_llm');
COMMIT;
