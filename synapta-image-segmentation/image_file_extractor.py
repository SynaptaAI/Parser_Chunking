"""
Analyze a single image file using the Synapta visual pipeline (OCR + Mistral Vision).
Returns a JSON-serializable dict similar to VisualSegment.to_dict().
"""

from typing import Any, Dict, Optional
from pathlib import Path

import os
from PIL import Image

from pdf_image_segmentation import (
    OCRResult,
    VisualSegment,
    VisualType,
    BoundingBox,
    MistralVisionAPI,
)


_OCR = None


def _get_ocr() -> Any:
    global _OCR
    if _OCR is None:
        # Ensure Paddle/PaddleX cache is writable
        base = Path(__file__).resolve().parents[1] / "outputs" / "paddle_cache"
        base.mkdir(parents=True, exist_ok=True)
        os.environ["PADDLE_HOME"] = str(base)
        os.environ["PADDLEX_HOME"] = str(base)
        os.environ["PADDLEOCR_HOME"] = str(base)
        os.environ["XDG_CACHE_HOME"] = str(base)
        os.environ["HOME"] = str(base)
        os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
        try:
            from paddleocr import PaddleOCR
            _OCR = PaddleOCR(use_angle_cls=True, lang="en")
        except Exception:
            _OCR = None
    return _OCR


def analyze_image_file(
    image_path: str,
    caption: str = "",
    page_no: int = 1,
    heading_path: Optional[str] = None,
    book_id: str = "book",
) -> Optional[Dict[str, Any]]:
    img_path = Path(image_path)
    if not img_path.exists():
        return None

    image = Image.open(img_path).convert("RGB")

    # OCR
    ocr_engine = _get_ocr()
    if ocr_engine:
        try:
            ocr_raw = ocr_engine.ocr(str(img_path), cls=True)
            ocr_text, ocr_blocks = _flatten_ocr(ocr_raw)
            ocr_result = OCRResult(raw_text=ocr_text, blocks=ocr_blocks, confidence=_avg_conf(ocr_blocks))
        except Exception:
            ocr_result = OCRResult(raw_text="", blocks=[], confidence=0.0)
    else:
        ocr_result = OCRResult(raw_text="", blocks=[], confidence=0.0)

    # Vision analysis
    vision = MistralVisionAPI()
    analysis = vision.analyze_visual_comprehensive(image, ocr_result)

    # Build VisualSegment
    bbox = BoundingBox(0, 0, image.width, image.height, image.width, image.height)
    segment = VisualSegment(
        segment_id=f"visual_{book_id}_p{page_no}_{img_path.stem}",
        segment_type=analysis.get("visual_type", VisualType.UNKNOWN),
        book_id=book_id,
        page_no=page_no,
        bbox=bbox,
        image_path=str(img_path),
        caption_text=caption or None,
        ocr_result=ocr_result,
        summary=analysis.get("summary"),
        summary_confidence=analysis.get("summary_confidence", 0.0),
        classification_confidence=analysis.get("confidence", 0.0),
        classification_method=analysis.get("classification_method", "mistral"),
    )

    # Attach type-specific data from analysis if present
    meta = analysis.get("metadata") or {}
    _apply_metadata(segment, meta)

    if heading_path:
        segment.heading_path = [p.strip() for p in heading_path.split(" > ") if p.strip()]

    return segment.to_dict()


def _flatten_ocr(ocr_raw) -> tuple[str, list]:
    text_lines = []
    blocks = []
    if not ocr_raw:
        return "", []
    for item in ocr_raw:
        if not item:
            continue
        for box, (txt, conf) in item:
            text_lines.append(txt)
            blocks.append({
                "text": txt,
                "confidence": float(conf) if conf is not None else 0.0,
            })
    return "\n".join(text_lines), blocks


def _avg_conf(blocks) -> float:
    if not blocks:
        return 0.0
    return sum(b.get("confidence", 0.0) for b in blocks) / max(len(blocks), 1)


def _apply_metadata(segment: VisualSegment, meta: Dict[str, Any]) -> None:
    # pdf_image_segmentation expects type-specific data objects; we keep raw meta on to_dict output.
    # Use the metadata as-is by setting extracted_text_structured to keep info without deep parsing.
    if meta:
        segment.extracted_text_structured = {"vision_metadata": meta}
