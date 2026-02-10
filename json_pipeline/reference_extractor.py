import re
from typing import List, Dict, Tuple


PATTERNS = [
    (r"\b(?:Figure|Fig\.?)\s+(\d+(?:\.\d+)*)", "figure"),
    (r"\b(?:Table|Tbl\.?)\s+(\d+(?:\.\d+)*)", "table"),
    (r"\b(?:Equation|Eq\.?)\s+(\d+(?:\.\d+)*)", "equation"),
    (r"\b(?:Appendix)\s+([A-Z])", "appendix"),
]


def extract_references(text: str) -> List[Dict[str, str]]:
    if not text:
        return []
    refs = []
    for pattern, rtype in PATTERNS:
        for m in re.finditer(pattern, text):
            refs.append({"type": rtype, "id": m.group(1), "raw": m.group(0)})
    return _dedupe_refs(refs)


def _dedupe_refs(refs: List[Dict[str, str]]) -> List[Dict[str, str]]:
    seen: set[Tuple[str, str]] = set()
    out: List[Dict[str, str]] = []
    for r in refs:
        key = (r.get("type"), r.get("id"))
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def link_references(chunks: List[Dict[str, str]]) -> None:
    # Build index from captions in figure/table/formula chunks.
    index: Dict[Tuple[str, str], str] = {}
    for c in chunks:
        ctype = c.get("type")
        if ctype not in ("image", "table", "formula"):
            continue
        content = c.get("content") or ""
        for r in extract_references(content):
            index[(r["type"], r["id"])] = c.get("segment_id")

    # Attach target ids
    for c in chunks:
        refs = c.get("references") or []
        if not refs:
            continue
        for r in refs:
            key = (r.get("type"), r.get("id"))
            if key in index:
                r["ref_target_id"] = index[key]
