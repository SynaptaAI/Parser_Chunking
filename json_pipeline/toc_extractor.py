import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import fitz

from .models import ContentBlock
from .cleaning import clean_heading_text

logger = logging.getLogger(__name__)


def extract_toc_from_pdf(pdf_path: Path, max_pages: int = 40) -> List[Dict[str, Any]]:
    try:
        doc = fitz.open(str(pdf_path))
    except Exception as e:
        logger.warning("Failed to open PDF %s: %s", pdf_path, e)
        return []

    toc = doc.get_toc()
    if toc:
        return [{"level": e[0], "title": clean_heading_text(e[1]), "page": int(e[2])} for e in toc]

    toc_entries: List[Dict[str, Any]] = []
    toc_pattern = re.compile(r"^(.*?)(?:[\s\.·\-_]{3,})\s*([ivxIVX\d]+)$", re.MULTILINE)
    toc_keywords = ["contents", "table of contents", "index", "brief contents"]
    found_start = False

    for page_num in range(min(max_pages, len(doc))):
        page_text = doc[page_num].get_text()
        if not found_start:
            if any(kw in page_text.lower()[:500] for kw in toc_keywords):
                found_start = True
        if found_start:
            matches = toc_pattern.findall(page_text)
            for title, page_str in matches:
                title = clean_heading_text(title)
                if len(title) < 3:
                    continue
                try:
                    if page_str.isdigit():
                        target_page = int(page_str)
                    else:
                        target_page = roman_to_int(page_str)
                        if target_page is None:
                            continue
                    toc_entries.append({
                        "level": 1,
                        "title": title,
                        "page": target_page,
                        "source_page": page_num + 1,
                    })
                except Exception:
                    continue
    return toc_entries


def extract_toc_from_headers(blocks: List[ContentBlock]) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    seen = set()
    for b in blocks:
        if b.type != "heading":
            continue
        title = clean_heading_text(b.text)
        if not title:
            continue
        key = (title, b.page_idx)
        if key in seen:
            continue
        seen.add(key)
        level = infer_heading_level(title)
        entries.append({"level": level, "title": title, "page": b.page_idx + 1, "source": "header"})
    return entries


def align_toc_to_headers(toc: List[Dict[str, Any]], blocks: List[ContentBlock]) -> List[Dict[str, Any]]:
    blocks_by_page: Dict[int, List[ContentBlock]] = {}
    for b in blocks:
        blocks_by_page.setdefault(b.page_idx, []).append(b)

    aligned: List[Dict[str, Any]] = []
    for entry in toc:
        title = clean_heading_text(entry.get("title", ""))
        if not title:
            continue
        pred_page = max(int(entry.get("page", 1)) - 1, 0)
        window = range(max(0, pred_page - 2), pred_page + 3)
        match = _find_header_match(title, blocks_by_page, window)
        new_entry = dict(entry)
        if match:
            new_entry["page"] = match.page_idx + 1
            new_entry["matched_block_id"] = match.id
        aligned.append(new_entry)
    return aligned


def _find_header_match(
    title: str,
    blocks_by_page: Dict[int, List[ContentBlock]],
    window: range,
) -> Optional[ContentBlock]:
    norm_title = _normalize(title)
    if not norm_title:
        return None
    # Pass 1: heading blocks exact/close match
    for p in window:
        for b in blocks_by_page.get(p, []):
            if b.type != "heading":
                continue
            norm_text = _normalize(b.text)
            if norm_title in norm_text or norm_text in norm_title:
                if _is_substantial_match(norm_title, norm_text):
                    return b
    # Pass 2: any text block exact match
    for p in window:
        for b in blocks_by_page.get(p, []):
            if b.type != "text":
                continue
            norm_text = _normalize(b.text)
            if norm_title == norm_text:
                return b
    return None


def _normalize(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]", "", text.lower())


def _is_substantial_match(ref: str, target: str) -> bool:
    if not ref or not target:
        return False
    if len(ref) < 4 and len(target) < 4:
        return False
    if ref == target:
        return True
    from difflib import SequenceMatcher

    ratio = SequenceMatcher(None, ref, target).ratio()
    if ratio >= 0.7:
        return True
    shorter, longer = (ref, target) if len(ref) <= len(target) else (target, ref)
    if shorter in longer and len(shorter) / max(len(longer), 1) >= 0.6:
        return True
    return False


def infer_heading_level(title: str) -> int:
    t = title.strip()
    if re.match(r"^(part|chapter)\b", t, re.IGNORECASE):
        return 1
    if re.match(r"^section\b", t, re.IGNORECASE):
        return 2
    m = re.match(r"^(\d+)(?:\.(\d+))*", t)
    if m:
        dots = t.split(" ")[0].count(".")
        return max(1, dots + 1)
    return 2


def roman_to_int(roman: str) -> Optional[int]:
    roman = roman.upper().strip()
    if not roman:
        return None
    valid = {"I", "V", "X", "L", "C", "D", "M"}
    if any(ch not in valid for ch in roman):
        return None
    values = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    total = 0
    prev = 0
    for ch in reversed(roman):
        val = values[ch]
        if val < prev:
            total -= val
        else:
            total += val
            prev = val
    return total if total > 0 else None
