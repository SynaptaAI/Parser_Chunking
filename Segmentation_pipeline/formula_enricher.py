from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
import sys

from .enricher_utils import enrich_anchor, set_enrichment_status


def enrich_formula_chunks(chunks: List[Dict[str, Any]], doc_id: str = "book") -> None:
    extractor = _load_formula_extractor()
    if extractor is None:
        for chunk in chunks:
            if chunk.get("type") == "formula":
                set_enrichment_status(chunk, "formula", "skipped", "synapta_formula_unavailable")
        return

    for chunk in chunks:
        if chunk.get("type") != "formula":
            continue
        formula_text = chunk.get("content") or ""
        if not formula_text.strip():
            set_enrichment_status(chunk, "formula", "skipped", "empty_formula_text")
            continue
        anchor = enrich_anchor(chunk, doc_id)
        try:
            synapta_formula = extractor(
                formula_text=formula_text,
                page_number=anchor["page_start"],
                book_id=doc_id,
                heading_path=anchor["heading_path"],
                bbox=anchor["bbox"],
                chapter_number=anchor["chapter_number"],
                chapter_title=anchor["chapter_title"],
            )
        except Exception as exc:
            set_enrichment_status(chunk, "formula", "error", f"extract_failed:{type(exc).__name__}")
            continue

        if synapta_formula:
            synapta_formula["source_chunk_id"] = anchor["source_chunk_id"]
            synapta_formula["page_start"] = anchor["page_start"]
            synapta_formula["page_end"] = anchor["page_end"]
            synapta_formula["heading_path"] = anchor["heading_path"]
            if anchor["chapter_number"] and synapta_formula.get("chapter_number") in (None, "", "unknown"):
                synapta_formula["chapter_number"] = anchor["chapter_number"]
            if anchor["chapter_title"] and not synapta_formula.get("chapter_title"):
                synapta_formula["chapter_title"] = anchor["chapter_title"]
            chunk["synapta_formula"] = synapta_formula
            set_enrichment_status(chunk, "formula", "ok")
        else:
            set_enrichment_status(chunk, "formula", "empty", "no_formula_payload")


def _load_formula_extractor() -> Optional[Callable[..., Any]]:
    root = Path(__file__).resolve().parents[1] / "synapta-formula-segmentation"
    if root.exists():
        sys.path.insert(0, str(root))
    try:
        from formula_item_extractor import extract_formula_item
        return extract_formula_item
    except Exception:
        return None
