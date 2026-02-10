from pathlib import Path
from typing import Any, Dict, List, Optional
import sys


def enrich_image_chunks(chunks: List[Dict[str, Any]]) -> None:
    analyzer = _load_image_analyzer()
    if analyzer is None:
        return

    for chunk in chunks:
        if chunk.get("type") != "image":
            continue
        image_path = _pick_local_image_path(chunk.get("image_paths") or [])
        if not image_path:
            continue
        result = analyzer(
            image_path=image_path,
            caption=chunk.get("caption") or "",
            page_no=_page_number(chunk),
            heading_path=chunk.get("heading_path") or "",
            book_id=chunk.get("doc_id") or "book",
        )
        if result:
            chunk["image_data"] = result


def _load_image_analyzer():
    root = Path(__file__).resolve().parents[1] / "synapta-image-segmentation"
    if root.exists():
        sys.path.insert(0, str(root))
    try:
        from image_file_extractor import analyze_image_file
        return analyze_image_file
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
