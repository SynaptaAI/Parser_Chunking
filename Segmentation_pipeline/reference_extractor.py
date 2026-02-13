import re
from typing import Any, Dict, List, Tuple


PATTERNS = [
    (r"\b(?:Figure|Fig\.?)\s+(\d+(?:\.\d+)*)", "figure"),
    (r"\b(?:Table|Tbl\.?)\s+(\d+(?:\.\d+)*)", "table"),
    (r"\b(?:Equation|Eq\.?)\s*\(?\s*(\d+(?:\.\d+)*)\s*\)?", "equation"),
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


def link_references(chunks: List[Dict[str, Any]]) -> None:
    # Build index from figure/table/formula captions/content and formula equation numbers.
    index: Dict[Tuple[str, str], str] = {}
    for c in chunks:
        ctype = c.get("type")
        if ctype not in ("image", "table", "formula"):
            continue
        segment_id = c.get("segment_id")
        if not segment_id:
            continue

        texts = [c.get("content") or "", c.get("caption") or ""]
        if ctype == "formula":
            sf = c.get("synapta_formula") or {}
            eq = str(sf.get("equation_number") or "").strip()
            if eq:
                m = re.search(r"(\d+(?:\.\d+)*)", eq)
                if m:
                    index[("equation", m.group(1))] = segment_id

        for text in texts:
            if not text:
                continue
            for r in extract_references(text):
                index[(r["type"], r["id"])] = segment_id

    # Figure/table chunks often don't contain explicit self-reference text.
    # Derive weak self-keys from nearby heading path if numbering is present.
    for c in chunks:
        ctype = c.get("type")
        if ctype not in ("image", "table"):
            continue
        segment_id = c.get("segment_id")
        heading = str(c.get("heading_path") or "")
        if not segment_id or not heading:
            continue
        for r in extract_references(heading):
            index[(r["type"], r["id"])] = segment_id

    # Attach target ids.
    for c in chunks:
        refs = c.get("references") or []
        if not refs:
            continue
        for r in refs:
            key = (r.get("type"), r.get("id"))
            if key in index:
                r["ref_target_id"] = index[key]


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
