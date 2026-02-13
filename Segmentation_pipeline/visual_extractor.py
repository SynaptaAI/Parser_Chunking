from pathlib import Path
from typing import List, Optional

from .models import ContentBlock

try:
    import fitz  # PyMuPDF
except Exception:  # pragma: no cover - optional dependency
    fitz = None


def extract_visual_crops(
    pdf_path: Path,
    blocks: List[ContentBlock],
    out_dir: Path,
    dpi: int = 200,
) -> None:
    if not pdf_path or not pdf_path.exists():
        return
    if fitz is None:
        return

    out_dir.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(str(pdf_path))
    try:
        for b in blocks:
            if b.type not in ("image", "table", "formula"):
                continue
            if not b.bbox:
                continue
            page_idx = b.page_idx
            if page_idx < 0 or page_idx >= len(doc):
                continue
            page = doc[page_idx]
            rect = _safe_rect(b.bbox.x0, b.bbox.y0, b.bbox.x1, b.bbox.y1, page.rect)
            if rect is None:
                continue
            if _is_decorative(rect, page.rect):
                continue

            filename = f"{b.type}_p{page_idx + 1}_{b.id}.png"
            out_path = out_dir / filename
            try:
                pix = page.get_pixmap(clip=rect, dpi=dpi)
                pix.save(str(out_path))
            except Exception:
                continue

            local_paths = b.metadata.setdefault("local_image_paths", [])
            if str(out_path) not in local_paths:
                local_paths.append(str(out_path))
    finally:
        doc.close()


def _safe_rect(
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    page_rect,
) -> Optional["fitz.Rect"]:
    if fitz is None:
        return None
    px0 = max(x0, page_rect.x0)
    py0 = max(y0, page_rect.y0)
    px1 = min(x1, page_rect.x1)
    py1 = min(y1, page_rect.y1)
    if px1 <= px0 or py1 <= py0:
        return None
    return fitz.Rect(px0, py0, px1, py1)


def _is_decorative(rect, page_rect) -> bool:
    page_w = max(page_rect.width, 1.0)
    page_h = max(page_rect.height, 1.0)
    area_ratio = (rect.width * rect.height) / (page_w * page_h)

    # Tiny elements or very thin strips are likely decorative icons/lines.
    if area_ratio < 0.002:
        return True
    if rect.width < page_w * 0.03 or rect.height < page_h * 0.03:
        return True

    aspect = rect.width / rect.height if rect.height else 0
    if aspect > 12 or aspect < (1 / 12):
        return True
    return False
