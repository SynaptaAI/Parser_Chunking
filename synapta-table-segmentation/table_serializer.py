"""
Serialization utilities for TableSegment to Markdown and JSON
"""

from table_segment import TableSegment, CellMeta, FormulaCell, DerivedColumn, TableLink
from typing import Dict, Any
import json


def table_to_markdown(table: TableSegment) -> str:
    """Convert table segment to Markdown format"""
    lines = []
    
    # Add caption if present
    if table.caption:
        lines.append(f"**{table.caption}**\n")
    
    if not table.cells:
        return "\n".join(lines)
    
    # Build markdown table
    # Header row
    if table.col_headers:
        # Convert all headers to strings (some may be integers)
        header_strs = [str(h) for h in table.col_headers]
        header_row = "| " + " | ".join(header_strs) + " |"
        lines.append(header_row)
        # Separator
        separator = "| " + " | ".join(["---"] * len(table.col_headers)) + " |"
        lines.append(separator)
    
    # Data rows
    start_row = 1 if table.col_headers and len(table.cells) > 0 and table.cells[0] == table.col_headers else 0
    for row in table.cells[start_row:]:
        row_str = "| " + " | ".join(str(cell) for cell in row) + " |"
        lines.append(row_str)
    
    # Add footnotes if present
    if table.footnotes:
        lines.append("")
        for i, footnote in enumerate(table.footnotes, 1):
            lines.append(f"^{i}: {footnote}")
    
    # Add description
    if table.description:
        lines.append("")
        lines.append(f"*{table.description}*")
    
    return "\n".join(lines)


def table_to_json(table: TableSegment) -> Dict[str, Any]:
    """Convert table segment to JSON format"""
    def serialize_bbox(bbox):
        if bbox is None:
            return None
        return {"x0": bbox[0], "y0": bbox[1], "x1": bbox[2], "y1": bbox[3]}
    
    def serialize_source_anchor(anchor):
        if anchor is None:
            return None
        return {
            "page_number": anchor.page_number,
            "bbox": serialize_bbox(anchor.bbox),
            "extractor": anchor.extractor,
            "confidence": anchor.confidence,
            "metadata": anchor.metadata
        }
    
    def serialize_cell_meta(meta: CellMeta):
        return {
            "row_index": meta.row_index,
            "col_index": meta.col_index,
            "bbox": serialize_bbox(meta.bbox),
            "cell_type": meta.cell_type.value,
            "raw_value": meta.raw_value,
            "normalized_value": meta.normalized_value,
            "rowspan": meta.rowspan,
            "colspan": meta.colspan,
            "formula_text": meta.formula_text,
            "units": meta.units
        }
    
    def serialize_formula_cell(formula: FormulaCell):
        return {
            "cell_address": formula.cell_address,
            "formula_text": formula.formula_text,
            "bbox": serialize_bbox(formula.bbox),
            "source_anchor": serialize_source_anchor(formula.source_anchor),
            "confidence": formula.confidence
        }
    
    def serialize_derived_column(derived: DerivedColumn):
        return {
            "column_index": derived.column_index,
            "column_header": derived.column_header,
            "rule_description": derived.rule_description,
            "input_columns": derived.input_columns,
            "confidence": derived.confidence,
            "is_inferred": derived.is_inferred
        }
    
    def serialize_link(link: TableLink):
        return {
            "link_type": link.link_type.value,
            "target_id": link.target_id,
            "source_anchor": serialize_source_anchor(link.source_anchor),
            "evidence": link.evidence,
            "confidence": link.confidence
        }
    
    return {
        "segment_id": table.segment_id,
        "table_id": table.table_id,
        "table_number": table.table_number,
        "caption": table.caption,
        "row_headers": table.row_headers,
        "col_headers": table.col_headers,
        "cells": table.cells,
        "cell_meta": [serialize_cell_meta(meta) for meta in table.cell_meta],
        "footnotes": table.footnotes,
        "units": table.units,
        "formula_cells": [serialize_formula_cell(f) for f in table.formula_cells],
        "derived_columns": [serialize_derived_column(d) for d in table.derived_columns],
        "variable_candidates": table.variable_candidates,
        "description": table.description,
        "table_summary": table.table_summary,
        "table_schema_hint": table.table_schema_hint,
        "linked_concept_ids": table.linked_concept_ids,
        "links": [serialize_link(link) for link in table.links],
        "source_anchor": serialize_source_anchor(table.source_anchor),
        "confidence": table.confidence,
        "heading_path": table.heading_path,
        "page_span": list(table.page_span) if table.page_span else None
    }


def save_table_output(table: TableSegment, output_dir: str, base_name: str = None):
    """Save table in both Markdown and JSON formats"""
    import os
    from pathlib import Path
    
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    if base_name is None:
        base_name = table.segment_id
    
    # Save Markdown
    md_content = table_to_markdown(table)
    md_path = output_path / f"{base_name}.md"
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(md_content)
    
    # Save JSON
    json_content = table_to_json(table)
    json_path = output_path / f"{base_name}.json"
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(json_content, f, indent=2, ensure_ascii=False)
    
    return str(md_path), str(json_path)





