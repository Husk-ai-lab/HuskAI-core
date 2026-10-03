"""Typed graph operations: the ONLY way automations change in this phase.

Operations are plain dicts so they can be stored in proposals (ultra-careful personality)
and replayed later. Every application is validated by the AutomationGraph model and the
graph is re-laid out deterministically.
"""

from __future__ import annotations

from typing import Any

from pydantic import TypeAdapter, ValidationError

from app.data.models.automation import AutomationGraph, Edge, Node
from app.helpers.text import slug

_node_adapter: TypeAdapter = TypeAdapter(Node)

X0, DX, Y0, DY = 60.0, 240.0, 260.0, 160.0


class GraphOpError(ValueError):
    pass


def _unique_id(graph: AutomationGraph, base: str) -> str:
    base = slug(base, 32).replace("-", "_") or "nodo"
    existing = {n.id for n in graph.nodes}
    if base not in existing:
        return base
    i = 2
    while f"{base}_{i}" in existing:
        i += 1
    return f"{base}_{i}"


def _edge_id(source: str, target: str, branch: str | None) -> str:
    return f"e_{source}__{target}" + (f"__{slug(branch, 12)}" if branch else "")


def apply_op(graph: AutomationGraph, op: dict[str, Any]) -> AutomationGraph:
    data = graph.model_dump(mode="json")
    nodes: list[dict] = data["nodes"]
    edges: list[dict] = data["edges"]
    kind = op.get("op")
    by_id = {n["id"]: n for n in nodes}

    if kind == "add_node":
        node = dict(op.get("node") or {})
        node["id"] = _unique_id(graph, node.get("id") or node.get("label", "nodo"))
        node.setdefault("position", {"x": 0, "y": 0})
        try:
            _node_adapter.validate_python(node)
        except ValidationError as exc:
            raise GraphOpError(f"invalid node: {exc.errors()[0]['msg']} ({exc.errors()[0]['loc']})") from exc
        if node.get("kind") == "trigger":
            raise GraphOpError("the trigger already exists: use set_trigger")
        after, branch, before = op.get("after"), op.get("branch"), op.get("before")
        if after and after not in by_id:
            raise GraphOpError(f"node '{after}' does not exist")
        nodes.append(node)
        if after:
            if before:  # insert between `after` and `before`
                edges[:] = [e for e in edges if not (e["source"] == after and e["target"] == before)]
                edges.append({"id": _edge_id(node["id"], before, None), "source": node["id"], "target": before, "branch": None})
            edges.append({"id": _edge_id(after, node["id"], branch), "source": after, "target": node["id"], "branch": branch})
    elif kind == "update_node":
        nid = op.get("node_id")
        if nid not in by_id:
            raise GraphOpError(f"node '{nid}' does not exist")
        changes = dict(op.get("changes") or {})
        for forbidden in ("id", "kind"):
            changes.pop(forbidden, None)
        by_id[nid].update(changes)
    elif kind == "remove_node":
        nid = op.get("node_id")
        if nid not in by_id:
            raise GraphOpError(f"node '{nid}' does not exist")
        if by_id[nid]["kind"] == "trigger":
            raise GraphOpError("the trigger cannot be removed")
        incoming = [e for e in edges if e["target"] == nid]
        outgoing = [e for e in edges if e["source"] == nid]
        nodes[:] = [n for n in nodes if n["id"] != nid]
        edges[:] = [e for e in edges if nid not in (e["source"], e["target"])]
        if len(incoming) == 1 and len(outgoing) == 1:  # keep the flow connected
            i, o = incoming[0], outgoing[0]
            edges.append({"id": _edge_id(i["source"], o["target"], i["branch"]), "source": i["source"],
                          "target": o["target"], "branch": i["branch"]})
    elif kind == "connect":
        s, t, branch = op.get("source"), op.get("target"), op.get("branch")
        if s not in by_id or t not in by_id:
            raise GraphOpError("source or target does not exist")
        if not any(e["source"] == s and e["target"] == t and e.get("branch") == branch for e in edges):
            edges.append({"id": _edge_id(s, t, branch), "source": s, "target": t, "branch": branch})
    elif kind == "disconnect":
        s, t = op.get("source"), op.get("target")
        edges[:] = [e for e in edges if not (e["source"] == s and e["target"] == t)]
    elif kind == "set_trigger":
        trig = next((n for n in nodes if n["kind"] == "trigger"), None)
        if trig is None:
            raise GraphOpError("no trigger")
        for k in ("trigger", "cron", "folder", "pattern", "label", "subtitle"):
            if op.get(k) is not None:
                trig[k] = op[k]
    else:
        raise GraphOpError(f"unknown operation: {kind}")

    try:
        new = AutomationGraph.model_validate({"nodes": nodes, "edges": edges})
    except ValidationError as exc:
        raise GraphOpError(f"invalid flow: {exc.errors()[0]['msg']}") from exc
    return layout(new)


def apply_ops(graph: AutomationGraph, ops: list[dict[str, Any]]) -> AutomationGraph:
    for op in ops:
        graph = apply_op(graph, op)
    return graph


def layout(graph: AutomationGraph) -> AutomationGraph:
    """Left-to-right layers by longest path from the trigger; branches fan out vertically."""
    if not graph.nodes:
        return graph
    trigger = graph.trigger().id
    layer: dict[str, int] = {trigger: 0}
    order = [trigger]
    changed = True
    guard = 0
    while changed and guard < len(graph.nodes) + 2:
        changed = False
        guard += 1
        for e in graph.edges:
            if e.source in layer and layer.get(e.target, -1) < layer[e.source] + 1:
                layer[e.target] = layer[e.source] + 1
                changed = True
                if e.target not in order:
                    order.append(e.target)
    max_layer = max(layer.values(), default=0)
    for n in graph.nodes:  # disconnected nodes go after the flow
        if n.id not in layer:
            max_layer += 1
            layer[n.id] = max_layer
    branch_rank = {"vero": -1, "falso": 1}
    y_hint: dict[str, float] = {trigger: 0.0}
    for e in sorted(graph.edges, key=lambda e: layer.get(e.source, 0)):
        base = y_hint.get(e.source, 0.0)
        siblings = [x for x in graph.edges if x.source == e.source]
        if len(siblings) > 1:
            if e.branch in branch_rank:
                off = branch_rank[e.branch]
            else:
                off = siblings.index(e) - (len(siblings) - 1) / 2
            y_hint.setdefault(e.target, base + off * 0.9)
        else:
            y_hint.setdefault(e.target, base)
    by_layer: dict[int, list[str]] = {}
    for nid, lyr in layer.items():
        by_layer.setdefault(lyr, []).append(nid)
    for lyr, ids in by_layer.items():
        ids.sort(key=lambda i: y_hint.get(i, 0.0))
        ys = [y_hint.get(i, 0.0) for i in ids]
        for k in range(1, len(ys)):  # avoid overlaps in a column
            if ys[k] - ys[k - 1] < 0.85:
                ys[k] = ys[k - 1] + 0.85
        for nid, y in zip(ids, ys, strict=True):
            n = graph.node(nid)
            n.position.x = X0 + lyr * DX
            n.position.y = Y0 + y * DY
    return graph


def compact(graph: AutomationGraph) -> str:
    """Short textual view of the flow for prompts."""
    lines = []
    for n in graph.nodes:
        d = n.model_dump(exclude={"position"}, exclude_none=True)
        lines.append(str(d))
    for e in graph.edges:
        lines.append(f"{e.source} -> {e.target}" + (f" [{e.branch}]" if e.branch else ""))
    return "\n".join(lines)


def edge(source: str, target: str, branch: str | None = None) -> Edge:
    return Edge(id=_edge_id(source, target, branch), source=source, target=target, branch=branch)
