from __future__ import annotations

from typing import Any

from aedt_agent.agent.mission import NodeRunStatus


_STATUS_SYMBOLS: dict[str, str] = {
    NodeRunStatus.CREATED.value: "◯",
    NodeRunStatus.RUNNING.value: "◌",
    NodeRunStatus.SUCCEEDED.value: "●",
    NodeRunStatus.FAILED.value: "✕",
    NodeRunStatus.SKIPPED.value: "⏭",
    NodeRunStatus.WAITING_APPROVAL.value: "⏸",
}

_STATUS_COLORS: dict[str, str] = {
    NodeRunStatus.CREATED.value: "\033[90m",       # grey
    NodeRunStatus.RUNNING.value: "\033[94m",       # blue
    NodeRunStatus.SUCCEEDED.value: "\033[92m",     # green
    NodeRunStatus.FAILED.value: "\033[91m",        # red
    NodeRunStatus.SKIPPED.value: "\033[93m",       # yellow
    NodeRunStatus.WAITING_APPROVAL.value: "\033[95m",  # magenta
}
_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"


def render_graph_live(
    template: dict[str, Any],
    node_runs: list[dict[str, Any]],
    handoffs: list[dict[str, Any]],
    *,
    title: str = "",
    width: int = 80,
) -> str:
    """Render an ASCII DAG with colored node states.

    Args:
        template: graph template snapshot (with nodes, edges).
        node_runs: list of node_run records with node_id, status, edge_decision.
        handoffs: list of handoff records with edge_id, from_node, to_node, outcome, status.
        title: optional header line.
        width: max line width for the layout.
    """
    nodes = template.get("nodes", [])
    edges = template.get("edges", [])
    template_id = template.get("template_id", template.get("id", ""))

    # Build node state map: node_id -> latest_run
    state_map: dict[str, dict[str, Any]] = {}
    for run in sorted(node_runs, key=lambda r: r.get("sequence", 0)):
        state_map[run["node_id"]] = run

    # Build edge traversal map
    edge_traversal: dict[str, int] = {}
    for h in handoffs:
        eid = h.get("edge_id", "")
        edge_traversal[eid] = edge_traversal.get(eid, 0) + 1

    lines: list[str] = []
    if title:
        lines.append(f"{_BOLD}{title}{_RESET}")
    lines.append(f"{_DIM}template: {template_id}  |  {len(nodes)} nodes, {len(edges)} edges{_RESET}")
    lines.append("─" * width)

    # Layout: assign each node a row based on topological depth
    depth, node_order = _topological_layout(nodes, edges)

    # Build a grid: rows are depth layers, columns are nodes at that depth
    rows: dict[int, list[dict[str, Any]]] = {}
    for node in nodes:
        d = depth.get(node["id"], 0)
        rows.setdefault(d, []).append(node)

    # Render each layer
    for d in sorted(rows):
        layer = rows[d]
        cols = len(layer)
        col_width = max(1, (width - 4) // max(cols, 1))

        # Node row
        node_line = "  "
        for node in layer:
            node_id = node["id"]
            state = state_map.get(node_id, {})
            status = state.get("status", "pending")
            decision = state.get("edge_decision", "")
            role = node.get("role", "")
            kind = node.get("kind", "")

            symbol = _STATUS_SYMBOLS.get(status, "?")
            color = _STATUS_COLORS.get(status, "\033[0m")

            label = f"{node_id}"
            if decision and status in ("succeeded", "failed", "skipped"):
                label += f":{decision}"
            elif status == "pending":
                label += f" ({role or kind})"

            padded = _pad_center(label, col_width - 2)
            node_line += f"{color}{symbol} {padded}{_RESET}"
        lines.append(node_line)

        # Edge row (show edges between this layer and next)
        if d + 1 in rows:
            next_layer = {n["id"] for n in rows[d + 1]}
            edge_line = "  "
            for node in layer:
                out_edges = [e for e in edges if e["from"] == node["id"] and e["to"] in next_layer]
                if out_edges:
                    status_char = _edge_status_char(out_edges, edge_traversal, state_map)
                    edge_line += _pad_center(status_char, col_width)
                else:
                    edge_line += " " * col_width
            lines.append(edge_line)

        # Separator between layers
        lines.append("")

    # Legend
    lines.append("─" * width)
    lines.append(f"{_DIM}Legend:{_RESET}")
    legend_items = []
    for status, symbol in _STATUS_SYMBOLS.items():
        color = _STATUS_COLORS.get(status, "")
        legend_items.append(f"{color}{symbol} {status}{_RESET}")
    lines.append("  ".join(legend_items))

    return "\n".join(lines)


def _edge_status_char(
    out_edges: list[dict[str, Any]],
    edge_traversal: dict[str, int],
    state_map: dict[str, dict[str, Any]],
) -> str:
    """Return a character representing edge state."""
    traversed = any(edge_traversal.get(e["id"], 0) > 0 for e in out_edges)
    if traversed:
        return f"{_DIM}│{_RESET}"
    # Check if target nodes have been reached through any path
    targets = {e["to"] for e in out_edges}
    reached = any(state_map.get(t, {}).get("status") in ("succeeded", "failed", "skipped") for t in targets)
    if reached:
        return f"{_DIM}↓{_RESET}"
    return " "


def render_graph_mermaid(
    template: dict[str, Any],
    node_runs: list[dict[str, Any]],
    handoffs: list[dict[str, Any]],
) -> str:
    """Render a Mermaid flowchart showing node states with colors."""
    nodes = template.get("nodes", [])
    edges = template.get("edges", [])
    state_map: dict[str, dict[str, Any]] = {}
    for run in sorted(node_runs, key=lambda r: r.get("sequence", 0)):
        state_map[run["node_id"]] = run

    lines = ["flowchart TD"]

    # Define node styles
    for node in nodes:
        nid = node["id"]
        state = state_map.get(nid, {})
        status = state.get("status", "pending")
        decision = state.get("edge_decision", "")
        role = node.get("role", "")
        kind = node.get("kind", "")

        # Build label: use \n for line breaks, escape parens
        status_short = status[:4]  # succ/fail/skip/wait/runn/pend
        if decision and status in ("succeeded", "failed", "skipped"):
            label = f"{nid}\\n{status_short}:{decision}"
        elif status == "pending":
            label = f"{nid}\\n{role or kind}"
        else:
            label = f"{nid}\\n{status_short}"

        safe_id = _safe_id(nid)
        color = _mermaid_color(status)
        lines.append(f'  {safe_id}["{label}"]')
        if color:
            lines.append(f"  style {safe_id} {color}")

    # Edges
    for edge in edges:
        from_id = _safe_id(edge["from"])
        to_id = _safe_id(edge["to"])
        condition = edge.get("if", edge.get("if_condition", ""))
        on = edge.get("on", "")

        if condition:
            lines.append(f'  {from_id} -->|"{condition[:30]}"| {to_id}')
        elif on and on not in ("succeeded",):
            lines.append(f'  {from_id} -->|{on}| {to_id}')
        else:
            lines.append(f"  {from_id} --> {to_id}")

    return "\n".join(lines)


def _mermaid_color(status: str) -> str:
    mapping = {
        "succeeded": "fill:#1a3a1a,stroke:#4ade80,color:#bbf7d0",
        "failed": "fill:#3a1a1a,stroke:#f87171,color:#fecaca",
        "skipped": "fill:#3a3510,stroke:#facc15,color:#fef08a",
        "waiting_approval": "fill:#2a1a3a,stroke:#c084fc,color:#e9d5ff",
        "running": "fill:#1a2a3a,stroke:#60a5fa,color:#bfdbfe",
        "created": "fill:#1a1a2a,stroke:#6b7280,color:#9ca3af",
    }
    return mapping.get(status, "")


def _safe_id(node_id: str) -> str:
    """Make a node ID safe for Mermaid by replacing special chars."""
    return node_id.replace("-", "_").replace(".", "_").replace(":", "_")


def _topological_layout(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
) -> tuple[dict[str, int], list[str]]:
    """Assign depth to each node based on incoming edges."""
    depth: dict[str, int] = {}
    node_ids = {n["id"] for n in nodes}

    # Find root nodes (no incoming edges or after=[])
    has_incoming: set[str] = set()
    for e in edges:
        has_incoming.add(e["to"])
    for n in nodes:
        after = n.get("after", [])
        for a in after:
            if a in node_ids:
                has_incoming.add(n["id"])

    roots = [n["id"] for n in nodes if n["id"] not in has_incoming]

    # BFS to assign depths
    adjacency: dict[str, list[str]] = {n["id"]: [] for n in nodes}
    for e in edges:
        adjacency.setdefault(e["from"], []).append(e["to"])

    queue = list(roots)
    for r in queue:
        depth[r] = 0

    while queue:
        current = queue.pop(0)
        for neighbor in adjacency.get(current, []):
            new_depth = depth[current] + 1
            if neighbor not in depth or depth[neighbor] < new_depth:
                depth[neighbor] = new_depth
            if neighbor not in queue:
                queue.append(neighbor)

    # Assign depth 0 to any unvisited nodes
    for n in nodes:
        depth.setdefault(n["id"], 0)

    return depth, roots


def _pad_center(text: str, width: int) -> str:
    if len(text) >= width:
        return text[: width - 1] + "…"
    left = (width - len(text)) // 2
    right = width - len(text) - left
    return " " * left + text + " " * right
