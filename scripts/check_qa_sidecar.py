#!/usr/bin/env python3
import argparse
import json
from pathlib import Path
from typing import Dict, List, Set


def _fail(msg: str) -> None:
    raise SystemExit(f"FAIL: {msg}")


def _ok(msg: str) -> None:
    print(f"OK: {msg}")


def check_sidecar(path: Path, min_match_rate: float) -> None:
    if not path.exists():
        _fail(f"sidecar not found: {path}")
    _ok(f"sidecar exists: {path}")

    obj = json.loads(path.read_text(encoding="utf-8"))
    required_top = {"doc_id", "version", "stats", "segments", "edges"}
    missing_top = sorted(required_top - set(obj.keys()))
    if missing_top:
        _fail(f"missing top-level keys: {missing_top}")
    _ok("top-level schema keys present")

    stats: Dict = obj.get("stats") or {}
    match_rate = float(stats.get("source_match_rate", 0.0))
    if match_rate < min_match_rate:
        _fail(f"source_match_rate {match_rate:.4f} < {min_match_rate:.4f}")
    _ok(f"source_match_rate >= threshold ({match_rate:.4f})")

    segments: List[Dict] = obj.get("segments") or []
    empty_ids = [i for i, s in enumerate(segments) if not s.get("segment_id")]
    if empty_ids:
        _fail(f"segments with empty segment_id: {len(empty_ids)}")
    _ok("all segments have segment_id")

    seg_ids: Set[str] = {str(s["segment_id"]) for s in segments if s.get("segment_id")}
    formula_ids: Set[str] = {
        str(f["segment_id"])
        for f in (obj.get("formula_refs") or [])
        if isinstance(f, dict) and f.get("segment_id")
    }
    concept_ids: Set[str] = {
        str(c["segment_id"])
        for c in (obj.get("concept_refs") or [])
        if isinstance(c, dict) and c.get("segment_id")
    }
    dangling_edges = []
    for idx, e in enumerate(obj.get("edges") or []):
        src = e.get("source_id")
        tgt = e.get("target_id")
        src_ok = src in seg_ids or src in formula_ids or src in concept_ids
        tgt_ok = tgt in seg_ids or tgt in formula_ids or tgt in concept_ids
        if not (src_ok and tgt_ok):
            dangling_edges.append(idx)
    if dangling_edges:
        _fail(f"dangling edges referencing missing segment IDs: {len(dangling_edges)}")
    _ok("all edges reference existing segment IDs")

    print("PASS: qa sidecar regression checks passed")


def main() -> None:
    parser = argparse.ArgumentParser(description="Check QA sidecar regression invariants.")
    parser.add_argument("path", type=Path, help="Path to *_qa_segments.json")
    parser.add_argument("--min-match-rate", type=float, default=0.98, help="Minimum acceptable source_match_rate")
    args = parser.parse_args()
    check_sidecar(args.path, args.min_match_rate)


if __name__ == "__main__":
    main()
