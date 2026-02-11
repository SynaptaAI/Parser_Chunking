from pathlib import Path
from typing import Any, Dict, List, Optional


def pick_local_image_path(paths: List[str]) -> Optional[str]:
    for p in paths:
        if p and not p.startswith("http") and Path(p).exists():
            return p
    return None


def page_number(chunk: Dict[str, Any]) -> int:
    span = chunk.get("page_span") or chunk.get("page_range") or []
    if span and isinstance(span, list):
        return int(span[0]) + 1 if span else 1
    return 1
