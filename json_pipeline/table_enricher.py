from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
import sys

from .enricher_utils import page_number, pick_local_image_path


def enrich_table_chunks(chunks: List[Dict[str, Any]]) -> None:
    extractor = _load_table_extractor()
    if extractor is None:
        return

    for chunk in chunks:
        if chunk.get("type") != "table":
            continue
        image_path = pick_local_image_path(chunk.get("image_paths") or [])
        if not image_path:
            continue
        table_data = extractor(
            image_path=image_path,
            page_number=page_number(chunk),
            caption=chunk.get("caption") or "",
        )
        if table_data:
            chunk["table_data"] = table_data


def _load_table_extractor() -> Optional[Callable[..., Any]]:
    root = Path(__file__).resolve().parents[1] / "synapta-table-segmentation"
    if root.exists():
        sys.path.insert(0, str(root))
    try:
        from table_image_extractor import extract_table_from_image
        return extract_table_from_image
    except Exception:
        return None
