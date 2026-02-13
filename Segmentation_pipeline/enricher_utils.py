from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple


def pick_local_image_path(paths: List[str]) -> Optional[str]:
    for p in paths:
        if p and not p.startswith("http") and Path(p).exists():
            return p
    return None


def page_number(chunk: Dict[str, Any]) -> int:
    start, _ = page_bounds(chunk)
    return start


def page_bounds(chunk: Dict[str, Any]) -> Tuple[int, int]:
    span = chunk.get("page_span")
    if isinstance(span, list) and span:
        if len(span) >= 2:
            start = int(span[0]) + 1
            end = int(span[1]) + 1
            return max(1, start), max(1, end)
        page = int(span[0]) + 1
        return max(1, page), max(1, page)

    page_range = chunk.get("page_range")
    if isinstance(page_range, list) and page_range:
        vals = [int(p) + 1 for p in page_range]
        return max(1, min(vals)), max(1, max(vals))

    return 1, 1


def bbox_dict(chunk: Dict[str, Any]) -> Optional[Dict[str, float]]:
    bbox = chunk.get("bbox")
    if not bbox or len(bbox) < 4:
        return None
    try:
        return {"x0": float(bbox[0]), "y0": float(bbox[1]), "x1": float(bbox[2]), "y1": float(bbox[3])}
    except Exception:
        return None


def normalize_heading_path(heading_path: Any) -> str:
    if not isinstance(heading_path, str):
        return ""
    parts = [p.strip() for p in heading_path.split(">") if p and p.strip()]
    return " > ".join(parts)


def chapter_from_heading_path(heading_path: str) -> Tuple[str, Optional[str]]:
    hp = normalize_heading_path(heading_path)
    if not hp:
        return "unknown", None

    m = re.search(r"chapter\s+(\d+)\s*:\s*([^>]+)", hp, re.IGNORECASE)
    if m:
        return m.group(1), m.group(2).strip()
    m = re.search(r"chapter\s+(\d+)", hp, re.IGNORECASE)
    if m:
        return m.group(1), None

    # Prefer numbered section cues anywhere in heading path (e.g., "1.2: ...", "Concept Check 7.3").
    m = re.search(r"(?:^| > )(?!table\s|figure\s|fig\.|equation\s|eq\.)(?:[^>]*?)\b(\d+)\.(\d+)\b", hp, re.IGNORECASE)
    if m:
        return m.group(1), None

    first = hp.split(" > ")[0].strip()
    m = re.match(r"(\d+)(?:\.\d+)*\s*(.*)$", first)
    if m:
        title = m.group(2).strip() or None
        return m.group(1), title

    # Last fallback: any plain leading integer token in path.
    m = re.search(r"(?:^| > )(\d+)\b", hp)
    if m:
        return m.group(1), None
    return "unknown", None


def enrich_anchor(chunk: Dict[str, Any], doc_id: str) -> Dict[str, Any]:
    page_start, page_end = page_bounds(chunk)
    heading = normalize_heading_path(chunk.get("heading_path") or "")
    chapter_number, chapter_title = chapter_from_heading_path(heading)
    return {
        "doc_id": doc_id,
        "source_chunk_id": chunk.get("id"),
        "page_start": page_start,
        "page_end": page_end,
        "heading_path": heading,
        "chapter_number": chapter_number,
        "chapter_title": chapter_title,
        "bbox": bbox_dict(chunk),
    }


def resolve_visual_path(
    chunk: Dict[str, Any],
    doc_id: str,
    out_dir: Optional[Path] = None,
) -> Optional[str]:
    local = pick_local_image_path(chunk.get("image_paths") or [])
    if local:
        return local

    if out_dir is None:
        return None

    page_no = page_number(chunk)
    chunk_type = (chunk.get("type") or "").strip()
    chunk_id = (chunk.get("id") or "").strip()
    if not chunk_type or not chunk_id:
        return None

    visual_dir = out_dir / "visuals" / doc_id
    if not visual_dir.exists():
        return None

    # Fallback for imperfect path propagation in chunks.
    candidates = [
        visual_dir / f"{chunk_type}_p{page_no}_{chunk_id}.png",
        visual_dir / f"{chunk_type}_p{page_no}_{chunk_id}.jpg",
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    return None


def set_enrichment_status(
    chunk: Dict[str, Any],
    module: str,
    status: str,
    reason: Optional[str] = None,
) -> None:
    store = chunk.setdefault("enrichment_status", {})
    if not isinstance(store, dict):
        store = {}
        chunk["enrichment_status"] = store
    payload: Dict[str, Any] = {"status": status}
    if reason:
        payload["reason"] = reason
    store[module] = payload
