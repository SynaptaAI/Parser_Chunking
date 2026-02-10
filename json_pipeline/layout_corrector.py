from typing import Dict, List, Tuple

from .json_adapter import PUNCT_END
from .models import ContentBlock


class LayoutCorrectorJson:
    def __init__(self):
        self.para_indent_threshold = 20.0

    def process(self, blocks: List[ContentBlock], page_sizes: Dict[int, Tuple[float, float]]) -> List[ContentBlock]:
        if not blocks:
            return []
        ordered = self._sort_blocks(blocks)
        stitched = self._stitch_blocks(ordered, page_sizes)
        return stitched

    def _sort_blocks(self, blocks: List[ContentBlock]) -> List[ContentBlock]:
        def key(b: ContentBlock):
            if b.metadata.get("index") is not None:
                return (b.page_idx, b.metadata.get("index"))
            if b.bbox:
                return (b.page_idx, b.bbox.y0, b.bbox.x0)
            return (b.page_idx, 0, 0)
        return sorted(blocks, key=key)

    def _stitch_blocks(self, blocks: List[ContentBlock], page_sizes: Dict[int, Tuple[float, float]]) -> List[ContentBlock]:
        if not blocks:
            return []
        stitched: List[ContentBlock] = []
        buffer = blocks[0]

        for nxt in blocks[1:]:
            should_merge = False
            if buffer.type == "text" and nxt.type == "text":
                text_end = buffer.text.strip()
                next_start = nxt.text.strip()
                ends_open = text_end and not text_end.endswith(PUNCT_END)
                starts_lower = next_start[:1].islower() if next_start else False

                is_adjacent_page = nxt.page_idx == buffer.page_idx + 1
                is_same_page = nxt.page_idx == buffer.page_idx

                if is_adjacent_page:
                    near_bottom = False
                    near_top = False
                    prev_size = page_sizes.get(buffer.page_idx)
                    next_size = page_sizes.get(nxt.page_idx)
                    if prev_size and buffer.bbox:
                        prev_h = prev_size[1] or 1
                        near_bottom = buffer.bbox.y1 >= prev_h * 0.9
                    if next_size and nxt.bbox:
                        next_h = next_size[1] or 1
                        near_top = nxt.bbox.y0 <= next_h * 0.1
                    if (ends_open or starts_lower) and (near_bottom or near_top):
                        should_merge = True
                elif is_same_page:
                    if starts_lower and buffer.bbox and nxt.bbox:
                        if abs(nxt.bbox.x0 - buffer.bbox.x0) <= self.para_indent_threshold:
                            should_merge = True

            if should_merge:
                if buffer.text.endswith("-"):
                    buffer.text = buffer.text[:-1] + nxt.text
                else:
                    buffer.text = f"{buffer.text} {nxt.text}".strip()
                buffer.metadata.setdefault("merged_ids", []).append(nxt.id)
            else:
                stitched.append(buffer)
                buffer = nxt
        stitched.append(buffer)
        return stitched
