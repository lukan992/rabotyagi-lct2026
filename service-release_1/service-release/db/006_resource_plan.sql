-- Additive v2 journal migration; archived v1 payloads/results stay unchanged.
BEGIN;
ALTER TABLE analytics_runtime.request ADD COLUMN IF NOT EXISTS input_version text NOT NULL
    DEFAULT 'frame-analysis-input-v1'
    CHECK (input_version IN ('frame-analysis-input-v1', 'frame-analysis-input-v2'));
CREATE TABLE IF NOT EXISTS analytics_runtime.resource_plan_snapshot (
    site_id text NOT NULL, stream_code text NOT NULL, plan_id text NOT NULL, revision_id text NOT NULL,
    fingerprint text NOT NULL, payload jsonb NOT NULL,
    PRIMARY KEY (site_id, stream_code, plan_id, revision_id)
);
CREATE TABLE IF NOT EXISTS analytics_runtime.source_snapshot (
    site_id text NOT NULL, stream_code text NOT NULL, source_system text NOT NULL, snapshot_id text NOT NULL,
    fingerprint text NOT NULL, payload jsonb NOT NULL,
    PRIMARY KEY (site_id, stream_code, source_system, snapshot_id)
);
GRANT SELECT, INSERT ON analytics_runtime.resource_plan_snapshot, analytics_runtime.source_snapshot
    TO lct_analytics_deterministic, lct_analytics_vlm;
COMMIT;
