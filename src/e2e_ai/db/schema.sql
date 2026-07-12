CREATE TABLE schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE projects (
    id TEXT PRIMARY KEY,
    root_path TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE tests (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    title TEXT NOT NULL,
    spec_file TEXT NOT NULL,
    project_name TEXT,
    line INTEGER,
    raw_list_line TEXT NOT NULL,
    excluded INTEGER NOT NULL,
    exclude_reason TEXT,
    is_stale INTEGER NOT NULL DEFAULT 0,
    stale_at TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    last_status TEXT,
    FOREIGN KEY(project_id) REFERENCES projects(id)
);

CREATE TABLE runs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    reason TEXT,
    FOREIGN KEY(project_id) REFERENCES projects(id)
);

CREATE TABLE attempts (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    test_id TEXT NOT NULL,
    attempt_index INTEGER NOT NULL,
    status TEXT NOT NULL,
    work_dir TEXT NOT NULL,
    environment_id TEXT,
    database_name TEXT,
    frontend_url TEXT,
    backend_url TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    exit_code INTEGER,
    FOREIGN KEY(run_id) REFERENCES runs(id),
    FOREIGN KEY(test_id) REFERENCES tests(id)
);

CREATE TABLE failure_packets (
    id TEXT PRIMARY KEY,
    attempt_id TEXT NOT NULL,
    signature TEXT NOT NULL,
    error_message TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(attempt_id) REFERENCES attempts(id)
);

CREATE TABLE repair_plans (
    id TEXT PRIMARY KEY,
    test_id TEXT NOT NULL,
    failure_packet_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    plan_text TEXT NOT NULL,
    result_json TEXT,
    created_at TEXT NOT NULL,
    superseded_by TEXT,
    FOREIGN KEY(test_id) REFERENCES tests(id),
    FOREIGN KEY(failure_packet_id) REFERENCES failure_packets(id)
);

CREATE TABLE agent_invocations (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    test_id TEXT,
    role TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    model_requested TEXT,
    model_effective TEXT,
    quota_snapshot_json TEXT,
    command_json TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    exit_code INTEGER,
    stdout_path TEXT,
    stderr_path TEXT,
    provider_order_json TEXT,
    exit_class TEXT,
    switch_reason TEXT,
    failover_retry INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY(run_id) REFERENCES runs(id)
);

CREATE INDEX idx_tests_project_spec ON tests(project_id, spec_file);
CREATE INDEX idx_tests_project_stale ON tests(project_id, is_stale);
CREATE INDEX idx_attempts_test_started ON attempts(test_id, started_at);
CREATE INDEX idx_failure_packets_signature ON failure_packets(signature);
CREATE INDEX idx_repair_plans_test_created ON repair_plans(test_id, created_at);
