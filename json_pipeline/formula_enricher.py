from pathlib import Path
from typing import Any, Dict, List, Optional
import sys


def enrich_formula_chunks(chunks: List[Dict[str, Any]], doc_id: str = "book") -> None:
    extractor = _load_formula_extractor()
    if extractor is None:
        return

    for chunk in chunks:
        if chunk.get("type") != "formula":
            continue
        formula_text = chunk.get("content") or ""
        if not formula_text.strip():
            continue
        bbox = _bbox_from_chunk(chunk)
        formula_data = extractor(
            formula_text=formula_text,
            page_number=_page_number(chunk),
            book_id=doc_id,
            heading_path=chunk.get("heading_path"),
            bbox=bbox,
        )
        if formula_data:
            chunk["formula_data"] = formula_data


def _load_formula_extractor():
    root = Path(__file__).resolve().parents[1] / "synapta-formula-segmentation"
    if root.exists():
        sys.path.insert(0, str(root))
    try:
        from formula_item_extractor import extract_formula_item
        return extract_formula_item
    except Exception:
        return None


def _page_number(chunk: Dict[str, Any]) -> int:
    span = chunk.get("page_span") or []
    if span and isinstance(span, list):
        return int(span[0]) + 1 if span else 1
    return 1


def _bbox_from_chunk(chunk: Dict[str, Any]) -> Optional[Dict[str, float]]:
    bbox = chunk.get("bbox")
    if not bbox or len(bbox) < 4:
        return None
    return {"x0": bbox[0], "y0": bbox[1], "x1": bbox[2], "y1": bbox[3]}
