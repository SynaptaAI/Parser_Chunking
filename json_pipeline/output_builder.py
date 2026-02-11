from typing import Any, Dict, List, Optional, Tuple
import re
import hashlib

from .models import DocumentTree, SectionNode
from .sentence_classifier import SentenceClassifier
from .object_detector import detect_text_object, detect_title_object
from .reference_extractor import extract_references


class ChunkerJson:
    def __init__(self, char_limit: int = 1500) -> None:
        self.char_limit = char_limit
        self.counter = 0
        self.classifier = SentenceClassifier()

    def chunk(self, doc: DocumentTree) -> List[Dict[str, Any]]:
        chunks: List[Dict[str, Any]] = []
        for section in doc.root_sections:
            chunks.extend(self._chunk_section(section))
        return chunks

    def _chunk_section(self, section: SectionNode) -> List[Dict[str, Any]]:
        chunks: List[Dict[str, Any]] = []
        heading_path = section.path or section.title

        i = 0
        while i < len(section.blocks):
            b = section.blocks[i]

            if b.type == "heading":
                raw_type = b.metadata.get("raw_type")
                title_obj_type = detect_title_object(b.text) if raw_type == "title" else ""
                if title_obj_type:
                    # Merge title with following text/list blocks into one structured chunk.
                    content_parts, pages, bboxes, j = _collect_structured_content(
                        section.blocks,
                        i,
                        max_text_blocks=None,
                    )
                    content = "\n".join([p for p in content_parts if p])

                    chunk_id = f"chunk_{self.counter:05d}"
                    self.counter += 1
                    chunks.append({
                        "id": chunk_id,
                        "heading_path": heading_path,
                        "content": content,
                        "type": title_obj_type,
                        "POS": self.classifier.classify(content),
                        "page_range": sorted(pages),
                        "page_span": _page_span(pages),
                        "bbox": _bbox_union(bboxes),
                        "taxonomy_path": _taxonomy_path(heading_path),
                        "segment_type": title_obj_type,
                        "confidence": 1.0,
                        "references": extract_references(content),
                    })
                    i = j
                    continue

                # Keep normal heading separate.
                heading_seg_type = detect_text_object(b.text, heading_path)
                chunk_id = f"chunk_{self.counter:05d}"
                self.counter += 1
                chunks.append({
                    "id": chunk_id,
                    "heading_path": heading_path,
                    "content": b.text.strip(),
                    "type": "heading",
                    "POS": [],
                    "page_range": [b.page_idx],
                    "page_span": [b.page_idx, b.page_idx],
                    "bbox": _bbox_from_block(b),
                    "taxonomy_path": _taxonomy_path(heading_path),
                    "segment_type": f"heading_{heading_seg_type}" if heading_seg_type != "text" else "heading",
                    "confidence": 1.0,
                })
                i += 1
                continue

            if b.type in ("table", "image", "formula"):
                chunk_id = f"chunk_{self.counter:05d}"
                self.counter += 1
                content = b.text.strip()
                image_paths = []
                if b.metadata.get("local_image_paths"):
                    image_paths.extend(b.metadata.get("local_image_paths"))
                if b.metadata.get("image_paths"):
                    image_paths.extend(b.metadata.get("image_paths"))
                # de-dup while preserving order
                seen = set()
                image_paths = [p for p in image_paths if not (p in seen or seen.add(p))]

                if b.type == "image":
                    caption = content
                    if not caption:
                        content = "[image]"
                    else:
                        content = f"[image] {caption}"
                else:
                    caption = ""
                chunks.append({
                    "id": chunk_id,
                    "heading_path": heading_path,
                    "content": content,
                    "type": b.type,
                    "POS": [],
                    "page_range": [b.page_idx],
                    "page_span": [b.page_idx, b.page_idx],
                    "bbox": _bbox_from_block(b),
                    "taxonomy_path": _taxonomy_path(heading_path),
                    "segment_type": b.type,
                    "confidence": 1.0,
                    "caption": caption if b.type == "image" else "",
                    "image_paths": image_paths,
                    "references": extract_references(content),
                })
                i += 1
                continue

            if not b.text:
                i += 1
                continue

            if _is_list_item(b.text) or _is_procedure_item(b.text):
                list_kind = "procedure" if _is_procedure_item(b.text) else "list"
                items = [b.text.strip()]
                pages = {b.page_idx}
                bboxes = []
                if b.bbox:
                    bboxes.append((b.bbox.x0, b.bbox.y0, b.bbox.x1, b.bbox.y1))

                j = i + 1
                while j < len(section.blocks):
                    nb = section.blocks[j]
                    nb_raw = nb.metadata.get("raw_type")
                    if nb.type == "heading" or nb_raw == "title":
                        break
                    if nb.type in ("table", "image", "formula"):
                        break
                    if nb.type != "text":
                        break
                    if not nb.text:
                        j += 1
                        continue
                    if _is_list_item(nb.text) or _is_procedure_item(nb.text):
                        if _is_procedure_item(nb.text):
                            list_kind = "procedure"
                        items.append(nb.text.strip())
                        pages.add(nb.page_idx)
                        if nb.bbox:
                            bboxes.append((nb.bbox.x0, nb.bbox.y0, nb.bbox.x1, nb.bbox.y1))
                        j += 1
                        continue
                    if _looks_like_list_continuation(nb.text):
                        items[-1] = items[-1].rstrip() + " " + nb.text.strip()
                        pages.add(nb.page_idx)
                        if nb.bbox:
                            bboxes.append((nb.bbox.x0, nb.bbox.y0, nb.bbox.x1, nb.bbox.y1))
                        j += 1
                        continue
                    break

                chunk_id = f"chunk_{self.counter:05d}"
                self.counter += 1
                content = "\n".join(items)
                chunks.append({
                    "id": chunk_id,
                    "heading_path": heading_path,
                    "content": content,
                    "type": "text",
                    "POS": self.classifier.classify(content),
                    "page_range": sorted(pages),
                    "page_span": _page_span(pages),
                    "bbox": _bbox_union(bboxes),
                    "taxonomy_path": _taxonomy_path(heading_path),
                    "segment_type": list_kind,
                    "confidence": 1.0,
                    "references": extract_references(content),
                })
                i = j
                continue

            list_context = _is_numbered_list_context(heading_path)
            obj_type = detect_text_object(b.text, heading_path, list_context=list_context)
            chunk_id = f"chunk_{self.counter:05d}"
            self.counter += 1
            chunks.append({
                "id": chunk_id,
                "heading_path": heading_path,
                "content": b.text.strip(),
                "type": "text",
                "POS": self.classifier.classify(b.text.strip()),
                "page_range": [b.page_idx],
                "page_span": [b.page_idx, b.page_idx],
                "bbox": _bbox_from_block(b),
                "taxonomy_path": _taxonomy_path(heading_path),
                "segment_type": obj_type,
                "confidence": 1.0,
                "references": extract_references(b.text.strip()),
            })
            i += 1

        for child in section.children:
            chunks.extend(self._chunk_section(child))

        return chunks


def build_elements(doc: DocumentTree) -> List[Dict[str, Any]]:
    elements: List[Dict[str, Any]] = []
    classifier = SentenceClassifier()

    def emit_section(section: SectionNode) -> None:
        heading_path = section.path or section.title
        elements.append({
            "heading_path": heading_path,
            "content": section.title,
            "type": "heading",
            "POS": [],
            "page_range": [],
            "page_span": [],
            "bbox": None,
            "taxonomy_path": _taxonomy_path(heading_path),
            "segment_type": "heading",
            "confidence": 1.0,
        })
        for idx, b in enumerate(section.blocks):
            pos = classifier.classify(b.text) if b.type == "text" and b.text else []
            seg_type = b.type
            if b.type == "text":
                list_context = _is_numbered_list_context(heading_path)
                if list_context and (_is_list_item(b.text) or _is_procedure_item(b.text)):
                    seg_type = detect_text_object(b.text, heading_path, list_context=True)
                else:
                    seg_type = detect_text_object(b.text, heading_path, list_context=False)
            elements.append({
                "heading_path": heading_path,
                "content": b.text,
                "type": b.type,
                "POS": pos,
                "page_range": [b.page_idx],
                "page_span": [b.page_idx, b.page_idx],
                "bbox": _bbox_from_block(b),
                "taxonomy_path": _taxonomy_path(heading_path),
                "segment_type": seg_type,
                "confidence": 1.0,
                "references": extract_references(b.text or ""),
            })
        for child in section.children:
            emit_section(child)

    for r in doc.root_sections:
        emit_section(r)

    return elements


def finalize_segments(segments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    # Add stable segment_id and prev/next ids.
    for seg in segments:
        seg["segment_id"] = _stable_id(seg)
    for i, seg in enumerate(segments):
        seg["prev_segment_id"] = segments[i - 1]["segment_id"] if i > 0 else None
        seg["next_segment_id"] = segments[i + 1]["segment_id"] if i < len(segments) - 1 else None
    return segments


def _stable_id(seg: Dict[str, Any]) -> str:
    key = {
        "type": seg.get("segment_type") or seg.get("type"),
        "heading_path": seg.get("heading_path"),
        "page_span": seg.get("page_span"),
        "content": seg.get("content"),
    }
    blob = repr(key).encode("utf-8")
    return "seg_" + hashlib.md5(blob).hexdigest()[:12]


def _taxonomy_path(heading_path: str) -> List[str]:
    if not heading_path:
        return []
    return [p.strip() for p in heading_path.split(" > ") if p.strip()]


def _bbox_from_block(b) -> Optional[List[float]]:
    if not b or not b.bbox:
        return None
    return [b.bbox.x0, b.bbox.y0, b.bbox.x1, b.bbox.y1]


def _bbox_union(bboxes: List[Tuple[float, float, float, float]]) -> Optional[List[float]]:
    if not bboxes:
        return None
    x0 = min(b[0] for b in bboxes)
    y0 = min(b[1] for b in bboxes)
    x1 = max(b[2] for b in bboxes)
    y1 = max(b[3] for b in bboxes)
    return [x0, y0, x1, y1]


def _page_span(pages: set) -> List[int]:
    if not pages:
        return []
    return [min(pages), max(pages)]


def mark_numbered_lists(segments: List[Dict[str, Any]]) -> None:
    for seg in segments:
        seg_type = seg.get("segment_type") or seg.get("type")
        if seg_type not in ("text", "heading", "heading_text"):
            continue
        content = seg.get("content") or ""
        if _is_procedure_item(content):
            seg["segment_type"] = "procedure"
        elif _is_list_item(content):
            seg["segment_type"] = "list"


def _is_numbered_list_context(heading_path: str) -> bool:
    t = (heading_path or "").lower()
    return any(k in t for k in ["list", "procedure", "steps", "checklist", "summary points"])


def _is_list_item(text: str) -> bool:
    if not text:
        return False
    t = text.strip()
    return bool(
        t.startswith(("-", "•", "–", "—"))
        or re.match(r"^\s*\d+\.\s+", t)
        or re.match(r"^\s*\d+\)\s+", t)
        or re.match(r"^\s*\(\d+\)\s+", t)
        or re.match(r"^\s*[A-Za-z]\)\s+", t)
    )


def _is_procedure_item(text: str) -> bool:
    if not text:
        return False
    t = text.strip()
    if re.match(r"^\s*step\s+\d+\b", t, re.IGNORECASE):
        return True
    if re.match(r"^\s*(first|second|third|next|then|finally)[,:\s]", t, re.IGNORECASE):
        return True
    return False


def _looks_like_list_continuation(text: str) -> bool:
    if not text:
        return False
    t = text.strip()
    if _is_list_item(t) or _is_procedure_item(t):
        return False
    if t.startswith(("and ", "or ", "but ", "with ", "including ", "(")):
        return True
    return t[:1].islower()


def _collect_structured_content(
    blocks: List[Any],
    start_index: int,
    max_text_blocks: Optional[int] = None,
) -> Tuple[List[str], set, List[Tuple[float, float, float, float]], int]:
    b = blocks[start_index]
    content_parts = [b.text.strip()]
    pages = {b.page_idx}
    bboxes: List[Tuple[float, float, float, float]] = []
    if b.bbox:
        bboxes.append((b.bbox.x0, b.bbox.y0, b.bbox.x1, b.bbox.y1))

    j = start_index + 1
    appended_text_blocks = 0
    while j < len(blocks):
        nb = blocks[j]
        nb_raw = nb.metadata.get("raw_type")
        if nb_raw == "title" or nb.type == "heading":
            break
        if nb.type in ("table", "image", "formula"):
            break
        if nb.text:
            content_parts.append(nb.text.strip())
            pages.add(nb.page_idx)
            if nb.bbox:
                bboxes.append((nb.bbox.x0, nb.bbox.y0, nb.bbox.x1, nb.bbox.y1))
            appended_text_blocks += 1
            if max_text_blocks is not None and appended_text_blocks >= max_text_blocks:
                j += 1
                break
        j += 1

    return content_parts, pages, bboxes, j
