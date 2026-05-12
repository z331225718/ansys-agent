CREATE TABLE IF NOT EXISTS api_semantics (
    fqname              TEXT PRIMARY KEY,
    domain              TEXT,
    category            TEXT,
    signature           TEXT,
    params_json         TEXT,
    returns_json        TEXT,
    docstring           TEXT,
    constraints_json    TEXT,
    common_errors_json  TEXT,
    common_traps_json   TEXT,
    examples_ref_json   TEXT,
    source_refs_json    TEXT,
    confidence          TEXT,
    pyaedt_version      TEXT,
    aedt_version        TEXT,
    last_verified_at    TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS api_semantics_fts USING fts5(
    fqname,
    domain,
    category,
    signature,
    docstring,
    content=api_semantics,
    content_rowid=rowid
);

CREATE TRIGGER IF NOT EXISTS api_semantics_ai AFTER INSERT ON api_semantics BEGIN
    INSERT INTO api_semantics_fts(rowid, fqname, domain, category, signature, docstring)
    VALUES (new.rowid, new.fqname, new.domain, new.category, new.signature, new.docstring);
END;

CREATE TRIGGER IF NOT EXISTS api_semantics_ad AFTER DELETE ON api_semantics BEGIN
    INSERT INTO api_semantics_fts(api_semantics_fts, rowid, fqname, domain, category, signature, docstring)
    VALUES ('delete', old.rowid, old.fqname, old.domain, old.category, old.signature, old.docstring);
END;
