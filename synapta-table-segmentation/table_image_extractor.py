"""
Extract table structure directly from a cropped table image.
This bypasses PDF parsing and only uses OCR + light structure reconstruction.
"""

from typing import Any, Dict, List, Optional
from pathlib import Path
import numpy as np

try:
    import pytesseract
    from PIL import Image
    OCR_AVAILABLE = True
except Exception:
    OCR_AVAILABLE = False
    pytesseract = None
    Image = None

from table_segment import (
    TableSegment, CellMeta, SourceAnchor, generate_segment_id,
    normalize_cell_value, detect_cell_type
)
from table_serializer import table_to_json


def extract_table_from_image(
    image_path: str,
    page_number: int = 1,
    caption: str = "",
) -> Optional[Dict[str, Any]]:
    if not OCR_AVAILABLE:
        return None
    img_path = Path(image_path)
    if not img_path.exists():
        return None

    image = Image.open(img_path).convert("RGB")
    ocr_data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
    table_data = _ocr_words_to_table(
        ocr_data,
        page_width=image.width,
        page_height=image.height,
        image_width=image.width,
        image_height=image.height,
    )
    if not table_data or len(table_data.get("cells", [])) < 2:
        return None

    segment = _reconstruct_from_ocr(table_data, page_number=page_number, caption=caption)
    if not segment:
        return None

    return table_to_json(segment)


def _ocr_words_to_table(
    ocr_data: Dict[str, List[Any]],
    page_width: float,
    page_height: float,
    image_width: int,
    image_height: int,
) -> Optional[Dict[str, Any]]:
    words = []
    scale_x = page_width / image_width if image_width else 1.0
    scale_y = page_height / image_height if image_height else 1.0

    for i, text in enumerate(ocr_data.get("text", [])):
        if not text or not text.strip():
            continue
        conf = ocr_data.get("conf", [None])[i]
        try:
            conf_val = float(conf)
        except (TypeError, ValueError):
            conf_val = -1
        if conf_val != -1 and conf_val < 40:
            continue
        x = ocr_data["left"][i]
        y = ocr_data["top"][i]
        w = ocr_data["width"][i]
        h = ocr_data["height"][i]
        x0 = x * scale_x
        y0 = y * scale_y
        x1 = (x + w) * scale_x
        y1 = (y + h) * scale_y
        words.append({
            "text": text.strip(),
            "x0": x0, "y0": y0, "x1": x1, "y1": y1,
            "yc": (y0 + y1) / 2,
            "xc": (x0 + x1) / 2,
            "w": (x1 - x0),
            "h": (y1 - y0),
        })

    if len(words) < 5:
        return None

    words.sort(key=lambda w: w["yc"])
    heights = [w["h"] for w in words]
    median_h = np.median(heights) if heights else 8
    row_threshold = max(4.0, median_h * 0.6)
    rows = []
    current_row = []
    current_y = None

    for word in words:
        if current_y is None or abs(word["yc"] - current_y) <= row_threshold:
            current_row.append(word)
            current_y = word["yc"] if current_y is None else (current_y + word["yc"]) / 2
        else:
            rows.append(current_row)
            current_row = [word]
            current_y = word["yc"]
    if current_row:
        rows.append(current_row)

    if len(rows) < 2:
        return None

    x_positions = [w["x0"] for w in words]
    median_w = np.median([w["w"] for w in words]) if words else 10
    col_threshold = max(8.0, median_w * 1.2)
    col_centers: List[float] = []
    for x in sorted(x_positions):
        placed = False
        for i, center in enumerate(col_centers):
            if abs(x - center) <= col_threshold:
                col_centers[i] = (center + x) / 2
                placed = True
                break
        if not placed:
            col_centers.append(x)

    col_centers = sorted(col_centers)
    if len(col_centers) < 1:
        return None

    cells = []
    cell_bboxes = []
    for row in rows:
        row_cells = [""] * len(col_centers)
        row_bboxes = [None] * len(col_centers)
        row.sort(key=lambda w: w["xc"])
        for word in row:
            col_idx = min(range(len(col_centers)), key=lambda i: abs(word["x0"] - col_centers[i]))
            if row_cells[col_idx]:
                row_cells[col_idx] += " " + word["text"]
            else:
                row_cells[col_idx] = word["text"]
            bbox = row_bboxes[col_idx]
            if bbox is None:
                row_bboxes[col_idx] = (word["x0"], word["y0"], word["x1"], word["y1"])
            else:
                x0, y0, x1, y1 = bbox
                row_bboxes[col_idx] = (
                    min(x0, word["x0"]), min(y0, word["y0"]),
                    max(x1, word["x1"]), max(y1, word["y1"])
                )
        cells.append(row_cells)
        cell_bboxes.append(row_bboxes)

    avg_filled = np.mean([sum(1 for c in row if c.strip()) for row in cells])
    if avg_filled < 1.5:
        return None

    non_empty_rows = sum(1 for row in cells if any(c.strip() for c in row))
    if non_empty_rows < 2:
        return None

    all_bboxes = [b for row in cell_bboxes for b in row if b]
    if not all_bboxes:
        return None
    x0 = min(b[0] for b in all_bboxes)
    y0 = min(b[1] for b in all_bboxes)
    x1 = max(b[2] for b in all_bboxes)
    y1 = max(b[3] for b in all_bboxes)

    return {
        "cells": cells,
        "cell_bboxes": cell_bboxes,
        "bbox": (x0, y0, x1, y1)
    }


def _reconstruct_from_ocr(region: Dict, page_number: int, caption: str) -> Optional[TableSegment]:
    cells = region.get("cells") or []
    cell_bboxes = region.get("cell_bboxes") or []
    if not cells:
        return None

    col_headers = [str(h) for h in (cells[0] if cells else [])]
    row_headers = []
    if len(cells) > 1:
        row_headers = [[str(row[0])] for row in cells[1:]] if cells[0] else []

    cell_meta = []
    for r_idx, row in enumerate(cells):
        for c_idx, cell_text in enumerate(row):
            bbox = None
            if r_idx < len(cell_bboxes) and c_idx < len(cell_bboxes[r_idx]):
                bbox = cell_bboxes[r_idx][c_idx]
            cell_type = detect_cell_type(cell_text)
            normalized = normalize_cell_value(cell_text, cell_type)
            meta = CellMeta(
                row_index=r_idx,
                col_index=c_idx,
                bbox=bbox or (0, 0, 0, 0),
                cell_type=cell_type,
                raw_value=cell_text,
                normalized_value=normalized
            )
            cell_meta.append(meta)

    source_anchor = SourceAnchor(
        page_number=page_number,
        bbox=region.get("bbox") or (0, 0, 0, 0),
        extractor="ocr_image",
        confidence=0.6
    )

    segment_id = generate_segment_id(None, page_number, 0)
    table_segment = TableSegment(
        segment_id=segment_id,
        caption=caption or "",
        col_headers=col_headers,
        row_headers=row_headers,
        cells=cells,
        cell_meta=cell_meta,
        source_anchor=source_anchor,
        page_span=(page_number, page_number)
    )
    return table_segment
