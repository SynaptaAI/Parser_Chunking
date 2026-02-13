#!/usr/bin/env python3
import argparse
import json
from pathlib import Path
from typing import Dict, List, Set


def _fail(msg: str) -> None:
    raise SystemExit(f"FAIL: {msg}")


def _ok(msg: str) -> None:
    print(f"OK: {msg}")


def check_kg(path: Path) -> None:
    if not path.exists():
        _fail(f"kg sidecar not found: {path}")
    _ok(f"kg sidecar exists: {path}")

    obj = json.loads(path.read_text(encoding="utf-8"))
    required_top = {"doc_id", "version", "stats", "nodes", "edges"}
    missing_top = sorted(required_top - set(obj.keys()))
    if missing_top:
        _fail(f"missing top-level keys: {missing_top}")
    _ok("top-level schema keys present")

    nodes: List[Dict] = obj.get("nodes") or []
    edges: List[Dict] = obj.get("edges") or []
    if not nodes:
        _fail("nodes is empty")
    _ok("nodes is non-empty")

    node_ids: Set[str] = set()
    dup = 0
    for n in nodes:
        nid = n.get("segment_id")
        if not nid:
            _fail("node missing segment_id")
        if nid in node_ids:
            dup += 1
        node_ids.add(nid)
    if dup:
        _fail(f"duplicate node IDs found: {dup}")
    _ok("node IDs are unique")

    required_edge_keys = {"source_id", "target_id", "edge_type", "strength", "anchor_metadata"}
    dangling = 0
    for e in edges:
        if required_edge_keys - set(e.keys()):
            _fail("edge missing required keys")
        if e.get("source_id") not in node_ids or e.get("target_id") not in node_ids:
            dangling += 1
    if dangling:
        _fail(f"dangling edges: {dangling}")
    _ok("all edges reference existing nodes")

    stats = obj.get("stats") or {}
    if int(stats.get("node_count", -1)) != len(nodes):
        _fail("stats.node_count mismatch")
    if int(stats.get("edge_count", -1)) != len(edges):
        _fail("stats.edge_count mismatch")
    _ok("stats counts match payload")
    print("PASS: kg sidecar regression checks passed")


def main() -> None:
    parser = argparse.ArgumentParser(description="Check KG sidecar invariants.")
    parser.add_argument("path", type=Path, help="Path to *_kg_segments.json")
    args = parser.parse_args()
    check_kg(args.path)


if __name__ == "__main__":
    main()

