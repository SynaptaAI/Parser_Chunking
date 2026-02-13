from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
import json
import re
import sys

from .enricher_utils import enrich_anchor, resolve_visual_path, set_enrichment_status


def enrich_table_chunks(
    chunks: List[Dict[str, Any]],
    doc_id: str = "book",
    out_dir: Optional[Path] = None,
) -> None:
    extractor = _load_table_extractor()
    extracted_tables: List[Dict[str, Any]] = []
    if extractor is None:
        for chunk in chunks:
            if chunk.get("type") == "table":
                set_enrichment_status(chunk, "table", "skipped", "synapta_table_unavailable")
        _write_synapta_table_outputs(extracted_tables, doc_id=doc_id)
        return

    for chunk in chunks:
        if chunk.get("type") != "table":
            continue
        anchor = enrich_anchor(chunk, doc_id)
        image_path = resolve_visual_path(chunk, doc_id=doc_id, out_dir=out_dir)
        if not image_path:
            set_enrichment_status(chunk, "table", "skipped", "local_image_not_found")
            continue
        try:
            table_data = extractor(
                image_path=image_path,
                page_number=anchor["page_start"],
                caption=chunk.get("caption") or "",
            )
        except Exception as exc:
            set_enrichment_status(chunk, "table", "error", f"extract_failed:{type(exc).__name__}")
            continue

        if table_data:
            table_data["source_chunk_id"] = anchor["source_chunk_id"]
            table_data["page_start"] = anchor["page_start"]
            table_data["page_end"] = anchor["page_end"]
            table_data["heading_path"] = anchor["heading_path"]
            chunk["table_data"] = table_data
            extracted_tables.append(table_data)
            set_enrichment_status(chunk, "table", "ok")
        else:
            set_enrichment_status(chunk, "table", "empty", "no_table_payload")

    _write_synapta_table_outputs(extracted_tables, doc_id=doc_id)


def _load_table_extractor() -> Optional[Callable[..., Any]]:
    root = Path(__file__).resolve().parents[1] / "synapta-table-segmentation"
    if root.exists():
        sys.path.insert(0, str(root))
    try:
        from table_image_extractor import extract_table_from_image
        return extract_table_from_image
    except Exception:
        return None


def _write_synapta_table_outputs(tables: List[Dict[str, Any]], doc_id: str) -> None:
    root = Path(__file__).resolve().parents[1] / "synapta-table-segmentation" / "output"
    try:
        root.mkdir(parents=True, exist_ok=True)
    except Exception:
        return

    # Keep folder style consistent with original run_extraction.py outputs.
    for old in root.glob("table_*.json"):
        try:
            old.unlink()
        except Exception:
            pass
    for old in root.glob("table_*.md"):
        try:
            old.unlink()
        except Exception:
            pass

    for idx, table in enumerate(tables, 1):
        base = f"table_{idx}"
        json_path = root / f"{base}.json"
        md_path = root / f"{base}.md"
        try:
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(table, f, indent=2, ensure_ascii=False)
        except Exception:
            continue
        try:
            md_path.write_text(_table_dict_to_markdown(table), encoding="utf-8")
        except Exception:
            pass

    total_pages = 0
    if tables:
        total_pages = max(int(t.get("page_end") or t.get("page_start") or 0) for t in tables)
    summary = {
        "pdf_path": f"{doc_id}.pdf",
        "page_range": "all",
        "total_pages": total_pages,
        "total_tables": len(tables),
        "tables": [],
    }
    for t in tables:
        cells = t.get("cells") or []
        rows = len(cells)
        cols = len(cells[0]) if cells else len(t.get("col_headers") or [])
        cap = str(t.get("caption") or "")
        summary["tables"].append({
            "segment_id": t.get("segment_id"),
            "table_number": t.get("table_number"),
            "caption": cap[:100],
            "page": (t.get("source_anchor") or {}).get("page_number") or t.get("page_start"),
            "dimensions": f"{rows}×{cols}",
            "schema_hint": t.get("table_schema_hint"),
        })
    try:
        with open(root / "extraction_summary.json", "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def _table_dict_to_markdown(table: Dict[str, Any]) -> str:
    lines: List[str] = []
    caption = str(table.get("caption") or "")
    if caption:
        lines.append(f"**{caption}**")
        lines.append("")

    cells = table.get("cells") or []
    if not cells:
        return "\n".join(lines).rstrip() + ("\n" if lines else "")

    col_headers = [str(h) for h in (table.get("col_headers") or [])]
    header = col_headers if col_headers else [str(c) for c in cells[0]]
    lines.append("| " + " | ".join(header) + " |")
    lines.append("| " + " | ".join(["---"] * len(header)) + " |")

    start_row = 1 if col_headers and cells and [str(x) for x in cells[0]] == header else 0
    for row in cells[start_row:]:
        vals = [str(v) for v in row]
        if len(vals) < len(header):
            vals.extend([""] * (len(header) - len(vals)))
        elif len(vals) > len(header):
            vals = vals[:len(header)]
        lines.append("| " + " | ".join(vals) + " |")

    footnotes = table.get("footnotes") or []
    if footnotes:
        lines.append("")
        for i, fn in enumerate(footnotes, 1):
            lines.append(f"^{i}: {fn}")

    desc = str(table.get("description") or "")
    if desc:
        lines.append("")
        lines.append(f"*{desc}*")
    return "\n".join(lines) + "\n"
