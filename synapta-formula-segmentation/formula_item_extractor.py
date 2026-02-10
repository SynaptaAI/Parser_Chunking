"""
Create a FormulaSegment from a single formula text + minimal context.
This bypasses full PDF parsing and LLM enrichment.
"""

from typing import Any, Dict, Optional
import hashlib
import re

from schemas import BBox, FormulaSegment, VariableDefinition


def extract_formula_item(
    formula_text: str,
    page_number: int,
    book_id: str,
    heading_path: Optional[str] = None,
    bbox: Optional[Dict[str, float]] = None,
    chapter_number: str = "unknown",
    chapter_title: Optional[str] = None,
) -> Dict[str, Any]:
    raw = (formula_text or "").strip()
    equation_number = _extract_equation_number(raw)
    canonical_key = _canonical_key(raw)

    seg = FormulaSegment(
        segment_id=f"formula_{canonical_key[:12]}_p{page_number}",
        book_id=book_id,
        chapter_number=chapter_number,
        chapter_title=chapter_title,
        page_start=page_number,
        page_end=page_number,
        bbox=_bbox_from_dict(bbox, page_number),
        text_content=raw,
        heading_path=heading_path,
        formula_text_raw=raw,
        equation_number=equation_number,
        canonical_formula_key=canonical_key,
        variables=_extract_variable_symbols(raw),
        usage_type="application",
        confidence=1.0,
    )
    return seg.model_dump()


def _bbox_from_dict(bbox: Optional[Dict[str, float]], page_number: int) -> Optional[BBox]:
    if not bbox:
        return None
    return BBox(
        page=page_number,
        x0=bbox.get("x0", 0.0),
        y0=bbox.get("y0", 0.0),
        x1=bbox.get("x1", 0.0),
        y1=bbox.get("y1", 0.0),
    )


def _extract_equation_number(text: str) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"\((\d+(?:\.\d+)*)\)\s*$", text)
    if m:
        return m.group(1)
    m = re.search(r"\bEq\.?\s*(\d+(?:\.\d+)*)\b", text, re.IGNORECASE)
    if m:
        return m.group(1)
    return None


def _canonical_key(text: str) -> str:
    norm = re.sub(r"\s+", "", text or "")
    return hashlib.md5(norm.encode("utf-8")).hexdigest()


def _extract_variable_symbols(text: str) -> list[VariableDefinition]:
    if not text:
        return []
    symbols = set(re.findall(r"\b[a-zA-Z]\b", text))
    greek = re.findall(r"\b(alpha|beta|gamma|delta|sigma|mu|rho|theta|lambda|pi)\b", text, re.IGNORECASE)
    symbols.update(greek)
    return [
        VariableDefinition(symbol=s, meaning="", inferred=True, source="formula_only")
        for s in sorted(symbols)
    ]
