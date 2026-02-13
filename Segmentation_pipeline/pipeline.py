import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .json_adapter import blocks_from_mineru_json
from .cleaning import filter_blocks, is_back_matter_title, is_main_body_title, is_special_term_text
from .layout_corrector import LayoutCorrectorJson
from .models import DocumentTree, SectionNode
from .output_builder import ChunkerJson, build_elements, finalize_segments, mark_numbered_lists
from .reference_extractor import link_references
from .metadata_extractor import extract_metadata
from .toc_extractor import align_toc_to_headers, extract_toc_from_headers, extract_toc_from_pdf
from .tree_builder import TreeBuilderJson
from .visual_extractor import extract_visual_crops
from .table_enricher import enrich_table_chunks
from .image_enricher import enrich_image_chunks
from .formula_enricher import enrich_formula_chunks
from .qa_derivation_enricher import enrich_qa_derivation
from .enricher_utils import chapter_from_heading_path, normalize_heading_path

logger = logging.getLogger(__name__)


def process_mineru_json(
    json_path: Path,
    pdf_path: Optional[Path],
    out_dir: Path,
    char_limit: int = 1500,
) -> Tuple[Path, Path]:
    logger.info("[1/8] Load and normalize MinerU JSON")
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    raw_blocks, page_sizes = blocks_from_mineru_json(data)
    blocks = filter_blocks(raw_blocks)

    logger.info("[2/8] Layout correction and visual crop preparation")
    layout = LayoutCorrectorJson()
    blocks = layout.process(blocks, page_sizes)
    if pdf_path and pdf_path.exists():
        visual_dir = out_dir / "visuals" / json_path.stem
        extract_visual_crops(pdf_path, blocks, visual_dir)

    logger.info("[3/8] TOC extraction and document tree build")
    toc_entries: List[Dict] = []
    toc_source = ""
    if pdf_path and pdf_path.exists():
        toc_entries = extract_toc_from_pdf(pdf_path)
        if toc_entries:
            toc_entries = align_toc_to_headers(toc_entries, blocks)
        toc_source = "pdf"

    if not toc_entries:
        toc_entries = extract_toc_from_headers(blocks)
        toc_source = "headers"

    main_body_page = _find_main_body_page(toc_entries)

    tree_builder = TreeBuilderJson()
    doc = tree_builder.build(toc_entries, blocks, toc_source)
    _filter_document_sections(doc, main_body_page)
    _prune_empty_sections(doc)

    logger.info("[4/8] Build elements")
    elements = finalize_segments(build_elements(doc))
    mark_numbered_lists(elements)
    link_references(elements)
    _annotate_traceability(elements, doc_id=json_path.stem, pdf_path=pdf_path)

    logger.info("[5/8] Build chunks")
    chunker = ChunkerJson(char_limit=char_limit)
    chunks = finalize_segments(chunker.chunk(doc))
    mark_numbered_lists(chunks)
    link_references(chunks)
    _annotate_traceability(chunks, doc_id=json_path.stem, pdf_path=pdf_path)

    logger.info("[6/8] Run table/image/formula enrichers")
    enrich_table_chunks(chunks, doc_id=json_path.stem, out_dir=out_dir)
    enrich_image_chunks(chunks, doc_id=json_path.stem, out_dir=out_dir)
    enrich_formula_chunks(chunks, doc_id=json_path.stem)
    # Re-link after enrichers so equation/caption-derived targets can be resolved.
    link_references(chunks)

    logger.info("[7/8] Build QA/derivation sidecars")
    enrich_qa_derivation(
        chunks=chunks,
        blocks=blocks,
        page_sizes=page_sizes,
        out_dir=out_dir,
        doc_id=json_path.stem,
    )

    logger.info("[8/8] Extract metadata and write outputs")
    metadata = extract_metadata(raw_blocks)

    out_dir.mkdir(parents=True, exist_ok=True)
    elements_path = out_dir / f"{json_path.stem}_elements.json"
    chunks_path = out_dir / f"{json_path.stem}_chunks.json"
    metadata_path = out_dir / f"{json_path.stem}_metadata.json"

    with open(elements_path, "w", encoding="utf-8") as f:
        json.dump({"doc_id": json_path.stem, "content": elements}, f, ensure_ascii=False, indent=2)

    with open(chunks_path, "w", encoding="utf-8") as f:
        payload = {"doc_id": json_path.stem}
        if metadata:
            payload["metadata"] = metadata
        payload["content"] = chunks
        json.dump(payload, f, ensure_ascii=False, indent=2)

    if metadata:
        with open(metadata_path, "w", encoding="utf-8") as f:
            json.dump({"doc_id": json_path.stem, "metadata": metadata}, f, ensure_ascii=False, indent=2)

    logger.info(
        "Completed %s: elements=%d chunks=%d",
        json_path.name,
        len(elements),
        len(chunks),
    )
    return elements_path, chunks_path


def run_default() -> None:
    base_dir = Path(__file__).resolve().parents[1]
    json_dir = base_dir / "outputs" / "MinerU-Parser"
    pdf_dir = base_dir / "inputs"
    out_dir = base_dir / "outputs"

    json_files = list(json_dir.glob("*.json"))
    if not json_files:
        logger.error("No JSON files found in %s", json_dir)
        return

    for json_path in json_files:
        pdf_path = pdf_dir / f"{json_path.stem}.pdf"
        logger.info("Processing %s (pdf=%s)", json_path.name, pdf_path.name if pdf_path.exists() else "missing")
        elements_path, chunks_path = process_mineru_json(
            json_path,
            pdf_path if pdf_path.exists() else None,
            out_dir,
        )
        logger.info("Wrote %s", elements_path)
        logger.info("Wrote %s", chunks_path)


def _filter_document_sections(doc: DocumentTree, main_body_page: Optional[int]) -> None:
    # Determine main body start and back matter start among root sections.
    roots = doc.root_sections
    if not roots:
        return

    main_start_idx = None
    back_start_idx = None

    for i, sec in enumerate(roots):
        if main_start_idx is None and is_main_body_title(sec.title):
            main_start_idx = i
        if back_start_idx is None and is_back_matter_title(sec.title):
            back_start_idx = i

    if main_start_idx is None:
        main_start_idx = 0
    if back_start_idx is None:
        back_start_idx = len(roots)

    # Keep only main body + back matter (with filtering)
    kept_roots = roots[main_start_idx:back_start_idx]
    back_roots = roots[back_start_idx:]

    # Drop any blocks before main body start page to avoid front-matter bleed
    if kept_roots:
        if main_body_page is not None:
            min_page = max(main_body_page - 1, 0)
        else:
            min_page = kept_roots[0].start_page
        for sec in kept_roots:
            _drop_blocks_before_page(sec, min_page)

    # Filter back matter blocks: keep formulas and special terms only
    for sec in back_roots:
        _filter_back_matter_section(sec)

    doc.root_sections = kept_roots + back_roots


def _filter_back_matter_section(section: SectionNode) -> None:
    filtered = []
    for b in section.blocks:
        if b.type == "formula":
            filtered.append(b)
        elif b.type == "text" and is_special_term_text(b.text):
            filtered.append(b)
        elif b.type in ("table", "image"):
            # Keep images/tables only if they contain special-term text
            if is_special_term_text(b.text):
                filtered.append(b)
    section.blocks = filtered
    for child in section.children:
        _filter_back_matter_section(child)


def _drop_blocks_before_page(section: SectionNode, min_page: int) -> None:
    section.blocks = [b for b in section.blocks if b.page_idx >= min_page]
    for child in section.children:
        _drop_blocks_before_page(child, min_page)


def _find_main_body_page(toc_entries: List[Dict]) -> Optional[int]:
    if not toc_entries:
        return None
    pages = []
    for entry in toc_entries:
        title = entry.get("title", "")
        if is_main_body_title(title):
            try:
                pages.append(int(entry.get("page", 0)))
            except Exception:
                continue
    return min(pages) if pages else None


def _prune_empty_sections(doc: DocumentTree) -> None:
    kept_roots: List[SectionNode] = []
    for root in doc.root_sections:
        if _prune_section(root):
            kept_roots.append(root)
    doc.root_sections = kept_roots


def _prune_section(section: SectionNode) -> bool:
    kept_children: List[SectionNode] = []
    for child in section.children:
        if _prune_section(child):
            kept_children.append(child)
    section.children = kept_children

    has_blocks = bool(section.blocks)
    has_children = bool(section.children)
    if not has_blocks and not has_children:
        return False

    pages: List[int] = [b.page_idx for b in section.blocks]
    for child in section.children:
        if child.start_page >= 0:
            pages.append(child.start_page)
        if child.end_page >= 0:
            pages.append(child.end_page)

    if pages:
        section.start_page = min(pages)
        section.end_page = max(pages)
    return True


def _annotate_traceability(
    segments: List[Dict],
    doc_id: str,
    pdf_path: Optional[Path],
) -> None:
    doc_uri = str(pdf_path) if pdf_path and pdf_path.exists() else None
    for seg in segments:
        seg["book_id"] = doc_id
        seg["doc_uri"] = doc_uri
        heading = normalize_heading_path(seg.get("heading_path") or "")
        if heading:
            seg["heading_path"] = heading
        chapter_number, chapter_title = chapter_from_heading_path(heading)
        if seg.get("chapter_number") in (None, "", "unknown"):
            seg["chapter_number"] = chapter_number
        # Keep backward-compat key used in current bridge logic.
        if seg.get("chapter_main") in (None, "", "unknown"):
            seg["chapter_main"] = chapter_number
        if not seg.get("chapter_title"):
            if chapter_title:
                seg["chapter_title"] = chapter_title
            elif heading:
                seg["chapter_title"] = heading.split(" > ")[0].strip()
