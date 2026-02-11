from typing import Dict, List, Tuple, Optional

from .models import ContentBlock, DocumentTree, SectionNode
from .toc_extractor import infer_heading_level


class TreeBuilderJson:
    def build(self, toc_entries: List[Dict], blocks: List[ContentBlock], source: str) -> DocumentTree:
        if not toc_entries:
            root = SectionNode(
                title="Document",
                level=1,
                start_idx=0,
                end_idx=len(blocks) - 1,
                start_page=0,
                end_page=max((b.page_idx for b in blocks), default=0),
                path="Document",
            )
            for b in blocks:
                root.add_block(b)
            return DocumentTree(title="Document", root_sections=[root], all_blocks=blocks)

        if source == "headers":
            return self._build_from_headers(toc_entries, blocks)
        return self._build_from_toc_and_headers(toc_entries, blocks)

    def _build_from_toc_pages(self, toc: List[Dict], blocks: List[ContentBlock]) -> DocumentTree:
        roots = self._build_skeleton(toc)
        self._calculate_page_ranges(roots, total_pages=max((b.page_idx for b in blocks), default=0) + 1)
        self._assign_blocks_by_page(roots, blocks)
        return DocumentTree(title="Document", root_sections=roots, all_blocks=blocks)

    def _build_from_toc_and_headers(self, toc: List[Dict], blocks: List[ContentBlock]) -> DocumentTree:
        id_to_index: Dict[str, int] = {b.id: i for i, b in enumerate(blocks)}

        # Build TOC entries with start_idx
        entries: List[Dict] = []
        matched_ids = set()
        for entry in toc:
            title = entry.get("title", "")
            page = int(entry.get("page", 1))
            matched_id = entry.get("matched_block_id")
            start_idx = None
            if matched_id and matched_id in id_to_index:
                start_idx = id_to_index[matched_id]
                matched_ids.add(matched_id)
            else:
                # fallback by page
                start_idx = self._find_first_index_by_page(blocks, page - 1)
            if start_idx is None:
                continue
            entries.append({
                "title": title,
                "level": int(entry.get("level", 1)),
                "start_idx": start_idx,
                "start_page": blocks[start_idx].page_idx,
                "source": "toc",
            })

        # Add header-derived entries not already matched
        for i, b in enumerate(blocks):
            if b.type != "heading":
                continue
            if b.id in matched_ids:
                continue
            title = b.text.strip()
            if not title:
                continue
            entries.append({
                "title": title,
                "level": infer_heading_level(title),
                "start_idx": i,
                "start_page": b.page_idx,
                "source": "header",
            })

        if not entries:
            return self._build_from_toc_pages(toc, blocks)

        # Sort by start_idx, then level
        entries.sort(key=lambda e: (e["start_idx"], e["level"]))

        # Build hierarchy from combined sequence
        roots: List[SectionNode] = []
        stack: List[Tuple[int, SectionNode]] = []
        nodes: List[SectionNode] = []

        for e in entries:
            node = SectionNode(
                title=e["title"],
                level=e["level"],
                start_idx=e["start_idx"],
                end_idx=e["start_idx"],
                start_page=e["start_page"],
                end_page=e["start_page"],
            )
            while stack and stack[-1][0] >= node.level:
                stack.pop()
            if stack:
                parent = stack[-1][1]
                parent.add_child(node)
                node.path = f"{parent.path} > {node.title}".strip()
            else:
                roots.append(node)
                node.path = node.title.strip()
            stack.append((node.level, node))
            nodes.append(node)

        # Assign ranges by index order
        self._assign_blocks_by_index(roots, blocks)
        return DocumentTree(title="Document", root_sections=roots, all_blocks=blocks)

    def _build_from_headers(self, toc: List[Dict], blocks: List[ContentBlock]) -> DocumentTree:
        roots: List[SectionNode] = []
        stack: List[Tuple[int, SectionNode]] = []

        header_positions: List[Tuple[int, Dict]] = []
        title_page_to_idx: Dict[Tuple[str, int], int] = {}
        for i, b in enumerate(blocks):
            if b.type == "heading":
                key = (b.text.strip(), b.page_idx + 1)
                if key not in title_page_to_idx:
                    title_page_to_idx[key] = i

        for entry in toc:
            key = (entry["title"].strip(), entry["page"])
            idx = title_page_to_idx.get(key)
            if idx is None:
                continue
            header_positions.append((idx, entry))

        header_positions.sort(key=lambda x: x[0])

        nodes: List[SectionNode] = []
        for pos, entry in header_positions:
            node = SectionNode(
                title=entry["title"],
                level=entry["level"],
                start_idx=pos,
                end_idx=pos,
                start_page=entry["page"] - 1,
                end_page=entry["page"] - 1,
            )
            while stack and stack[-1][0] >= node.level:
                stack.pop()
            if stack:
                parent = stack[-1][1]
                parent.add_child(node)
                node.path = f"{parent.path} > {node.title}".strip()
            else:
                roots.append(node)
                node.path = node.title.strip()
            stack.append((node.level, node))
            nodes.append(node)

        for i, node in enumerate(nodes):
            if i < len(nodes) - 1:
                node.end_idx = nodes[i + 1].start_idx - 1
            else:
                node.end_idx = len(blocks) - 1

        for node in nodes:
            for b in blocks[node.start_idx: node.end_idx + 1]:
                node.add_block(b)
            if node.blocks:
                node.start_page = min(b.page_idx for b in node.blocks)
                node.end_page = max(b.page_idx for b in node.blocks)

        return DocumentTree(title="Document", root_sections=roots, all_blocks=blocks)

    def _build_skeleton(self, toc: List[Dict]) -> List[SectionNode]:
        roots: List[SectionNode] = []
        stack: List[Tuple[int, SectionNode]] = []
        for entry in toc:
            node = SectionNode(
                title=entry["title"],
                level=int(entry["level"]),
                start_idx=0,
                end_idx=0,
                start_page=int(entry["page"]) - 1,
                end_page=-1,
            )
            while stack and stack[-1][0] >= node.level:
                stack.pop()
            if not stack:
                roots.append(node)
                node.path = node.title.strip()
            else:
                parent = stack[-1][1]
                parent.add_child(node)
                node.path = f"{parent.path} > {node.title}".strip()
            stack.append((node.level, node))
        return roots

    def _calculate_page_ranges(self, nodes: List[SectionNode], total_pages: int, parent_end: int = -1) -> None:
        if parent_end == -1:
            parent_end = total_pages
        for i, node in enumerate(nodes):
            if i < len(nodes) - 1:
                next_sibling = nodes[i + 1]
                node.end_page = max(node.start_page, next_sibling.start_page - 1)
                if next_sibling.start_page == node.start_page:
                    node.end_page = node.start_page
            else:
                node.end_page = parent_end
            if node.children:
                self._calculate_page_ranges(node.children, total_pages, parent_end=node.end_page)

    def _assign_blocks_by_page(self, roots: List[SectionNode], blocks: List[ContentBlock]) -> None:
        flat: List[SectionNode] = []

        def collect(n: SectionNode) -> None:
            flat.append(n)
            for c in n.children:
                collect(c)

        for r in roots:
            collect(r)
        flat.sort(key=lambda s: s.start_page)

        for b in blocks:
            candidates = [s for s in flat if s.start_page <= b.page_idx <= s.end_page]
            if not candidates:
                continue
            candidates.sort(key=lambda s: (s.start_page, s.level), reverse=True)
            candidates[0].add_block(b)

    def _assign_blocks_by_index(self, roots: List[SectionNode], blocks: List[ContentBlock]) -> None:
        flat = self._flatten_nodes(roots)
        flat = [n for n in flat if n.start_idx is not None]
        flat.sort(key=lambda s: s.start_idx)

        # Fill missing start_idx by previous
        for i, n in enumerate(flat):
            if n.start_idx is None:
                n.start_idx = flat[i - 1].start_idx if i > 0 else 0

        for i, n in enumerate(flat):
            if i < len(flat) - 1:
                n.end_idx = max(n.start_idx, flat[i + 1].start_idx - 1)
            else:
                n.end_idx = len(blocks) - 1

        for n in flat:
            for b in blocks[n.start_idx : n.end_idx + 1]:
                n.add_block(b)
            if n.blocks:
                n.start_page = min(b.page_idx for b in n.blocks)
                n.end_page = max(b.page_idx for b in n.blocks)

    def _flatten_nodes(self, roots: List[SectionNode]) -> List[SectionNode]:
        flat: List[SectionNode] = []

        def collect(n: SectionNode) -> None:
            flat.append(n)
            for c in n.children:
                collect(c)

        for r in roots:
            collect(r)
        return flat

    def _find_first_index_by_page(self, blocks: List[ContentBlock], page_idx: int) -> Optional[int]:
        for i, b in enumerate(blocks):
            if b.page_idx >= page_idx:
                return i
        return None
