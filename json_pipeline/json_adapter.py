import re
from typing import Any, Dict, List, Tuple

from .models import BoundingBox, ContentBlock
from .cleaning import clean_block_text, clean_heading_text

PUNCT_END = (".", "!", "?", ":", ";", "\"", "”")


def join_spans(spans: List[Dict[str, Any]]) -> str:
    parts: List[str] = []
    for sp in spans:
        st = sp.get("type", "")
        content = sp.get("content", "")
        if not content:
            continue
        if st == "inline_equation":
            parts.append(f"${content}$")
        elif st == "interline_equation":
            parts.append(f"$${content}$$")
        else:
            parts.append(content)
    text = " ".join(parts)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def extract_text_from_block(block: Dict[str, Any]) -> str:
    if "lines" in block:
        lines = block.get("lines") or []
        line_texts = []
        for line in lines:
            spans = line.get("spans") or []
            line_texts.append(join_spans(spans))
        return "\n".join([t for t in line_texts if t])

    if block.get("type") == "image" and block.get("blocks"):
        sub_texts = []
        for sub in block.get("blocks", []):
            if sub.get("lines"):
                lines = sub.get("lines") or []
                for line in lines:
                    spans = line.get("spans") or []
                    sub_texts.append(join_spans(spans))
        return "\n".join([t for t in sub_texts if t])

    return ""


def extract_image_paths(block: Dict[str, Any]) -> List[str]:
    paths: List[str] = []
    for sub in block.get("blocks", []) or []:
        for line in sub.get("lines", []) or []:
            for sp in line.get("spans", []) or []:
                if sp.get("type") == "image" and sp.get("image_path"):
                    paths.append(sp["image_path"])
    return paths


def map_block_type(raw_type: str) -> str:
    t = (raw_type or "").lower()
    if t in ("title", "header"):
        return "heading"
    if t in ("text", "paragraph"):
        return "text"
    if t in ("list", "list_item"):
        return "list_item"
    if t in ("table",):
        return "table"
    if t in ("image", "picture"):
        return "image"
    if t in ("interline_equation", "equation"):
        return "formula"
    return "text"


def blocks_from_mineru_json(data: Dict[str, Any]) -> Tuple[List[ContentBlock], Dict[int, Tuple[float, float]]]:
    blocks: List[ContentBlock] = []
    page_sizes: Dict[int, Tuple[float, float]] = {}
    pages = data.get("pdf_info", [])

    for page in pages:
        page_idx = page.get("page_idx", 0)
        size = page.get("page_size") or [0, 0]
        if len(size) >= 2:
            page_sizes[page_idx] = (size[0], size[1])

        for b in page.get("para_blocks", []) or []:
            raw_type = b.get("type", "")
            mapped_type = map_block_type(raw_type)
            text = extract_text_from_block(b)
            if mapped_type == "heading":
                text = clean_heading_text(text)
            else:
                text = clean_block_text(text)
            bbox_raw = b.get("bbox") or [0, 0, 0, 0]
            bbox = BoundingBox(bbox_raw[0], bbox_raw[1], bbox_raw[2], bbox_raw[3], page_idx)
            block_id = f"p{page_idx}_b{b.get('index', len(blocks))}"
            metadata = {
                "raw_type": raw_type,
                "index": b.get("index"),
            }
            if mapped_type == "image":
                metadata["image_paths"] = extract_image_paths(b)
            blocks.append(ContentBlock(
                id=block_id,
                type=mapped_type,
                text=text,
                page_idx=page_idx,
                bbox=bbox,
                metadata=metadata,
            ))

    return blocks, page_sizes
