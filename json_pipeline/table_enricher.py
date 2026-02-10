from pathlib import Path
from typing import Any, Dict, List, Optional
import sys


def enrich_table_chunks(chunks: List[Dict[str, Any]]) -> None:
    extractor = _load_table_extractor()
    if extractor is None:
        return

    for chunk in chunks:
        if chunk.get("type") != "table":
            continue
        image_path = _pick_local_image_path(chunk.get("image_paths") or [])
        if not image_path:
            continue
        table_data = extractor(
            image_path=image_path,
            page_number=_page_number(chunk),
            caption=chunk.get("caption") or "",
        )
        if table_data:
            chunk["table_data"] = table_data


def _load_table_extractor():
    root = Path(__file__).resolve().parents[1] / "synapta-table-segmentation"
    if root.exists():
        sys.path.insert(0, str(root))
    try:
        from table_image_extractor import extract_table_from_image
        return extract_table_from_image
    except Exception:
        return None


def _pick_local_image_path(paths: List[str]) -> Optional[str]:
    for p in paths:
        if p and not p.startswith("http") and Path(p).exists():
            return p
    return None


def _page_number(chunk: Dict[str, Any]) -> int:
    span = chunk.get("page_span") or []
    if span and isinstance(span, list):
        return int(span[0]) + 1 if span else 1
    return 1
