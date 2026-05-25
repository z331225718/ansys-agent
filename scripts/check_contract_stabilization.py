from __future__ import annotations

import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from aedt_agent.nodes.registry import NodeRegistry


def collect_contract_status() -> dict:
    registry = NodeRegistry.from_directory(Path("nodes/catalog"))
    all_nodes = registry.list_nodes(include_experimental=True)
    default_nodes = registry.list_nodes(include_experimental=False)

    experimental_layout = [node.node_id for node in all_nodes if node.track == "layout-brd" and node.status == "experimental"]
    default_layout = [node.node_id for node in default_nodes if node.track == "layout-brd"]

    trap_files = list(Path("knowledge/common_traps").glob("*.yaml"))
    api_records = [
        line
        for line in Path("knowledge/api_semantics/api_semantics.seed.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    return {
        "mcp_adapter_modes": {
            "fake": True,
            "real": True,
            "env_var": "AEDT_AGENT_MCP_ADAPTER",
        },
        "node_lifecycle": {
            "total_nodes": len(all_nodes),
            "default_nodes": len(default_nodes),
            "experimental_layout_nodes": experimental_layout,
            "default_layout_nodes": default_layout,
        },
        "knowledge_assets": {
            "api_records": len(api_records),
            "common_traps": len(trap_files),
        },
        "validation_positioning": {
            "structural_validation": True,
            "result_file_validation": True,
            "electromagnetic_semantic_validation": "limited-template-heuristics",
        },
    }


def main() -> None:
    print(json.dumps(collect_contract_status(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
