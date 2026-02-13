import json
import logging
import os
import re
import uuid
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Set, Tuple

from .models import ContentBlock

logger = logging.getLogger(__name__)

_TEXT_BLOCK_TYPES = {"text", "heading", "list_item"}
_TARGET_SEGMENT_TYPES = {"question", "solution", "derivation", "worked_example", "calculation", "reference_stub"}
_SOURCE_MATCH_TYPES = {"question", "solution", "derivation", "worked_example", "calculation"}
_CANDIDATE_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\bquestion\b",
        r"\bproblem\b",
        r"\bexercise\b",
        r"\bwhat\s+is\b",
        r"\bcalculate\b",
        r"\bdetermine\b",
        r"\bfind\b",
        r"\bexplain\b",
        r"\bdiscuss\b",
        r"\banalyze\b",
        r"\bsolution\b",
        r"\banswer\b",
        r"\btherefore\b",
        r"\bthus\b",
        r"\bwe\s+get\b",
        r"\bwe\s+find\b",
        r"\bderivation\b",
        r"\bproof\b",
        r"\bsubstitut(?:e|ing)\b",
        r"\bwe\s+can\s+show\b",
        r"\bworked\s+example\b",
        r"\billustration\b",
        r"\bgiven:\b",
        r"\bstep\s*1\b",
    ]
]


def enrich_qa_derivation(
    chunks: List[Dict[str, Any]],
    blocks: List[ContentBlock],
    page_sizes: Dict[int, Tuple[float, float]],
    out_dir: Path,
    doc_id: str = "book",
) -> Optional[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    sidecar_path = out_dir / f"{doc_id}_qa_segments.json"
    kg_sidecar_path = out_dir / f"{doc_id}_kg_segments.json"

    stats: Dict[str, Any] = {
        "total_chunks": len(chunks),
        "candidate_chunks": 0,
        "segments_out": 0,
        "edges_out": 0,
        "skipped_no_bbox": 0,
    }
    payload: Dict[str, Any] = {
        "doc_id": doc_id,
        "version": "qa-derivation-v1",
        "config": {
            "trigger_mode": "candidate",
            "language_rules": "en",
            "llm_mode": "off",
        },
        "stats": stats,
        "segments": [],
        "edges": [],
    }

    loaded = _load_synapta_components()
    if loaded is None:
        payload["stats"]["error"] = "synapta_unavailable"
        _write_sidecar(sidecar_path, payload)
        return sidecar_path
    text_extractor_cls, context_processor_cls, linker_cls, formula_segment_cls, concept_linker_cls = loaded

    candidates, skipped_no_bbox = _select_candidate_chunks(chunks)
    stats["candidate_chunks"] = len(candidates)
    stats["skipped_no_bbox"] = skipped_no_bbox
    if not candidates:
        _write_sidecar(sidecar_path, payload)
        return sidecar_path

    candidate_pages = _expand_candidate_pages(candidates, blocks, page_sizes)
    fitz_blocks_by_page = _blocks_to_fitz_tuples(blocks, candidate_pages, candidates)
    if not fitz_blocks_by_page:
        _write_sidecar(sidecar_path, payload)
        return sidecar_path

    extractor = text_extractor_cls(llm_mode="off")
    context_processor = context_processor_cls(mode="multi_chapter")
    concept_linker = _build_concept_linker(concept_linker_cls, doc_id=doc_id)
    linker = linker_cls(concept_linker=concept_linker)

    extracted_segments: List[Any] = []
    for page_num in sorted(fitz_blocks_by_page):
        width, height = page_sizes.get(page_num - 1, (1000.0, 1400.0))
        page = _build_dummy_page(width, height)
        try:
            page_segments = extractor.process_page(
                page=page,
                page_num=page_num,
                book_id=doc_id,
                blocks=fitz_blocks_by_page[page_num],
                doc_uri=None,
            )
            extracted_segments.extend(page_segments)
        except Exception as exc:
            logger.warning("QA enrich skip page %s due to error: %s", page_num, exc)

    if not extracted_segments:
        _write_sidecar(sidecar_path, payload)
        return sidecar_path

    processed_segments = context_processor.process(extracted_segments)
    target_segments = [
        seg for seg in processed_segments
        if getattr(seg, "segment_type", "") in _TARGET_SEGMENT_TYPES
    ]
    if not target_segments:
        _write_sidecar(sidecar_path, payload)
        return sidecar_path

    formula_segments = _build_formula_segments_from_chunks(chunks, formula_segment_cls, doc_id=doc_id)
    link_input = target_segments + formula_segments
    linked_segments_all, edges = linker.link_segments(link_input)
    candidate_index = _index_candidates_by_page(candidates, candidate_pages)

    linked_segments = [
        seg for seg in linked_segments_all
        if getattr(seg, "segment_type", "") in _TARGET_SEGMENT_TYPES
    ]
    formula_ids: Set[str] = {
        str(getattr(seg, "segment_id", ""))
        for seg in formula_segments
        if getattr(seg, "segment_id", None)
    }

    serialized_segments: List[Dict[str, Any]] = []
    for seg in linked_segments:
        data = _to_dict(seg)
        warnings = _derivation_quality_warnings(data)
        if warnings:
            data["quality_warnings"] = warnings
        source = _match_source_chunk(seg, candidate_index)
        if source:
            data["source_chunk_id"] = source["id"]
            data["source_chunk_heading_path"] = source.get("heading_path")
            data["source_match_method"] = source.get("match_method")
            data["source_chunk_candidate_role"] = source.get("candidate_role")
            data["source_chunk_qa_zone_type"] = source.get("qa_zone_type")
        serialized_segments.append(data)

    serialized_segments, derivation_refine_stats = _refine_derivation_segments(serialized_segments)
    kept_segment_ids: Set[str] = {
        str(s.get("segment_id"))
        for s in serialized_segments
        if s.get("segment_id")
    }

    concept_nodes_by_id: Dict[str, Dict[str, Any]] = {}
    serialized_edges: List[Dict[str, Any]] = []
    for edge in edges:
        data = _to_dict(edge)
        src = data.get("source_id")
        tgt = data.get("target_id")
        if src in kept_segment_ids and tgt in kept_segment_ids:
            serialized_edges.append(data)
            continue
        if (
            src in kept_segment_ids
            and tgt in formula_ids
            and data.get("edge_type") in {"REFERENCES", "USES_FORMULA", "EXPLAINS"}
        ):
            serialized_edges.append(data)
            continue
        if (
            src in kept_segment_ids
            and data.get("edge_type") in {"WORKED_EXAMPLE_OF"}
            and tgt
        ):
            serialized_edges.append(data)
            concept_nodes_by_id.setdefault(
                str(tgt),
                _build_concept_node(
                    concept_id=str(tgt),
                    concept_name=None,
                    level=None,
                    tags=[],
                    rationale=None,
                ),
            )
            continue
        if (
            src in formula_ids
            and data.get("edge_type") in {"DEFINES"}
            and tgt
        ):
            serialized_edges.append(data)
            concept_nodes_by_id.setdefault(
                str(tgt),
                _build_concept_node(
                    concept_id=str(tgt),
                    concept_name=None,
                    level=None,
                    tags=[],
                    rationale=None,
                ),
            )

    used_formula_ids: Set[str] = set(
        e.get("target_id")
        for e in serialized_edges
        if e.get("target_id") in formula_ids
    )
    formula_by_id = {
        str(getattr(seg, "segment_id")): seg
        for seg in formula_segments
        if getattr(seg, "segment_id", None)
    }
    for seg in serialized_segments:
        for fid in seg.get("referenced_formula_ids", []) or []:
            if fid in formula_ids:
                used_formula_ids.add(fid)
        for fid in seg.get("derived_from_formula_ids", []) or []:
            if fid in formula_ids:
                used_formula_ids.add(fid)
        to_fid = seg.get("derived_to_formula_id")
        if to_fid in formula_ids:
            used_formula_ids.add(to_fid)
    payload["formula_refs"] = [
        _to_dict(formula_by_id[fid]) for fid in sorted(used_formula_ids) if fid in formula_by_id
    ]
    _backfill_formula_ref_fields(payload["formula_refs"], _formula_source_by_id(chunks))
    for seg in serialized_segments + payload["formula_refs"]:
        for cl in seg.get("concept_links") or []:
            if not isinstance(cl, dict):
                continue
            cid = cl.get("concept_id")
            if not cid:
                continue
            concept_nodes_by_id[str(cid)] = _build_concept_node(
                concept_id=str(cid),
                concept_name=cl.get("concept_name"),
                level=cl.get("level"),
                tags=cl.get("tags") or [],
                rationale=cl.get("rationale"),
            )
    payload["concept_refs"] = list(concept_nodes_by_id.values())

    _append_mineru_solution_supplements(serialized_segments, chunks, doc_id=doc_id)
    _annotate_qa_keys_from_source(serialized_segments, chunks)
    serialized_edges = _rewrite_answer_edges(serialized_segments, serialized_edges)
    serialized_segments, serialized_edges, solution_prune_stats = _prune_unlinked_noisy_solutions(
        serialized_segments, serialized_edges
    )
    serialized_segments, serialized_edges, question_prune_stats = _prune_noisy_questions(
        serialized_segments, serialized_edges
    )
    serialized_segments, serialized_edges, solution_prune_stats_post_q = _prune_unlinked_noisy_solutions(
        serialized_segments, serialized_edges
    )
    for k, v in (solution_prune_stats_post_q or {}).items():
        solution_prune_stats[k] = int(solution_prune_stats.get(k, 0)) + int(v or 0)
    _annotate_question_solution_status(serialized_segments, serialized_edges)
    stub_segments, stub_edges = _build_reference_stubs(serialized_segments, doc_id=doc_id)
    if stub_segments:
        serialized_segments.extend(stub_segments)
        serialized_edges.extend(stub_edges)
    serialized_edges = _prune_edges_by_known_ids(
        serialized_edges,
        segment_ids={str(s.get("segment_id")) for s in serialized_segments if s.get("segment_id")},
        formula_ids={str(f.get("segment_id")) for f in payload["formula_refs"] if f.get("segment_id")},
        concept_ids={str(c.get("segment_id")) for c in payload["concept_refs"] if c.get("segment_id")},
    )
    payload["segments"] = serialized_segments
    payload["edges"] = serialized_edges
    matchable_segments = [s for s in serialized_segments if s.get("segment_type") in _SOURCE_MATCH_TYPES]
    matched = sum(1 for s in matchable_segments if s.get("source_chunk_id"))
    matched_prev = sum(
        1 for s in matchable_segments
        if s.get("source_match_method") == "prev_page_fallback"
    )
    payload["stats"]["segments_out"] = len(serialized_segments)
    payload["stats"]["edges_out"] = len(serialized_edges)
    payload["stats"]["derivation_refine"] = derivation_refine_stats
    payload["stats"]["solution_prune"] = solution_prune_stats
    payload["stats"]["question_prune"] = question_prune_stats
    payload["stats"]["source_matched"] = matched
    payload["stats"]["source_matched_prev_page"] = matched_prev
    payload["stats"]["source_match_rate"] = (matched / len(matchable_segments)) if matchable_segments else 0.0
    derivations = [s for s in serialized_segments if s.get("segment_type") == "derivation"]
    linked_derivations = sum(
        1 for s in derivations
        if (s.get("derived_to_formula_id") or (s.get("derived_from_formula_ids") or []) or (s.get("referenced_formula_ids") or []))
    )
    payload["stats"]["derivation_count"] = len(derivations)
    payload["stats"]["derivation_linked"] = linked_derivations
    payload["stats"]["derivation_link_coverage"] = (linked_derivations / len(derivations)) if derivations else 0.0
    payload["stats"]["worked_example_count"] = sum(1 for s in serialized_segments if s.get("segment_type") == "worked_example")
    payload["stats"]["calculation_count"] = sum(1 for s in serialized_segments if s.get("segment_type") == "calculation")
    payload["stats"]["concept_ref_count"] = len(payload["concept_refs"])
    payload["stats"]["segments_with_concept_links"] = sum(
        1 for s in serialized_segments
        if (s.get("concept_links") or [])
    )
    _write_sidecar(sidecar_path, payload)
    _write_sidecar(kg_sidecar_path, _build_kg_payload(payload))
    _write_formula_module_output(payload, doc_id=doc_id)
    return sidecar_path


def _load_synapta_components():
    root = Path(__file__).resolve().parents[1] / "synapta-formula-segmentation"
    if root.exists():
        sys.path.insert(0, str(root))
    try:
        from synapta_segmenter import Linker, TextBlockExtractor
        from segmenter.context import ContextProcessor
        from concept_linker import ConceptLinker
        from schemas import FormulaSegment
        return TextBlockExtractor, ContextProcessor, Linker, FormulaSegment, ConceptLinker
    except Exception as exc:
        logger.warning("Failed loading Synapta components: %s", exc)
        return None


def _write_formula_module_output(payload: Dict[str, Any], doc_id: str) -> None:
    """
    Keep a module-native output mirror compatible with
    synapta-formula-segmentation/outputs/*_segments.json.
    """
    out_dir = Path(__file__).resolve().parents[1] / "synapta-formula-segmentation" / "outputs"
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        return

    segs = list(payload.get("segments") or [])
    formulas = list(payload.get("formula_refs") or [])
    merged_segments = segs + formulas

    chapters_map: Dict[str, Dict[str, Any]] = {}
    for s in merged_segments:
        ch_num = str(s.get("chapter_number") or "Unknown")
        ch_title = s.get("chapter_title")
        if ch_num not in chapters_map:
            level = max(1, ch_num.count(".") + 1) if ch_num != "Unknown" else 1
            parent = None
            if ch_num != "Unknown" and "." in ch_num:
                parent = ch_num.rsplit(".", 1)[0]
            chapters_map[ch_num] = {
                "chapter_number": ch_num,
                "chapter_title": ch_title,
                "chapter_level": level,
                "parent_chapter": parent,
                "solutions_present": False,
                "solution_location": None,
            }
        if s.get("segment_type") == "solution":
            chapters_map[ch_num]["solutions_present"] = True
            chapters_map[ch_num]["solution_location"] = "in_chapter"
        if not chapters_map[ch_num].get("chapter_title") and ch_title:
            chapters_map[ch_num]["chapter_title"] = ch_title

    total_pages = 0
    for s in merged_segments:
        try:
            total_pages = max(total_pages, int(s.get("page_end") or s.get("page_start") or 0))
        except Exception:
            pass

    source_pdf = Path(__file__).resolve().parents[1] / "inputs" / f"{doc_id}.pdf"
    meta = {
        "source_pdf": str(source_pdf) if source_pdf.exists() else str(source_pdf),
        "total_pages": total_pages,
    }
    module_payload = {
        "metadata": meta,
        "chapters": sorted(
            chapters_map.values(),
            key=lambda x: (x["chapter_number"] == "Unknown", x["chapter_number"]),
        ),
        "segments": merged_segments,
        "edges": payload.get("edges") or [],
    }
    out_path = out_dir / f"{doc_id}_segments.json"
    try:
        _write_sidecar(out_path, module_payload)
    except Exception:
        pass


def _build_concept_linker(concept_linker_cls: Any, doc_id: str):
    concept_path = _resolve_concept_list_path(doc_id)
    if not concept_path:
        return None
    try:
        return concept_linker_cls(concept_list_path=str(concept_path))
    except Exception as exc:
        logger.warning("Failed creating ConceptLinker from %s: %s", concept_path, exc)
        return None


def _resolve_concept_list_path(doc_id: str) -> Optional[Path]:
    env_raw = (os.environ.get("MINERU_CONCEPT_LIST_PATH") or "").strip()
    if env_raw:
        env_path = Path(env_raw)
        if env_path.exists():
            return env_path
        logger.warning("MINERU_CONCEPT_LIST_PATH not found: %s", env_path)

    data_dir = Path(__file__).resolve().parents[1] / "synapta-formula-segmentation" / "data"
    if not data_dir.exists():
        return None

    files = sorted(
        [p for p in data_dir.glob("*.xlsx")] +
        [p for p in data_dir.glob("*.tsv")]
    )
    if not files:
        return None

    doc_low = (doc_id or "").lower()
    for p in files:
        if doc_low and doc_low in p.stem.lower():
            return p
    return files[0]


def _build_concept_node(
    concept_id: str,
    concept_name: Optional[str],
    level: Optional[int],
    tags: List[str],
    rationale: Optional[str],
) -> Dict[str, Any]:
    return {
        "segment_id": concept_id,
        "segment_type": "concept",
        "concept_id": concept_id,
        "concept_name": concept_name,
        "level": level,
        "tags": tags or [],
        "rationale": rationale,
    }


def _build_formula_segments_from_chunks(chunks: List[Dict[str, Any]], formula_segment_cls: Any, doc_id: str) -> List[Any]:
    out: List[Any] = []
    seen: Set[str] = set()
    for chunk in chunks:
        if chunk.get("type") != "formula":
            continue
        item = chunk.get("synapta_formula")
        if not isinstance(item, dict):
            continue
        seg_id = item.get("segment_id")
        if not seg_id or seg_id in seen:
            continue
        seen.add(seg_id)
        if not item.get("book_id"):
            item["book_id"] = doc_id
        try:
            out.append(formula_segment_cls(**item))
        except Exception:
            # Skip malformed formula payloads without interrupting QA flow.
            continue
    return out


def _formula_source_by_id(chunks: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for chunk in chunks:
        if chunk.get("type") != "formula":
            continue
        item = chunk.get("synapta_formula")
        if not isinstance(item, dict):
            continue
        seg_id = item.get("segment_id")
        if not seg_id:
            continue
        out[str(seg_id)] = item
    return out


def _backfill_formula_ref_fields(
    formula_refs: List[Dict[str, Any]],
    source_by_id: Dict[str, Dict[str, Any]],
) -> None:
    # Bridge-level guardrail: keep formula_refs consistent with source formula chunks.
    fill_keys = [
        "formula_latex",
        "equation_number",
        "short_meaning",
        "usage_type",
        "chapter_number",
        "chapter_title",
    ]
    for ref in formula_refs:
        rid = str(ref.get("segment_id") or "")
        src = source_by_id.get(rid)
        if not src:
            continue
        for key in fill_keys:
            if ref.get(key) in (None, "", []):
                src_val = src.get(key)
                if src_val not in (None, "", []):
                    ref[key] = src_val


def _select_candidate_chunks(chunks: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
    candidates: List[Dict[str, Any]] = []
    skipped_no_bbox = 0
    hint_roles = {
        "question_candidate",
        "solution_candidate",
        "derivation_candidate",
        "calculation_candidate",
        "worked_example_candidate",
    }
    for chunk in chunks:
        if chunk.get("type") != "text":
            continue
        text = (chunk.get("content") or "").strip()
        if len(text) < 8:
            continue
        # Never feed obvious heading-like lines into Synapta QA extraction.
        if bool(chunk.get("is_heading_like")) or _looks_like_heading_line(text):
            continue

        role = (chunk.get("candidate_role") or "").strip().lower()
        seg_type = (chunk.get("segment_type") or "").strip().lower()
        hinted = role in hint_roles or seg_type in hint_roles
        if not hinted and not _is_candidate_text(text):
            continue

        bbox = chunk.get("bbox")
        if not bbox or len(bbox) < 4:
            skipped_no_bbox += 1
            bbox = None

        pages = _chunk_pages_1based(chunk)
        candidates.append({
            "id": chunk.get("id"),
            "heading_path": chunk.get("heading_path"),
            "bbox": bbox,
            "pages": pages,
            "text": text,
            "qa_zone_type": chunk.get("qa_zone_type"),
            "candidate_role": role or seg_type,
            "numbering": chunk.get("numbering"),
            "chapter_main": chunk.get("chapter_main"),
        })
    return candidates, skipped_no_bbox


def _is_candidate_text(text: str) -> bool:
    return any(p.search(text) for p in _CANDIDATE_PATTERNS)


def _looks_like_heading_line(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    if re.match(r'^\d+(?:\.\d+)+\s+[A-Z]', t):
        return True
    words = re.findall(r"[A-Za-z]+", t)
    if len(words) <= 8 and t[:1].isdigit() and ":" not in t and "=" not in t:
        if not re.search(r"\b(solution|answer|therefore|thus|we\s+find|we\s+get)\b", t, re.IGNORECASE):
            return True
    return False


def _chunk_pages_1based(chunk: Dict[str, Any]) -> List[int]:
    page_range = chunk.get("page_range")
    if isinstance(page_range, list) and page_range:
        pages = []
        for page in page_range:
            if isinstance(page, int):
                pages.append(page + 1)
        if pages:
            return sorted(set(pages))

    page_span = chunk.get("page_span")
    if isinstance(page_span, list) and len(page_span) == 2:
        start, end = page_span
        if isinstance(start, int) and isinstance(end, int):
            if end < start:
                start, end = end, start
            return list(range(start + 1, end + 2))
    return [1]


def _expand_candidate_pages(
    candidates: List[Dict[str, Any]],
    blocks: List[ContentBlock],
    page_sizes: Dict[int, Tuple[float, float]],
) -> Set[int]:
    pages: Set[int] = set()
    for c in candidates:
        for p in c.get("pages", []):
            pages.add(p)

    max_page = 0
    if blocks:
        max_page = max(max_page, max(b.page_idx + 1 for b in blocks))
    if page_sizes:
        max_page = max(max_page, max(page_sizes.keys()) + 1)
    if max_page <= 0:
        max_page = max(pages) if pages else 1

    expanded = set(pages)
    for p in pages:
        if p + 1 <= max_page:
            expanded.add(p + 1)
    return expanded


def _blocks_to_fitz_tuples(
    blocks: List[ContentBlock],
    candidate_pages: Set[int],
    candidates: Optional[List[Dict[str, Any]]] = None,
) -> Dict[int, List[Tuple[float, float, float, float, str, int, int]]]:
    by_page: Dict[int, List[Tuple[float, float, float, float, str, int, int]]] = {}
    per_page_counter: Dict[int, int] = {}
    candidate_regions = _candidate_regions_by_page(candidates or [])
    pages_with_unbounded = {
        p for p, regs in candidate_regions.items()
        if any(r is None for r in regs)
    }

    for b in blocks:
        page_num = b.page_idx + 1
        if page_num not in candidate_pages:
            continue
        if not b.bbox:
            continue
        text = (b.text or "").strip()
        if not text:
            continue
        if candidate_regions:
            if page_num not in pages_with_unbounded:
                regs = [r for r in candidate_regions.get(page_num, []) if r is not None]
                if regs:
                    b_rect = (float(b.bbox.x0), float(b.bbox.y0), float(b.bbox.x1), float(b.bbox.y1))
                    if not any(_rect_overlap_or_near(b_rect, r, margin=140.0) for r in regs):
                        continue

        block_type = 0 if b.type in _TEXT_BLOCK_TYPES else 1
        index = b.metadata.get("index")
        if isinstance(index, int):
            block_no = index
        else:
            block_no = per_page_counter.get(page_num, 0)
            per_page_counter[page_num] = block_no + 1

        tup = (
            float(b.bbox.x0),
            float(b.bbox.y0),
            float(b.bbox.x1),
            float(b.bbox.y1),
            text,
            int(block_no),
            int(block_type),
        )
        by_page.setdefault(page_num, []).append(tup)

    for page_num, page_blocks in by_page.items():
        page_blocks.sort(key=lambda x: (x[5], x[1], x[0]))
    return by_page


def _build_dummy_page(width: float, height: float) -> Any:
    safe_w = float(width) if width and width > 0 else 1000.0
    safe_h = float(height) if height and height > 0 else 1400.0
    return SimpleNamespace(rect=SimpleNamespace(width=safe_w, height=safe_h))


def _page_section_hints(chunks: List[Dict[str, Any]]) -> Dict[int, Optional[str]]:
    # Build a best-effort page -> section hint from MinerU qa_zone_type.
    scores: Dict[int, Dict[str, int]] = {}
    zone_score = {
        "concept_check_solution": 5,
        "concept_check": 3,
        "problem_set": 2,
        "other": 1,
    }
    for ch in chunks:
        if ch.get("type") != "text":
            continue
        pages = _chunk_pages_1based(ch)
        if not pages:
            continue
        zone = str(ch.get("qa_zone_type") or "other").lower()
        if zone not in zone_score:
            zone = "other"
        weight = zone_score[zone]
        # Solution/Question candidates provide stronger hints.
        role = str(ch.get("candidate_role") or "").lower()
        if role == "solution_candidate":
            weight += 3
        elif role == "question_candidate":
            weight += 1
        for p in pages:
            scores.setdefault(int(p), {})
            scores[int(p)][zone] = scores[int(p)].get(zone, 0) + weight

    out: Dict[int, Optional[str]] = {}
    for p, s in scores.items():
        zone = max(s.items(), key=lambda x: x[1])[0]
        out[p] = _zone_to_synapta_section(zone)
    return out


def _zone_to_synapta_section(zone: str) -> Optional[str]:
    z = (zone or "").lower()
    if z == "concept_check_solution":
        return "concept_check_solutions"
    if z == "concept_check":
        return "concept_check"
    if z == "problem_set":
        return "problem_set"
    return None


def _apply_page_section_hint(extractor: Any, page_num: int, page_hints: Dict[int, Optional[str]]) -> None:
    if not hasattr(extractor, "current_section"):
        return
    hint = page_hints.get(int(page_num))
    if hint is not None:
        extractor.current_section = hint


def _to_dict(item: Any) -> Dict[str, Any]:
    if hasattr(item, "model_dump"):
        return item.model_dump()
    if isinstance(item, dict):
        return item
    return dict(getattr(item, "__dict__", {}))


def _candidate_regions_by_page(
    candidates: List[Dict[str, Any]]
) -> Dict[int, List[Optional[Tuple[float, float, float, float]]]]:
    out: Dict[int, List[Optional[Tuple[float, float, float, float]]]] = {}
    for c in candidates:
        pages = c.get("pages") or []
        cb = c.get("bbox")
        rect: Optional[Tuple[float, float, float, float]] = None
        if cb and len(cb) >= 4:
            rect = (float(cb[0]), float(cb[1]), float(cb[2]), float(cb[3]))
        for p in pages:
            out.setdefault(int(p), []).append(rect)
    return out


def _rect_overlap_or_near(
    a: Tuple[float, float, float, float],
    b: Tuple[float, float, float, float],
    margin: float = 100.0,
) -> bool:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    if not (ax1 < bx0 or bx1 < ax0 or ay1 < by0 or by1 < ay0):
        return True
    expanded_b = (bx0 - margin, by0 - margin, bx1 + margin, by1 + margin)
    ex0, ey0, ex1, ey1 = expanded_b
    return not (ax1 < ex0 or ex1 < ax0 or ay1 < ey0 or ey1 < ay0)


def _derivation_quality_warnings(segment: Dict[str, Any]) -> List[str]:
    if segment.get("segment_type") != "derivation":
        return []

    text = (segment.get("text_content") or "").strip()
    low = text.lower()
    steps = segment.get("steps") or []
    warnings: List[str] = []

    has_eq = "=" in text
    has_eq_ref = bool(re.search(r'\b(?:eq\.?|equation)\s*\(?\d+(?:\.\d+)*\)?', low))
    has_transform = bool(re.search(r'\b(?:substitut|rearrang|derive|solve\s+for)\b', low))

    if len(steps) < 2:
        warnings.append("derivation_low_step_evidence")
    if not (has_eq or has_eq_ref):
        warnings.append("derivation_missing_math_anchor")
    if not has_transform:
        warnings.append("derivation_missing_transform_verb")
    return warnings


def _refine_derivation_segments(
    segments: List[Dict[str, Any]]
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """
    Tighten derivation precision for Formula.md alignment:
    - keep derivation only with transform evidence + math anchor;
    - downgrade weak math-heavy derivation to calculation;
    - drop narrative-only derivation noise.
    """
    out: List[Dict[str, Any]] = []
    stats = {"kept": 0, "downgraded_to_calculation": 0, "dropped_weak": 0}
    for seg in segments:
        if seg.get("segment_type") != "derivation":
            out.append(seg)
            continue

        text = (seg.get("text_content") or "").strip()
        low = text.lower()
        steps = seg.get("steps") or []
        if not isinstance(steps, list):
            steps = []

        has_transform = bool(
            re.search(
                r'\b(?:derive(?:d|s|ing)?|derivation|proof|substitut(?:e|ed|ing|ion)|'
                r'rearrang(?:e|ed|ing)|solve\s+for|rewrite|differentiat(?:e|ed|ing|ion)|'
                r'integrat(?:e|ed|ing|ion)|by\s+definition)\b',
                low,
            )
        )
        eq_ref = bool(re.search(r'\b(?:eq\.?|equation)\s*\(?\d+(?:\.\d+)*\)?', low))
        eq_count = text.count("=")
        has_math_anchor = eq_ref or eq_count > 0 or ("->" in text) or ("=>" in text) or ("⇒" in text)
        step_count = len([s for s in steps if str(s).strip()])

        if has_transform and has_math_anchor:
            stats["kept"] += 1
            out.append(seg)
            continue

        if has_math_anchor and (step_count >= 2 or eq_count >= 2):
            new_seg = dict(seg)
            new_seg["segment_type"] = "calculation"
            calc_steps = steps or _fallback_solution_steps_from_text(text)
            new_seg["steps"] = calc_steps[:12]
            # Remove derivation-only keys after downgrade.
            new_seg.pop("derived_to_formula_id", None)
            new_seg.pop("derived_from_formula_ids", None)
            new_seg.pop("link_type", None)
            warns = list(new_seg.get("quality_warnings") or [])
            if "downgraded_from_derivation" not in warns:
                warns.append("downgraded_from_derivation")
            new_seg["quality_warnings"] = warns
            stats["downgraded_to_calculation"] += 1
            out.append(new_seg)
            continue

        stats["dropped_weak"] += 1

    return out, stats


def _is_false_solution_segment(segment: Dict[str, Any]) -> bool:
    if segment.get("segment_type") != "solution":
        return False
    text = (segment.get("text_content") or "").strip()
    if not text:
        return True
    low = text.lower()
    words = re.findall(r"[A-Za-z]+", text)
    has_solution_cue = bool(
        re.search(r"\b(solution|answer|therefore|thus|we\s+find|we\s+get)\b", low)
    )
    heading_like = _looks_like_heading_line(text)
    very_short = len(words) <= 8
    if (heading_like or very_short) and not has_solution_cue and "=" not in text:
        return True
    return False


def _is_obvious_heading_misclassified_solution(segment: Dict[str, Any]) -> bool:
    if segment.get("segment_type") != "solution":
        return False
    text = (segment.get("text_content") or "").strip()
    if not text:
        return False
    # Canonical chapter/section heading format, e.g. "2.1 The Money Market".
    if not re.match(r'^\s*\d+(?:\.\d+)+\s+[A-Z]', text):
        return False
    role = (segment.get("source_chunk_candidate_role") or "").lower()
    if role == "solution_candidate":
        return False
    return True


def _annotate_qa_keys_from_source(
    segments: List[Dict[str, Any]],
    chunks: List[Dict[str, Any]],
) -> None:
    chunk_by_id = {
        str(c.get("id")): c
        for c in chunks
        if c.get("id") is not None
    }
    question_chapter_hints = _question_chapter_hints_by_page(segments)
    for s in segments:
        st = s.get("segment_type")
        if st not in {"question", "solution"}:
            continue
        src_id = s.get("source_chunk_id")
        src = chunk_by_id.get(str(src_id)) if src_id else None
        numbering = (src or {}).get("numbering") or _extract_numbering_from_text(s.get("text_content") or "")
        qnum = ""
        if isinstance(numbering, dict):
            qnum = str(numbering.get("normalized") or numbering.get("raw") or "").strip()
        qnum = _normalize_qnum(qnum)
        chapter_main = (src or {}).get("chapter_main") or _chapter_main_from_segment(s)
        if not chapter_main or str(chapter_main).strip().lower() in {"na", "unknown", "none"}:
            chapter_main = _nearest_question_chapter_hint(s, question_chapter_hints) or chapter_main
        zone = (src or {}).get("qa_zone_type") or _zone_from_segment(s)

        key = _build_qa_key(chapter_main, zone, qnum)
        if st == "question":
            s["question_key"] = key
        else:
            s["solution_key"] = key


def _append_mineru_solution_supplements(
    segments: List[Dict[str, Any]],
    chunks: List[Dict[str, Any]],
    doc_id: str,
) -> None:
    existing_source_ids = {
        str(s.get("source_chunk_id"))
        for s in segments
        if s.get("segment_type") == "solution" and s.get("source_chunk_id")
    }
    existing_seg_ids = {str(s.get("segment_id")) for s in segments if s.get("segment_id")}
    for ch in chunks:
        if ch.get("type") != "text":
            continue
        cid = str(ch.get("id") or "")
        if not cid or cid in existing_source_ids:
            continue
        text = (ch.get("content") or "").strip()
        if not text:
            continue
        if not _is_mineru_answer_like_chunk(ch):
            continue

        page_1 = _chunk_pages_1based(ch)
        page_start = page_1[0] if page_1 else 1
        page_end = page_1[-1] if page_1 else page_start
        seg_id = f"mineru_solution_{cid}"
        if seg_id in existing_seg_ids:
            continue
        seg = {
            "segment_id": seg_id,
            "segment_type": "solution",
            "book_id": doc_id,
            "chapter_number": str(ch.get("chapter_main") or "Unknown"),
            "chapter_title": None,
            "page_start": page_start,
            "page_end": page_end,
            "bbox": _bbox_to_obj(ch.get("bbox"), page_start),
            "text_content": text,
            "solution_steps": _fallback_solution_steps_from_text(text),
            "referenced_formula_ids": [],
            "concept_links": [],
            "context_before": None,
            "context_after": None,
            "heading_path": ch.get("heading_path"),
            "prev_segment_id": None,
            "next_segment_id": None,
            "doc_uri": None,
            "needs_human_review": True,
            "source_chunk_id": cid,
            "source_chunk_heading_path": ch.get("heading_path"),
            "source_match_method": "source_chunk_seeded",
            "source_chunk_candidate_role": ch.get("candidate_role"),
            "source_chunk_qa_zone_type": ch.get("qa_zone_type"),
        }
        segments.append(seg)
        existing_seg_ids.add(seg_id)


def _is_mineru_answer_like_chunk(ch: Dict[str, Any]) -> bool:
    zone = str(ch.get("qa_zone_type") or "").lower()
    role = str(ch.get("candidate_role") or "").lower()
    text = (ch.get("content") or "").strip()
    if not text:
        return False
    if _looks_like_heading_line(text):
        return False
    # Only seed from concept-check areas. Problem sets are question-heavy and
    # introduce too many false positive "solutions".
    if zone not in {"concept_check", "concept_check_solution"}:
        return False
    # Do not treat explicit question lines as solutions.
    if "?" in text:
        return False
    if role == "solution_candidate":
        return True
    if not re.match(r'^\s*\d+\s*[.)]', text):
        return False
    has_answer_cue = bool(re.search(r'\b(therefore|thus|hence|we\s+find|we\s+get|answer)\b', text, re.IGNORECASE))
    has_math = ("=" in text) or bool(re.search(r'\b\d+(?:\.\d+)?\s*[%$]\b', text))
    long_enough = len(text.split()) >= 14
    return has_answer_cue or (has_math and long_enough)


def _fallback_solution_steps_from_text(text: str) -> List[str]:
    lines = [ln.strip() for ln in re.split(r'\n+', text) if ln.strip()]
    if not lines:
        return []
    numbered = [ln for ln in lines if re.match(r'^\s*[a-z]?[.)]?\s*\d*', ln, re.IGNORECASE)]
    if numbered:
        return numbered[:12]
    sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', text) if s.strip()]
    return sentences[:10]


def _bbox_to_obj(bbox: Any, page: int) -> Optional[Dict[str, Any]]:
    if not bbox or not isinstance(bbox, list) or len(bbox) < 4:
        return None
    return {
        "page": int(page),
        "x0": float(bbox[0]),
        "y0": float(bbox[1]),
        "x1": float(bbox[2]),
        "y1": float(bbox[3]),
    }


def _rewrite_answer_edges(
    segments: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    kept_non_answer = [e for e in edges if e.get("edge_type") != "ANSWER_OF"]
    questions = [s for s in segments if s.get("segment_type") == "question"]
    solutions = [s for s in segments if s.get("segment_type") == "solution"]
    if not questions or not solutions:
        return kept_non_answer

    q_by_key: Dict[str, List[Dict[str, Any]]] = {}
    for q in questions:
        key = q.get("question_key")
        if key:
            q_by_key.setdefault(str(key), []).append(q)

    assigned_q: Set[str] = set()
    answer_edges: List[Dict[str, Any]] = []
    primary_solutions = [s for s in solutions if _is_pairable_solution(s)]
    fallback_solutions = [s for s in solutions if not _is_pairable_solution(s)]
    for sol in sorted(primary_solutions + fallback_solutions, key=lambda x: int(x.get("page_start") or 0)):
        sid = sol.get("segment_id")
        if not sid:
            continue
        best_q = None
        method = None
        skey = sol.get("solution_key")
        if skey and skey in q_by_key:
            cands = q_by_key[skey]
            cands = [q for q in cands if q.get("segment_id") not in assigned_q]
            if cands:
                best_q = _nearest_page_candidate(sol, cands)
                method = "source_key_exact"
        if best_q is None:
            zone = _zone_from_segment(sol)
            chapter = _chapter_main_from_segment(sol)
            pool = [
                q for q in questions
                if q.get("segment_id") not in assigned_q
                and _zones_compatible(_zone_from_segment(q), zone)
                and _chapter_main_from_segment(q) == chapter
            ]
            if pool:
                best_q = _nearest_page_candidate(sol, pool, max_page_delta=25)
                method = "chapter_zone_nearest" if best_q else None
        if best_q is None:
            chapter = _chapter_main_from_segment(sol)
            sq = _qnum_from_key(sol.get("solution_key"))
            pool = [
                q for q in questions
                if q.get("segment_id") not in assigned_q
                and _chapter_main_from_segment(q) == chapter
                and _qnum_suffix_matches(_qnum_from_key(q.get("question_key")), sq)
            ]
            if pool:
                best_q = _nearest_page_candidate(sol, pool, max_page_delta=80)
                method = "chapter_suffix_match" if best_q else None
        if best_q is None:
            # Handle concept-check answer pages keyed as concept_check_solution.
            chapter = _chapter_main_from_segment(sol)
            sq = _qnum_from_key(sol.get("solution_key"))
            pool = [
                q for q in questions
                if q.get("segment_id") not in assigned_q
                and _chapter_main_from_segment(q) == chapter
                and _qnum_suffix_matches(_qnum_from_key(q.get("question_key")), sq)
            ]
            if pool:
                best_q = _nearest_page_candidate(sol, pool, max_page_delta=160)
                method = "chapter_qnum_exact" if best_q else None
        if best_q is None:
            zone = _zone_from_segment(sol)
            sq = _qnum_from_key(sol.get("solution_key"))
            pool = [
                q for q in questions
                if q.get("segment_id") not in assigned_q
                and _zones_compatible(_zone_from_segment(q), zone)
                and _qnum_suffix_matches(_qnum_from_key(q.get("question_key")), sq)
            ]
            if pool:
                best_q = _nearest_page_candidate(sol, pool, max_page_delta=120)
                method = "zone_suffix_nearest" if best_q else None
        if best_q is None:
            chapter = _chapter_main_from_segment(sol)
            pool = [
                q for q in questions
                if q.get("segment_id") not in assigned_q
                and _chapter_main_from_segment(q) == chapter
            ]
            if pool:
                best_q = _nearest_page_candidate(sol, pool, max_page_delta=80)
                method = "chapter_nearest" if best_q else None
        if best_q is None:
            continue
        qid = best_q.get("segment_id")
        if not qid:
            continue
        assigned_q.add(qid)
        sol["solution_for_question_id"] = qid
        answer_edges.append({
            "edge_id": f"answer_of_{sid}_{qid}",
            "source_id": sid,
            "target_id": qid,
            "edge_type": "ANSWER_OF",
            "strength": 1.0 if method == "source_key_exact" else 0.85,
            "link_method": "exact" if method == "source_key_exact" else "heuristic",
            "anchor_metadata": {
                "method": method,
                "page": sol.get("page_start"),
                "snippet": _truncate_text(sol.get("text_content") or "", 180),
            },
        })
    return kept_non_answer + answer_edges


def _build_qa_key(chapter_main: str, zone: str, qnum: str) -> str:
    return f"{chapter_main or 'na'}|{zone or 'other'}|{qnum or 'na'}"


def _qnum_from_key(key: Optional[str]) -> str:
    if not key:
        return ""
    parts = str(key).split("|")
    if len(parts) != 3:
        return ""
    return parts[2]


def _qnum_suffix_matches(question_num: str, solution_num: str) -> bool:
    q = (question_num or "").strip()
    s = (solution_num or "").strip()
    if not q or not s or q == "na" or s == "na":
        return False
    if q == s:
        return True
    q_parts = q.split(".")
    s_parts = s.split(".")
    return bool(q_parts and s_parts and q_parts[-1] == s_parts[-1])


def _zones_compatible(q_zone: str, s_zone: str) -> bool:
    qz = (q_zone or "").strip().lower()
    sz = (s_zone or "").strip().lower()
    if qz == sz:
        return True
    pair = {qz, sz}
    if pair == {"concept_check", "concept_check_solution"}:
        return True
    return False


def _prune_edges_by_known_ids(
    edges: List[Dict[str, Any]],
    segment_ids: Set[str],
    formula_ids: Set[str],
    concept_ids: Set[str],
) -> List[Dict[str, Any]]:
    known = set(segment_ids) | set(formula_ids) | set(concept_ids)
    out: List[Dict[str, Any]] = []
    for e in edges:
        src = str(e.get("source_id") or "")
        tgt = str(e.get("target_id") or "")
        if src in known and tgt in known:
            out.append(e)
    return out


def _build_reference_stubs(
    segments: List[Dict[str, Any]],
    doc_id: str,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    stubs: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []
    seen: Set[Tuple[str, str, str]] = set()
    patterns = [
        (re.compile(r'\b(Table)\s+([A-Za-z]?\d+(?:\.\d+)*)\b', re.IGNORECASE), "table"),
        (re.compile(r'\b(Figure)\s+([A-Za-z]?\d+(?:\.\d+)*)\b', re.IGNORECASE), "figure"),
        (re.compile(r'\b(Appendix)\s+([A-Z]|\d+)\b', re.IGNORECASE), "appendix"),
        (re.compile(r'\b(Section)\s+([A-Za-z]?\d+(?:\.\d+)*)\b', re.IGNORECASE), "section"),
    ]

    for src in segments:
        src_id = str(src.get("segment_id") or "")
        text = str(src.get("text_content") or "")
        if not src_id or not text:
            continue
        for pat, ref_type in patterns:
            for m in pat.finditer(text):
                ref_id = str(m.group(2))
                key = (src_id, ref_type, ref_id)
                if key in seen:
                    continue
                seen.add(key)
                stub_id = f"reference_stub_{ref_type}_{ref_id}_{uuid.uuid4().hex[:8]}"
                snippet_start = max(0, m.start() - 80)
                snippet_end = min(len(text), m.end() + 80)
                snippet = " ".join(text[snippet_start:snippet_end].split())[:220]
                stub = {
                    "segment_id": stub_id,
                    "segment_type": "reference_stub",
                    "book_id": src.get("book_id") or doc_id,
                    "chapter_number": src.get("chapter_number") or "unknown",
                    "chapter_title": src.get("chapter_title"),
                    "page_start": src.get("page_start"),
                    "page_end": src.get("page_end"),
                    "bbox": src.get("bbox"),
                    "text_content": f"{ref_type.title()} {ref_id}",
                    "context_before": None,
                    "context_after": None,
                    "concept_links": [],
                    "heading_path": src.get("heading_path"),
                    "prev_segment_id": None,
                    "next_segment_id": None,
                    "doc_uri": src.get("doc_uri"),
                    "needs_human_review": True,
                    "ref_type": ref_type,
                    "ref_id_text": ref_id,
                    "target_unknown": True,
                    "source_segment_id": src_id,
                    "snippet": snippet,
                }
                stubs.append(stub)
                edges.append({
                    "edge_id": f"ref_{src_id}_{stub_id}",
                    "source_id": src_id,
                    "target_id": stub_id,
                    "edge_type": "REFERENCES",
                    "strength": 0.8,
                    "link_method": "heuristic",
                    "anchor_metadata": {
                        "method": "reference_stub_regex",
                        "page": src.get("page_start"),
                        "snippet": snippet,
                    },
                })

    return stubs, edges


def _annotate_question_solution_status(
    segments: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
) -> None:
    linked_qids: Set[str] = {
        str(e.get("target_id"))
        for e in edges
        if e.get("edge_type") == "ANSWER_OF" and e.get("target_id")
    }
    for s in segments:
        if s.get("segment_type") != "question":
            continue
        sid = str(s.get("segment_id") or "")
        if sid in linked_qids:
            s["solution_status"] = "linked"
        else:
            s["solution_status"] = "not_found_in_book"


def _prune_noisy_questions(
    segments: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, int]]:
    stats = {"dropped_placeholder_question": 0}
    kept: List[Dict[str, Any]] = []
    drop_ids: Set[str] = set()

    for s in segments:
        if s.get("segment_type") != "question":
            kept.append(s)
            continue
        text = (s.get("text_content") or "").strip()
        low = text.lower()
        words = re.findall(r"[A-Za-z]+", text)
        is_placeholder = False
        if re.match(r'^\s*concept\s+check\s+\d+(?:\.\d+)*\s*$', low):
            is_placeholder = True
        if re.match(r'^\s*\d+\s*[.)]\s*[A-Za-z]{1,12}\s*$', text):
            is_placeholder = True
        if len(words) <= 2 and "?" not in text and not re.search(r'\b(calculate|compute|determine|find|explain|discuss)\b', low):
            is_placeholder = True
        if is_placeholder:
            qid = str(s.get("segment_id") or "")
            if qid:
                drop_ids.add(qid)
                stats["dropped_placeholder_question"] += 1
            continue
        kept.append(s)

    if not drop_ids:
        return segments, edges, stats

    kept_edges: List[Dict[str, Any]] = []
    for e in edges:
        src = str(e.get("source_id") or "")
        tgt = str(e.get("target_id") or "")
        if src in drop_ids or tgt in drop_ids:
            continue
        kept_edges.append(e)
    return kept, kept_edges, stats


def _prune_unlinked_noisy_solutions(
    segments: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, int]]:
    answer_edges = [e for e in edges if e.get("edge_type") == "ANSWER_OF"]
    linked_solution_ids: Set[str] = {
        str(e.get("source_id"))
        for e in answer_edges
        if e.get("source_id")
    }

    kept: List[Dict[str, Any]] = []
    stats = {
        "dropped_unlinked_seeded": 0,
        "dropped_unlinked_question_like": 0,
        "dropped_unlinked_heading_like": 0,
    }
    for s in segments:
        if s.get("segment_type") != "solution":
            kept.append(s)
            continue
        sid = str(s.get("segment_id") or "")
        if sid in linked_solution_ids:
            kept.append(s)
            continue

        role = str(s.get("source_chunk_candidate_role") or "").lower()
        seeded = sid.startswith("mineru_solution_")
        text = (s.get("text_content") or "").strip()
        question_like = ("?" in text) or (role == "question_candidate") or _is_false_solution_segment(s)
        heading_like = _is_heading_like_solution_text(text)

        if seeded:
            stats["dropped_unlinked_seeded"] += 1
            continue
        if question_like:
            stats["dropped_unlinked_question_like"] += 1
            continue
        if heading_like:
            stats["dropped_unlinked_heading_like"] += 1
            continue
        kept.append(s)

    kept_ids = {str(s.get("segment_id")) for s in kept if s.get("segment_id")}
    kept_edges: List[Dict[str, Any]] = []
    for e in edges:
        src = str(e.get("source_id") or "")
        tgt = str(e.get("target_id") or "")
        if src in kept_ids or e.get("source_id") is None:
            if tgt in kept_ids or e.get("target_id") is None or e.get("edge_type") != "ANSWER_OF":
                kept_edges.append(e)
    return kept, kept_edges, stats


def _is_heading_like_solution_text(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    low = t.lower()
    # Typical chapter/section heading fragments mistakenly emitted as solution text.
    if re.match(r'^\s*\d+(?:\.\d+)+\s+[A-Z][A-Za-z].{0,120}$', t):
        if "?" not in t and "=" not in t and "therefore" not in low:
            return True
    if re.match(r'^\s*(chapter|ch)\s+\d+\b', low):
        return True
    return False


def _extract_numbering_from_text(text: str) -> Dict[str, Any]:
    t = (text or "").strip()
    if not t:
        return {}
    m = re.match(r'^\s*(?:concept\s+check\s+)?(?:q\s*)?(\d+(?:\.\d+)*)\b', t, re.IGNORECASE)
    if not m:
        return {}
    raw = m.group(1)
    return {"raw": raw, "normalized": _normalize_qnum(raw)}


def _normalize_qnum(qnum: str) -> str:
    return re.sub(r'[^0-9.]', '', (qnum or '').strip()).strip(".")


def _chapter_main_from_segment(seg: Dict[str, Any]) -> str:
    ch = str(seg.get("chapter_number") or "").strip()
    m = re.match(r'^\s*(\d+)', ch)
    if m:
        return m.group(1)
    hp = str(seg.get("heading_path") or "")
    m = re.search(r'(?:chapter|ch)\s+(\d+)', hp, re.IGNORECASE)
    if m:
        return m.group(1)
    m = re.search(r'(^| > )(\d+)(?:\.\d+)*', hp)
    return m.group(2) if m else "na"


def _zone_from_segment(seg: Dict[str, Any]) -> str:
    hp = str(seg.get("heading_path") or "").lower()
    if (
        "concept check solution" in hp
        or "solutions to concept checks" in hp
        or "solution to concept checks" in hp
        or "answers to concept checks" in hp
    ):
        return "concept_check_solution"
    if "concept check" in hp:
        return "concept_check"
    if any(k in hp for k in ["problem set", "exercise", "review question"]):
        return "problem_set"
    return "other"


def _nearest_page_candidate(
    source: Dict[str, Any],
    cands: List[Dict[str, Any]],
    max_page_delta: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    sp = int(source.get("page_start") or 0)
    best = None
    best_d = 10**9
    for c in cands:
        cp = int(c.get("page_start") or 0)
        d = abs(sp - cp)
        if d < best_d:
            best_d = d
            best = c
    if max_page_delta is not None and best is not None and best_d > max_page_delta:
        return None
    return best


def _question_chapter_hints_by_page(
    segments: List[Dict[str, Any]],
) -> List[Tuple[int, str]]:
    hints: List[Tuple[int, str]] = []
    for s in segments:
        if s.get("segment_type") != "question":
            continue
        chapter = _chapter_main_from_segment(s)
        if not chapter or chapter == "na":
            continue
        page = int(s.get("page_start") or 0)
        if page <= 0:
            continue
        hints.append((page, chapter))
    return hints


def _nearest_question_chapter_hint(
    segment: Dict[str, Any],
    hints: List[Tuple[int, str]],
    max_page_delta: int = 120,
) -> Optional[str]:
    if not hints:
        return None
    page = int(segment.get("page_start") or 0)
    if page <= 0:
        return None
    best_chapter: Optional[str] = None
    best_delta = 10**9
    for p, chapter in hints:
        d = abs(page - p)
        if d < best_delta:
            best_delta = d
            best_chapter = chapter
    if best_delta > max_page_delta:
        return None
    return best_chapter


def _truncate_text(text: str, n: int) -> str:
    t = " ".join((text or "").split())
    return t[:n] + ("..." if len(t) > n else "")


def _is_pairable_solution(seg: Dict[str, Any]) -> bool:
    text = (seg.get("text_content") or "").strip()
    if not text:
        return False
    if not _looks_like_heading_line(text):
        return True
    # If heading-like, only keep when explicit answer cues exist.
    return bool(re.search(r"\b(solution|answer|therefore|thus|we\s+find|we\s+get|hence)\b", text, re.IGNORECASE))


def _index_candidates_by_page(
    candidates: List[Dict[str, Any]],
    expanded_pages: Optional[Set[int]] = None,
) -> Dict[int, List[Dict[str, Any]]]:
    idx: Dict[int, List[Dict[str, Any]]] = {}
    for c in candidates:
        pages = set(c.get("pages", []))
        # Mirror candidate chunks onto the immediately following page if
        # that page is included in extraction scope.
        if expanded_pages:
            pages = set(p for p in pages if p in expanded_pages)
            for base_page in c.get("pages", []):
                next_page = base_page + 1
                if next_page in expanded_pages:
                    pages.add(next_page)

        for page in sorted(pages):
            idx.setdefault(page, []).append(c)
    return idx


def _match_source_chunk(seg: Any, candidate_index: Dict[int, List[Dict[str, Any]]]) -> Optional[Dict[str, Any]]:
    page = getattr(seg, "page_start", None)
    if page is None:
        return None
    candidates = candidate_index.get(page) or []
    page_fallback = False
    if not candidates and page > 1:
        candidates = candidate_index.get(page - 1) or []
        page_fallback = bool(candidates)
    if not candidates:
        return None

    seg_bbox = getattr(seg, "bbox", None)
    if seg_bbox is None:
        chosen = dict(candidates[0])
        chosen["match_method"] = "prev_page_fallback" if page_fallback else "first_page_candidate"
        return chosen

    seg_rect = (
        float(getattr(seg_bbox, "x0", 0.0)),
        float(getattr(seg_bbox, "y0", 0.0)),
        float(getattr(seg_bbox, "x1", 0.0)),
        float(getattr(seg_bbox, "y1", 0.0)),
    )

    best_overlap = 0.0
    best_candidate: Optional[Dict[str, Any]] = None
    for c in candidates:
        cb = c.get("bbox")
        if not cb:
            continue
        c_rect = (float(cb[0]), float(cb[1]), float(cb[2]), float(cb[3]))
        overlap = _overlap_ratio(seg_rect, c_rect)
        if overlap > best_overlap:
            best_overlap = overlap
            best_candidate = c

    if best_candidate is not None and best_overlap > 0:
        chosen = dict(best_candidate)
        chosen["match_method"] = "bbox_overlap"
        return chosen

    seg_center_y = (seg_rect[1] + seg_rect[3]) / 2.0
    nearest_dist = float("inf")
    nearest: Optional[Dict[str, Any]] = None
    for c in candidates:
        cb = c.get("bbox")
        if not cb:
            continue
        center_y = (float(cb[1]) + float(cb[3])) / 2.0
        dist = abs(center_y - seg_center_y)
        if dist < nearest_dist:
            nearest_dist = dist
            nearest = c

    if nearest is not None:
        chosen = dict(nearest)
        chosen["match_method"] = "prev_page_fallback" if page_fallback else "nearest_y_fallback"
        return chosen

    chosen = dict(candidates[0])
    chosen["match_method"] = "prev_page_fallback" if page_fallback else "first_page_candidate"
    return chosen


def _overlap_ratio(a: Tuple[float, float, float, float], b: Tuple[float, float, float, float]) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    inter_x0 = max(ax0, bx0)
    inter_y0 = max(ay0, by0)
    inter_x1 = min(ax1, bx1)
    inter_y1 = min(ay1, by1)
    if inter_x1 <= inter_x0 or inter_y1 <= inter_y0:
        return 0.0
    inter_area = (inter_x1 - inter_x0) * (inter_y1 - inter_y0)
    a_area = max((ax1 - ax0) * (ay1 - ay0), 1e-6)
    return inter_area / a_area


def _write_sidecar(path: Path, payload: Dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _build_kg_payload(qa_payload: Dict[str, Any]) -> Dict[str, Any]:
    segments = qa_payload.get("segments") or []
    formula_refs = qa_payload.get("formula_refs") or []
    concept_refs = qa_payload.get("concept_refs") or []
    edges = qa_payload.get("edges") or []

    nodes_by_id: Dict[str, Dict[str, Any]] = {}
    for n in segments + formula_refs + concept_refs:
        if not isinstance(n, dict):
            continue
        seg_id = n.get("segment_id")
        if not seg_id:
            continue
        nodes_by_id[seg_id] = dict(n)

    kg_edges: List[Dict[str, Any]] = []
    for e in edges:
        if not isinstance(e, dict):
            continue
        src = e.get("source_id")
        tgt = e.get("target_id")
        if not src or not tgt:
            continue
        kg_edges.append({
            "edge_id": e.get("edge_id"),
            "source_id": src,
            "target_id": tgt,
            "edge_type": e.get("edge_type"),
            "strength": e.get("strength", 1.0),
            "link_method": e.get("link_method"),
            "anchor_metadata": e.get("anchor_metadata") or {},
        })

    stats = dict(qa_payload.get("stats") or {})
    stats["node_count"] = len(nodes_by_id)
    stats["edge_count"] = len(kg_edges)
    type_counts: Dict[str, int] = {}
    for n in nodes_by_id.values():
        t = n.get("segment_type", "unknown")
        type_counts[t] = type_counts.get(t, 0) + 1
    stats["node_type_counts"] = type_counts

    return {
        "doc_id": qa_payload.get("doc_id"),
        "version": "kg-v1",
        "config": dict(qa_payload.get("config") or {}),
        "stats": stats,
        "nodes": list(nodes_by_id.values()),
        "edges": kg_edges,
    }
