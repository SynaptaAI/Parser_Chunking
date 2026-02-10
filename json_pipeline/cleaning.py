import re
from typing import Iterable, List

from .models import ContentBlock


CONTROL_CHARS = re.compile(r"[\x00-\x1F\x7F]")
MULTI_SPACE = re.compile(r"\s+")
SOFT_HYPHEN = "\u00ad"

SPACED_CAPS = re.compile(r"\b(?:[A-Z]\s){2,}[A-Z]\b")

FRONT_MATTER_PATTERNS = [
    r"\btable of contents\b",
    r"\bbrief contents\b",
    r"\bcontents\b",
    r"\bcopyright\b",
    r"\ball rights reserved\b",
    r"\blibrary of congress\b",
    r"\bcataloging-in-publication\b",
    r"\bprinted in\b",
    r"\bisbn\b",
    r"\bwww\.\b",
    r"\bhttp[s]?://\b",
]

MAIN_BODY_PATTERNS = [
    r"^(part|chapter)\b",
    r"^\d+(\.\d+)*\b",
    r"^第[一二三四五六七八九十0-9]+章",
]

BACK_MATTER_PATTERNS = [
    r"^index\b",
    r"^bibliography\b",
    r"^references\b",
    r"^appendix\b",
    r"^appendices\b",
    r"^glossary\b",
    r"^notation\b",
    r"^symbols?\b",
]

SPECIAL_TERM_PATTERNS = [
    r"\bdefinition\b",
    r"\bdefined as\b",
    r"\bglossary\b",
    r"\bnotation\b",
    r"\bsymbol\b",
    r"\bterm\b",
    r"\bkey term\b",
]


def _normalize_spaces(text: str) -> str:
    text = text.replace(SOFT_HYPHEN, "")
    text = CONTROL_CHARS.sub(" ", text)
    text = MULTI_SPACE.sub(" ", text)
    return text.strip()


def clean_heading_text(text: str) -> str:
    if not text:
        return ""
    t = _normalize_spaces(text)
    # Collapse spaced uppercase letters: "C H A P T E R" -> "CHAPTER"
    def _collapse(match: re.Match) -> str:
        return match.group(0).replace(" ", "")
    t = SPACED_CAPS.sub(_collapse, t)
    return t.strip()


def clean_block_text(text: str) -> str:
    if not text:
        return ""
    return _normalize_spaces(text)


def filter_blocks(blocks: Iterable[ContentBlock]) -> List[ContentBlock]:
    filtered: List[ContentBlock] = []
    for b in blocks:
        if b.type in ("image", "table", "formula"):
            filtered.append(b)
            continue

        text = (b.text or "").strip()
        if not text:
            continue
        if len(text) < 2:
            continue

        # Front-matter / boilerplate removal (mostly first pages)
        if b.page_idx <= 5:
            low = text.lower()
            if any(re.search(p, low) for p in FRONT_MATTER_PATTERNS):
                continue

        filtered.append(b)
    return filtered


def is_main_body_title(title: str) -> bool:
    t = (title or "").strip().lower()
    return any(re.search(p, t) for p in MAIN_BODY_PATTERNS)


def is_back_matter_title(title: str) -> bool:
    t = (title or "").strip().lower()
    return any(re.search(p, t) for p in BACK_MATTER_PATTERNS)


def is_special_term_text(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return False
    if any(re.search(p, t) for p in SPECIAL_TERM_PATTERNS):
        return True
    # Short term-definition pattern: "Term: definition"
    if ":" in t and len(t) <= 140:
        return True
    return False
