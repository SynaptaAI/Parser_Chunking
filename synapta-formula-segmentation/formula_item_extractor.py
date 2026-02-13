"""
Create a FormulaSegment from a single formula text + minimal context.
This bypasses full PDF parsing and LLM enrichment.
"""

from typing import Any, Dict, Optional, Tuple
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
    ch_num, ch_title = _extract_chapter_metadata(heading_path, chapter_number, chapter_title)
    if ch_num == "unknown" and equation_number:
        m = re.search(r'\((\d+)(?:\.\d+)*\)', equation_number)
        if m:
            ch_num = m.group(1)
    usage_type = _infer_usage_type(raw, heading_path)
    symbols = _extract_variable_symbols(raw)
    short_meaning = _build_short_meaning(raw, symbols, usage_type, equation_number)

    seg = FormulaSegment(
        segment_id=f"formula_{canonical_key[:12]}_p{page_number}",
        book_id=book_id,
        chapter_number=ch_num,
        chapter_title=ch_title,
        page_start=page_number,
        page_end=page_number,
        bbox=_bbox_from_dict(bbox, page_number),
        text_content=raw,
        heading_path=heading_path,
        formula_text_raw=raw,
        formula_latex=_latex_fallback(raw),
        equation_number=equation_number,
        canonical_formula_key=canonical_key,
        variables=symbols,
        usage_type=usage_type,
        short_meaning=short_meaning,
        confidence=1.0,
        needs_human_review=_needs_review(raw, equation_number, symbols),
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
        return f"({m.group(1)})"
    m = re.search(r"\b(?:Eq\.?|Equation)\s*\(?(\d+(?:\.\d+)*)\)?\b", text, re.IGNORECASE)
    if m:
        return f"({m.group(1)})"
    m = re.search(r'\\tag\s*\{\s*(\d+(?:\.\d+)*)\s*\}', text)
    if m:
        return f"({m.group(1)})"
    m = re.search(r"\b\((\d+[A-Za-z]?)\)\b", text)
    if m and len(m.group(1)) <= 4:
        # Weak fallback for short numbered labels such as (9), (9a)
        return f"({m.group(1)})"
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


def _extract_chapter_metadata(
    heading_path: Optional[str],
    chapter_number: str,
    chapter_title: Optional[str],
) -> Tuple[str, Optional[str]]:
    if chapter_number and str(chapter_number).strip().lower() not in {"", "unknown", "none"}:
        return str(chapter_number), chapter_title

    hp = (heading_path or "").strip()
    if not hp:
        return "unknown", chapter_title

    m = re.search(r'chapter\s+(\d+)\s*:\s*([^>]+)', hp, re.IGNORECASE)
    if m:
        return m.group(1), m.group(2).strip()

    m = re.search(r'chapter\s+(\d+)', hp, re.IGNORECASE)
    if m:
        return m.group(1), chapter_title

    m = re.search(r'(^| > )(\d+)(?:\.\d+)*', hp)
    if m:
        return m.group(2), chapter_title

    return "unknown", chapter_title


def _infer_usage_type(formula_text: str, heading_path: Optional[str]) -> str:
    low = (formula_text or "").lower()
    hp = (heading_path or "").lower()
    if re.search(r'\b(?:eq\.?|equation)\s*\(?\d+(?:\.\d+)*\)?', low):
        if re.search(r'\b(?:use|from|by|see|as in|according to)\b', low):
            return "reference"
    if any(k in hp for k in ["key equations", "definition", "notation"]):
        return "definition"
    if re.search(r'\b(?:where|let|defined as|is defined as)\b', low):
        return "definition"
    if re.search(r'\b(?:example|illustration|solution|calculate|compute|estimate|find)\b', low):
        return "application"
    return "application"


def _latex_fallback(formula_text: str) -> Optional[str]:
    raw = (formula_text or "").strip()
    if not raw:
        return None
    # Keep best-effort literal formula text to avoid null latex in downstream.
    return raw


def _build_short_meaning(
    formula_text: str,
    variables: list[VariableDefinition],
    usage_type: str,
    equation_number: Optional[str],
) -> Optional[str]:
    raw = (formula_text or "").strip()
    if not raw:
        return None
    lhs = None
    if "=" in raw:
        lhs = raw.split("=", 1)[0].strip()
    if lhs and 1 <= len(lhs) <= 24:
        return f"Computes {lhs} from related inputs ({usage_type})."
    if variables:
        syms = ", ".join(v.symbol for v in variables[:4])
        return f"Formula over variables {syms} ({usage_type})."
    if equation_number:
        return f"Equation {equation_number} ({usage_type})."
    return f"Mathematical relation ({usage_type})."


def _needs_review(
    formula_text: str,
    equation_number: Optional[str],
    variables: list[VariableDefinition],
) -> bool:
    raw = (formula_text or "").strip()
    if not raw:
        return True
    # Very long prose-like payload likely means formula extraction noise.
    if len(raw.split()) > 45 and "=" not in raw and not equation_number:
        return True
    if not variables and "=" not in raw and not equation_number:
        return True
    return False
