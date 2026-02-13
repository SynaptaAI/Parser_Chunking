from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
import csv
import json
import sys

from .enricher_utils import enrich_anchor, resolve_visual_path, set_enrichment_status


def enrich_image_chunks(
    chunks: List[Dict[str, Any]],
    doc_id: str = "book",
    out_dir: Optional[Path] = None,
) -> None:
    analyzer = _load_image_analyzer()
    extracted_images: List[Dict[str, Any]] = []
    if analyzer is None:
        for chunk in chunks:
            if chunk.get("type") == "image":
                set_enrichment_status(chunk, "image", "skipped", "synapta_image_unavailable")
        _write_synapta_image_outputs(extracted_images, doc_id=doc_id)
        return

    for chunk in chunks:
        if chunk.get("type") != "image":
            continue
        anchor = enrich_anchor(chunk, doc_id)
        image_path = resolve_visual_path(chunk, doc_id=doc_id, out_dir=out_dir)
        if not image_path:
            set_enrichment_status(chunk, "image", "skipped", "local_image_not_found")
            continue
        try:
            result = analyzer(
                image_path=image_path,
                caption=chunk.get("caption") or "",
                page_no=anchor["page_start"],
                heading_path=anchor["heading_path"],
                book_id=doc_id or "book",
            )
        except Exception as exc:
            set_enrichment_status(chunk, "image", "error", f"analyze_failed:{type(exc).__name__}")
            continue

        if result:
            result["source_chunk_id"] = anchor["source_chunk_id"]
            result["page_no"] = anchor["page_start"]
            result["page_start"] = anchor["page_start"]
            result["page_end"] = anchor["page_end"]
            result["heading_path"] = anchor["heading_path"]
            chunk["image_data"] = result
            extracted_images.append(result)
            set_enrichment_status(chunk, "image", "ok")
        else:
            set_enrichment_status(chunk, "image", "empty", "no_image_payload")

    _write_synapta_image_outputs(extracted_images, doc_id=doc_id)


def _load_image_analyzer() -> Optional[Callable[..., Any]]:
    root = Path(__file__).resolve().parents[1] / "synapta-image-segmentation"
    if root.exists():
        sys.path.insert(0, str(root))
    try:
        from image_file_extractor import analyze_image_file
        return analyze_image_file
    except Exception:
        return None


def _write_synapta_image_outputs(segments: List[Dict[str, Any]], doc_id: str) -> None:
    root = Path(__file__).resolve().parents[1] / "synapta-image-segmentation" / "output"
    try:
        root.mkdir(parents=True, exist_ok=True)
    except Exception:
        return

    json_path = root / f"{doc_id}_visual_segments.json"
    payload = {
        "book_id": doc_id,
        "pdf_path": f"{doc_id}.pdf",
        "total_segments": len(segments),
        "segments": segments,
    }
    try:
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
    except Exception:
        return

    csv_path = root / f"{doc_id}_visual_summary.csv"
    try:
        with open(csv_path, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(
                f,
                fieldnames=[
                    "segment_id",
                    "page",
                    "type",
                    "confidence",
                    "figure_number",
                    "caption",
                    "linked_concepts",
                    "summary",
                ],
            )
            w.writeheader()
            for seg in segments:
                w.writerow(
                    {
                        "segment_id": seg.get("segment_id"),
                        "page": seg.get("page_no"),
                        "type": seg.get("segment_type"),
                        "confidence": seg.get("classification_confidence"),
                        "figure_number": seg.get("figure_number") or "",
                        "caption": (seg.get("caption_text") or "")[:100],
                        "linked_concepts": len(seg.get("linked_concept_ids") or []),
                        "summary": (seg.get("summary") or "")[:100],
                    }
                )
    except Exception:
        pass
