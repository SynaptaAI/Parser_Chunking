from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
import sys

from .enricher_utils import page_number, pick_local_image_path


def enrich_image_chunks(chunks: List[Dict[str, Any]]) -> None:
    analyzer = _load_image_analyzer()
    if analyzer is None:
        return

    for chunk in chunks:
        if chunk.get("type") != "image":
            continue
        image_path = pick_local_image_path(chunk.get("image_paths") or [])
        if not image_path:
            continue
        result = analyzer(
            image_path=image_path,
            caption=chunk.get("caption") or "",
            page_no=page_number(chunk),
            heading_path=chunk.get("heading_path") or "",
            book_id=chunk.get("doc_id") or "book",
        )
        if result:
            chunk["image_data"] = result


def _load_image_analyzer() -> Optional[Callable[..., Any]]:
    root = Path(__file__).resolve().parents[1] / "synapta-image-segmentation"
    if root.exists():
        sys.path.insert(0, str(root))
    try:
        from image_file_extractor import analyze_image_file
        return analyze_image_file
    except Exception:
        return None
