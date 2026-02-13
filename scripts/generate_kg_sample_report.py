#!/usr/bin/env python3
import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Set, Tuple


def _short(text: str, n: int = 180) -> str:
    t = " ".join((text or "").split())
    return t[:n] + ("..." if len(t) > n else "")


def _node_label(node: Dict) -> str:
    sid = node.get("segment_id", "unknown")
    st = node.get("segment_type", "unknown")
    pg = node.get("page_start")
    return f"{sid} [{st}] p{pg}"


def _page(node: Dict) -> int:
    p = node.get("page_start")
    return int(p) if isinstance(p, int) else 0


def build_report(kg_path: Path, out_path: Path, max_chains: int = 8) -> None:
    kg = json.loads(kg_path.read_text(encoding="utf-8"))
    nodes = {n.get("segment_id"): n for n in kg.get("nodes", []) if n.get("segment_id")}
    edges = kg.get("edges", [])

    uses_formula: Dict[str, Set[str]] = defaultdict(set)
    answer_of: List[Tuple[str, str]] = []
    explains: List[Tuple[str, str]] = []
    for e in edges:
        et = e.get("edge_type")
        s = e.get("source_id")
        t = e.get("target_id")
        if not s or not t:
            continue
        if et == "USES_FORMULA":
            uses_formula[s].add(t)
        elif et == "ANSWER_OF":
            answer_of.append((s, t))  # solution -> question
        elif et == "EXPLAINS":
            explains.append((s, t))  # derivation -> formula

    formula_to_examples: Dict[str, List[str]] = defaultdict(list)
    for src, fids in uses_formula.items():
        n = nodes.get(src) or {}
        if n.get("segment_type") == "worked_example":
            for fid in fids:
                formula_to_examples[fid].append(src)

    formula_to_derivs: Dict[str, List[str]] = defaultdict(list)
    for d, f in explains:
        formula_to_derivs[f].append(d)

    chains = []
    seen_chain_keys: Set[Tuple[str, str, str]] = set()
    for sol_id, q_id in answer_of:
        q_formulas = uses_formula.get(q_id, set())
        s_formulas = uses_formula.get(sol_id, set())
        shared = sorted(q_formulas & s_formulas)
        if not shared:
            continue
        q_node = nodes.get(q_id, {})
        s_node = nodes.get(sol_id, {})
        q_page = _page(q_node)
        s_page = _page(s_node)
        # Prefer formula closest to both question and solution pages.
        def _formula_cost(form_id: str) -> int:
            f_node = nodes.get(form_id, {})
            fp = _page(f_node)
            return abs(fp - q_page) + abs(fp - s_page)

        fid = min(shared, key=_formula_cost)
        ex_ids = formula_to_examples.get(fid, [])
        if ex_ids:
            ex_id = min(ex_ids, key=lambda eid: abs(_page(nodes.get(eid, {})) - q_page))
        else:
            ex_id = None
        der_ids = formula_to_derivs.get(fid, [])
        if der_ids:
            der_id = min(der_ids, key=lambda did: abs(_page(nodes.get(did, {})) - _page(nodes.get(fid, {}))))
        else:
            der_id = None
        key = (q_id, sol_id, fid)
        if key in seen_chain_keys:
            continue
        seen_chain_keys.add(key)
        chains.append((fid, ex_id, q_id, sol_id, der_id))
        if len(chains) >= max_chains:
            break

    lines: List[str] = []
    lines.append("# KG Sample Chain Report")
    lines.append("")
    lines.append(f"- Source: `{kg_path}`")
    lines.append(f"- Nodes: {len(nodes)}")
    lines.append(f"- Edges: {len(edges)}")
    lines.append(f"- Chain samples: {len(chains)}")
    lines.append("")

    concept_linked = sum(1 for n in nodes.values() if n.get("concept_links"))
    lines.append(f"- Nodes with concept links: {concept_linked}")
    if concept_linked == 0:
        lines.append("- Note: no concept links present yet; chains below are formula-centric.")
    lines.append("")

    for i, (fid, ex_id, q_id, s_id, d_id) in enumerate(chains, 1):
        f = nodes.get(fid, {})
        q = nodes.get(q_id, {})
        s = nodes.get(s_id, {})
        ex = nodes.get(ex_id, {}) if ex_id else {}
        d = nodes.get(d_id, {}) if d_id else {}
        lines.append(f"## Chain {i}")
        lines.append(f"1. Formula: `{_node_label(f)}`")
        lines.append(f"   - Text: {_short(f.get('text_content') or f.get('formula_text_raw') or '')}")
        if ex_id:
            lines.append(f"2. Worked Example: `{_node_label(ex)}`")
            lines.append(f"   - Text: {_short(ex.get('text_content') or ex.get('example_prompt') or '')}")
        else:
            lines.append("2. Worked Example: _not found for this formula_")
        if d_id:
            lines.append(f"3. Derivation: `{_node_label(d)}`")
            lines.append(f"   - Text: {_short(d.get('text_content') or '')}")
        else:
            lines.append("3. Derivation: _not linked for this formula_")
        lines.append(f"4. Question: `{_node_label(q)}`")
        lines.append(f"   - Text: {_short(q.get('text_content') or '')}")
        lines.append(f"5. Solution: `{_node_label(s)}`")
        lines.append(f"   - Text: {_short(s.get('text_content') or '')}")
        lines.append("")

    if not chains:
        lines.append("No complete formula-example-question-solution chains found.")
        lines.append("")

    # Additional targeted samples so progress is visible even when full 5-hop
    # chains are sparse.
    we_links = []
    de_links = []
    for e in edges:
        s = e.get("source_id")
        t = e.get("target_id")
        if not s or not t:
            continue
        src = nodes.get(s) or {}
        tgt = nodes.get(t) or {}
        if e.get("edge_type") == "USES_FORMULA" and src.get("segment_type") == "worked_example" and tgt.get("segment_type") == "formula":
            we_links.append((s, t))
        if e.get("edge_type") == "EXPLAINS" and src.get("segment_type") == "derivation" and tgt.get("segment_type") == "formula":
            de_links.append((s, t))

    lines.append("## Worked Example -> Formula Samples")
    if not we_links:
        lines.append("- none")
    else:
        for i, (sid, tid) in enumerate(we_links[:5], 1):
            src = nodes.get(sid, {})
            tgt = nodes.get(tid, {})
            lines.append(f"{i}. `{_node_label(src)}` -> `{_node_label(tgt)}`")
            lines.append(f"   - Example text: {_short(src.get('text_content') or src.get('example_prompt') or '')}")
            lines.append(f"   - Formula text: {_short(tgt.get('text_content') or tgt.get('formula_text_raw') or '')}")

    lines.append("")
    lines.append("## Derivation -> Formula Samples")
    if not de_links:
        lines.append("- none")
    else:
        for i, (sid, tid) in enumerate(de_links[:5], 1):
            src = nodes.get(sid, {})
            tgt = nodes.get(tid, {})
            lines.append(f"{i}. `{_node_label(src)}` -> `{_node_label(tgt)}`")
            lines.append(f"   - Derivation text: {_short(src.get('text_content') or '')}")
            lines.append(f"   - Formula text: {_short(tgt.get('text_content') or tgt.get('formula_text_raw') or '')}")

    out_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate KG sample chain report.")
    parser.add_argument("kg_path", type=Path, help="Path to *_kg_segments.json")
    parser.add_argument("--out", type=Path, required=True, help="Output markdown report path")
    parser.add_argument("--max-chains", type=int, default=8, help="Maximum chains to include")
    args = parser.parse_args()
    build_report(args.kg_path, args.out, max_chains=args.max_chains)
    print(f"Wrote report: {args.out}")


if __name__ == "__main__":
    main()
