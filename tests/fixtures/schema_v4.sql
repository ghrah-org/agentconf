CREATE TABLE providers (
    id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, provider_type TEXT NOT NULL,
    base_url TEXT NOT NULL, api_key TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')), updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE model_capabilities (
    model_key TEXT PRIMARY KEY,
    origin TEXT NOT NULL CHECK(origin IN ('upstream','derived','local','example')),
    display_name TEXT NOT NULL, family TEXT, model_type TEXT NOT NULL,
    max_context_length INTEGER CHECK (max_context_length IS NULL OR max_context_length > 0), max_output_length INTEGER CHECK (max_output_length IS NULL OR max_output_length > 0), max_input_length INTEGER CHECK (max_input_length IS NULL OR max_input_length > 0), dimensions INTEGER CHECK (dimensions IS NULL OR dimensions > 0), top_n INTEGER CHECK (top_n IS NULL OR top_n > 0), supports_streaming INTEGER CHECK (supports_streaming IS NULL OR supports_streaming IN (0, 1)), supports_function_calling INTEGER CHECK (supports_function_calling IS NULL OR supports_function_calling IN (0, 1)), supports_reasoning INTEGER CHECK (supports_reasoning IS NULL OR supports_reasoning IN (0, 1)), supports_vision INTEGER CHECK (supports_vision IS NULL OR supports_vision IN (0, 1)), supports_json_mode INTEGER CHECK (supports_json_mode IS NULL OR supports_json_mode IN (0, 1)), input_modalities TEXT, output_modalities TEXT, languages TEXT, voices TEXT, supported_sizes TEXT, extra TEXT,
    annotations TEXT,
    source TEXT NOT NULL, source_ref TEXT, source_fetched_at TEXT,
    derived_from TEXT REFERENCES model_capabilities(model_key) ON DELETE RESTRICT,
    base_fingerprint TEXT CHECK (base_fingerprint IS NULL OR length(base_fingerprint)=64),
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision > 0),
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    CHECK ((origin='example') = (substr(model_key,1,8)='example-')),
    CHECK ((origin='example') = (source='example')),
    CHECK ((origin='derived') = (derived_from IS NOT NULL)),
    CHECK ((origin='derived') = (base_fingerprint IS NOT NULL)),
    CHECK (origin='derived' OR (derived_from IS NULL AND base_fingerprint IS NULL)),
    CHECK (derived_from IS NULL OR derived_from <> model_key),
    CHECK (origin='example' OR source <> 'example')
);
CREATE TABLE model_instances (
    id TEXT PRIMARY KEY, provider_id TEXT NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
    name TEXT NOT NULL UNIQUE, model_name TEXT NOT NULL, model_type TEXT NOT NULL DEFAULT 'llm',
    capabilities_key TEXT REFERENCES model_capabilities(model_key) ON DELETE RESTRICT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')), updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE agents (
    id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE,
    model_instance_id TEXT NOT NULL REFERENCES model_instances(id) ON DELETE CASCADE,
    temperature REAL, top_p REAL, top_k INTEGER, max_tokens INTEGER, extra_params TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')), updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
PRAGMA user_version=4;
