import re
from typing import Optional


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

SUMMARY_PATTERNS = [
    r"^\s*summary\b",
    r"^\s*chapter summary\b",
    r"^\s*summary and conclusions?\b",
    r"^\s*in summary\b",
    r"^\s*conclusion\b",
]

PROBLEM_SET_PATTERNS = [
    r"\bproblem sets?\b",
    r"\bproblem set\b",
]

CONCEPT_CHECK_PATTERNS = [
    r"\bconcept checks?\b",
    r"\bconcept check\b",
]

CONCEPT_CHECK_SOLUTION_PATTERNS = [
    r"\bconcept check solutions?\b",
    r"\bconcept check answers?\b",
    r"\banswers to concept checks?\b",
]

END_OF_CHAPTER_PATTERNS = [
    r"\bend of chapter\b",
    r"\bend-of-chapter\b",
    r"\bchapter review\b",
]

THEOREM_PATTERNS = [
    r"^\s*(theorem|lemma|proposition|corollary)\b",
]

RULE_PATTERNS = [
    r"^\s*(rule|heuristic|key takeaway|takeaway)\b",
    r"\b(?:rule of thumb)\b",
]

PROCEDURE_PATTERNS = [
    r"^\s*step\s+\d+\b",
    r"^\s*\d+\.\s+[A-Z]",
    r"^\s*\(\d+\)\s+[A-Z]",
]

BULLET_PATTERNS = [
    r"^\s*[-•]\s+",
    r"^\s*\d+\)\s+",
    r"^\s*[A-Za-z]\)\s+",
    r"^\s*\(\d+\)\s+",
]

TITLE_OBJECT_KEYWORDS = {
    "problem_sets": PROBLEM_SET_PATTERNS,
    "concept_check": CONCEPT_CHECK_PATTERNS,
    "concept_check_solution": CONCEPT_CHECK_SOLUTION_PATTERNS,
    "key_terms": KEY_TERMS_PATTERNS,
    "summary": SUMMARY_PATTERNS,
    "learning_objectives": LO_PATTERNS,
    "end_of_chapter": END_OF_CHAPTER_PATTERNS,
}


def _matches_any(patterns, text: str) -> bool:
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


def detect_text_object(text: str, heading_path: str = "", list_context: bool = False) -> str:
    if not text:
        return "text"
    t = text.strip()
    hp = (heading_path or "").lower()

    if t.lower().startswith(("note:", "source:")):
        return "note"

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
    if "summary" in hp or "conclusion" in hp:
        return "summary"
    if "end of chapter" in hp or "chapter review" in hp:
        return "end_of_chapter"
    if "references" in hp or "bibliography" in hp:
        return "references"

    if _matches_any(LO_PATTERNS, t):
        return "learning_objectives"
    if _matches_any(KEY_TERMS_PATTERNS, t):
        return "key_terms"
    if _matches_any(SUMMARY_PATTERNS, t):
        return "summary"
    if _matches_any(PROBLEM_SET_PATTERNS, t):
        return "problem_sets"
    if _matches_any(CONCEPT_CHECK_SOLUTION_PATTERNS, t):
        return "concept_check_solution"
    if _matches_any(CONCEPT_CHECK_PATTERNS, t):
        return "concept_check"
    if _matches_any(END_OF_CHAPTER_PATTERNS, t):
        return "end_of_chapter"

    if _matches_any(THEOREM_PATTERNS, t):
        return "theorem"
    if _matches_any(RULE_PATTERNS, t):
        return "rule"

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
