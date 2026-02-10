from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class BoundingBox:
    x0: float
    y0: float
    x1: float
    y1: float
    page: int

    def to_list(self) -> List[float]:
        return [self.x0, self.y0, self.x1, self.y1]


@dataclass
class ContentBlock:
    id: str
    type: str
    text: str
    page_idx: int
    bbox: Optional[BoundingBox] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    sentences: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class SectionNode:
    title: str
    level: int
    start_idx: int
    end_idx: int
    start_page: int
    end_page: int
    parent: Optional["SectionNode"] = None
    children: List["SectionNode"] = field(default_factory=list)
    blocks: List[ContentBlock] = field(default_factory=list)
    path: str = ""

    def add_child(self, child: "SectionNode"):
        child.parent = self
        self.children.append(child)

    def add_block(self, block: ContentBlock):
        self.blocks.append(block)


@dataclass
class DocumentTree:
    title: str
    root_sections: List[SectionNode] = field(default_factory=list)
    all_blocks: List[ContentBlock] = field(default_factory=list)
