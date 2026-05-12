from pathlib import Path

from aedt_agent.knowledge.build_sqlite import build_api_semantics_db
from aedt_agent.knowledge.sqlite_provider import SQLiteKnowledgeProvider
from aedt_agent.nodes.registry import NodeRegistry


def test_api_semantics_covers_node_whitelists(tmp_path):
    db_path = tmp_path / "api_semantics.sqlite"
    build_api_semantics_db(
        Path("knowledge/api_semantics/api_semantics.schema.sql"),
        Path("knowledge/api_semantics/api_semantics.seed.jsonl"),
        db_path,
    )
    provider = SQLiteKnowledgeProvider(db_path)
    registry = NodeRegistry.from_directory(Path("nodes/catalog"))

    uncovered: set[str] = set()
    for node in registry.list_nodes():
        for api in node.api_whitelist:
            results = provider.search_api(api, limit=10)
            if not any(result.fqname == api for result in results):
                uncovered.add(api)

    assert len(uncovered) == 0, f"APIs not in semantics library: {uncovered}"


def test_api_semantics_has_at_least_50_entries(tmp_path):
    db_path = tmp_path / "api_semantics.sqlite"
    build_api_semantics_db(
        Path("knowledge/api_semantics/api_semantics.schema.sql"),
        Path("knowledge/api_semantics/api_semantics.seed.jsonl"),
        db_path,
    )
    provider = SQLiteKnowledgeProvider(db_path)
    categories = ["geometry", "material", "boundary", "excitation", "setup", "postprocess"]
    total = sum(len(provider.search_api(cat, limit=100)) for cat in categories)
    assert total >= 50, f"Only {total} API entries, need at least 50"
