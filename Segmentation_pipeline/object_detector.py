import re
from typing import Any, Dict, Optional


LO_PATTERNS = [
    r"^\s*learning objectives?\b",
    r"^\s*in this chapter, you will\b",
    r"^\s*after studying this chapter\b",
    r"^\s*what you will learn\b",
]

KEY_TERMS_PATTERNS = [
    r"^\s*key terms?\b",
    r"^\s*key concepts?\b",
    r"^\s*glossary\b",
]

PROBLEM_SET_PATTERNS = [
    r"\bproblem sets?\b",
    r"\bproblems?\b",
    r"\bexercises?\b",
    r"\breview questions?\b",
    r"\bend[-\s]of[-\s]chapter problems?\b",
]

CONCEPT_CHECK_PATTERNS = [
    r"\bconcept checks?\b",
    r"\bconcept check\b",
]

CONCEPT_CHECK_SOLUTION_PATTERNS = [
    r"\bconcept check solutions?\b",
    r"\bconcept check answers?\b",
    r"\banswers to concept checks?\b",
    r"\bsolutions to concept checks?\b",
]

PROCEDURE_PATTERNS = [
    r"^\s*step\s+\d+\b",
    r"^\s*\d+\.\s+[A-Z]",
    r"^\s*\(\d+\)\s+[A-Z]",
]

DERIVATION_PATTERNS = [
    r"^\s*(?:derivation|proof)\b",
    r"\bwe\s+can\s+show\b",
    r"\bderive(?:d|s|ing)?\b",
    r"\bsubstitut(?:e|ed|ing|ion)\b",
    r"\brearrang(?:e|ed|ing)\b",
    r"\bsolve\s+for\b",
]

CALCULATION_PATTERNS = [
    r"\bcalculate\b",
    r"\bcompute\b",
    r"\bestimate\b",
    r"\bdetermine\b",
    r"\bfind\b",
    r"\busing\s+equation\b",
]

SOLUTION_PATTERNS = [
    r"^\s*solution\b",
    r"^\s*answer\b",
    r"\bsolution\s+to\b",
    r"\banswer\s+to\b",
]

WORKED_EXAMPLE_PATTERNS = [
    r"^\s*worked\s+example\b",
    r"^\s*example\s+\d+(?:\.\d+)*\b",
    r"^\s*illustration\b",
    r"\bgiven:\b",
    r"\bstep\s*1\b",
]

BULLET_PATTERNS = [
    r"^\s*[-•]\s+",
    r"^\s*\d+\)\s+",
    r"^\s*[A-Za-z]\)\s+",
    r"^\s*\(\d+\)\s+",
]

TITLE_OBJECT_KEYWORDS = {
    "problem_sets": PROBLEM_SET_PATTERNS,
    "concept_check_solution": CONCEPT_CHECK_SOLUTION_PATTERNS,
    "concept_check": CONCEPT_CHECK_PATTERNS,
    "key_terms": KEY_TERMS_PATTERNS,
    "learning_objectives": LO_PATTERNS,
    "references": [r"^\s*references?\b", r"^\s*bibliography\b"],
}


def _matches_any(patterns: list[str], text: str) -> bool:
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


def _has_derivation_math_anchor(text: str) -> bool:
    t = text or ""
    low = t.lower()
    if "=" in t or "->" in t or "=>" in t or "⇒" in t:
        return True
    if re.search(r'\b(?:eq\.?|equation)\s*\(?\d+(?:\.\d+)*\)?', low):
        return True
    # Compact symbolic relation like r = rf + beta(...)
    if re.search(r'\b[a-zA-Z][a-zA-Z0-9_]*\s*=', t):
        return True
    return False


def _is_derivation_like_text(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    low = t.lower()

    # Explicit derivation/proof headings are trusted.
    if re.match(r'^\s*(?:\d+\s*[.)]\s*)?(?:derivation|proof)\b', low):
        return True

    has_transform = bool(
        re.search(
            r'\b(?:derive(?:d|s|ing)?|substitut(?:e|ed|ing|ion)|rearrang(?:e|ed|ing)|'
            r'solve\s+for|rewrite|differentiat(?:e|ed|ing|ion)|integrat(?:e|ed|ing|ion)|'
            r'by\s+definition)\b',
            low,
        )
    )
    if has_transform and _has_derivation_math_anchor(t):
        return True

    # "we can show" without math anchor is usually narrative text, not derivation.
    if re.search(r'\bwe\s+can\s+show\b', low) and _has_derivation_math_anchor(t):
        return True

    return False


def detect_text_object(text: str, heading_path: str = "", list_context: bool = False) -> str:
    if not text:
        return "text"
    t = text.strip()
    hp = (heading_path or "").lower()

    if t.lower().startswith(("note:", "source:")):
        return "note"

    if _matches_any(SOLUTION_PATTERNS, t):
        return "solution_candidate"

    if _matches_any(WORKED_EXAMPLE_PATTERNS, t):
        return "worked_example_candidate"

    if _matches_any(DERIVATION_PATTERNS, t) and _is_derivation_like_text(t):
        return "derivation_candidate"

    if _matches_any(CALCULATION_PATTERNS, t):
        return "calculation_candidate"

    if list_context:
        if _matches_any(PROCEDURE_PATTERNS, t):
            return "procedure"
        if _matches_any(BULLET_PATTERNS, t):
            return "list"

    if any(k in hp for k in ["problem set", "problem sets"]):
        return "problem_sets"
    if "concept check" in hp:
        return "concept_check"
    if "key term" in hp or "key terms" in hp or "glossary" in hp:
        return "key_terms"
    if "learning objective" in hp or "learning objectives" in hp:
        return "learning_objectives"
    if "references" in hp or "bibliography" in hp:
        return "references"

    if _matches_any(LO_PATTERNS, t):
        return "learning_objectives"
    if _matches_any(KEY_TERMS_PATTERNS, t):
        return "key_terms"
    if _matches_any(PROBLEM_SET_PATTERNS, t):
        return "problem_sets"
    if _matches_any(CONCEPT_CHECK_SOLUTION_PATTERNS, t):
        return "concept_check_solution"
    if _matches_any(CONCEPT_CHECK_PATTERNS, t):
        return "concept_check"
    if _matches_any(PROCEDURE_PATTERNS, t):
        return "procedure"
    if _matches_any(BULLET_PATTERNS, t):
        return "list"

    if ":" in t and len(t) <= 200:
        return "definition"
    return "text"


def detect_title_object(title_text: str) -> str:
    if not title_text:
        return ""
    t = title_text.strip().lower()
    for obj_type, patterns in TITLE_OBJECT_KEYWORDS.items():
        if _matches_any(patterns, t):
            return obj_type
    return ""


def detect_qa_zone(heading_path: str, segment_type: str = "") -> str:
    hp = (heading_path or "").lower()
    st = (segment_type or "").lower()
    if (
        "concept check solution" in hp
        or "solutions to concept checks" in hp
        or "solution to concept checks" in hp
        or "answers to concept checks" in hp
    ):
        return "concept_check_solution"
    if "concept check" in hp:
        return "concept_check"
    if any(k in hp for k in ["problem set", "review question", "end-of-chapter problem", "exercise"]):
        return "problem_set"
    if st in {"concept_check_solution", "solution_candidate"}:
        return "concept_check_solution"
    if st in {"concept_check"}:
        return "concept_check"
    if st in {"problem_sets"}:
        return "problem_set"
    return "other"


def candidate_role_from_segment_type(segment_type: str) -> str:
    st = (segment_type or "").lower()
    if st in {"solution_candidate", "concept_check_solution"}:
        return "solution_candidate"
    if st in {"worked_example_candidate"}:
        return "worked_example_candidate"
    if st in {"derivation_candidate"}:
        return "derivation_candidate"
    if st in {"calculation_candidate"}:
        return "calculation_candidate"
    if st in {"problem_sets", "concept_check"}:
        return "question_candidate"
    return "none"


def extract_numbering(text: str) -> Optional[Dict[str, Any]]:
    t = (text or "").strip()
    if not t:
        return None
    patterns = [
        r'^(?:question\s+)?q?\s*(\d+(?:\.\d+)*)\s*(?:[\):.]|\s)',
        r'^\(?(\d+(?:\.\d+)*)\)?\s*(?:[\):.]|\s)',
        r'^(?:concept\s+check)\s+(\d+(?:\.\d+)*)\b',
    ]
    num = None
    for pat in patterns:
        m = re.match(pat, t, re.IGNORECASE)
        if m:
            num = m.group(1)
            break
    if not num:
        return None
    normalized = re.sub(r'[^0-9.]', '', num).strip(".")
    if not normalized:
        return None
    parts = normalized.split(".")
    parent = ".".join(parts[:-1]) if len(parts) > 1 else parts[0]
    return {
        "raw": num,
        "normalized": normalized,
        "parent": parent,
        "subpart": parts[-1] if len(parts) > 1 else None,
    }


def is_heading_like_text(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    if re.match(r'^\d+(?:\.\d+)+\s+[A-Z]', t):
        return True
    words = re.findall(r'[A-Za-z]+', t)
    if len(words) <= 8 and ":" not in t and "=" not in t and not re.search(r'\b(solution|answer|therefore|thus|we find|we get)\b', t, re.IGNORECASE):
        if t and t[0].isdigit():
            return True
    return False
