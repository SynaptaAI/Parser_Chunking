The pipeline does two things in one run:
1. Builds unified MinerU outputs (`elements/chunks/qa/kg`) in `outputs/`.
2. Produces module-native outputs for `formula/image/table` in each Synapta folder.

**Module Responsibilities**  
`Segmentation_pipeline/`  
- Main orchestrator and schema normalizer.
- Cleans MinerU blocks, fixes layout, builds TOC/tree, chunks text, links references, runs enrichers, writes sidecars.

`synapta-formula-segmentation/`  
- Formula extraction/normalization and QA-derivation linking support.
- Integrated output mirror written to `synapta-formula-segmentation/outputs/`.

`synapta-image-segmentation/`  
- Visual analysis (OCR + visual classification + summary).
- Integrated output mirror written to `synapta-image-segmentation/output/`.

`synapta-table-segmentation/`  
- OCR-based table structure reconstruction.
- Integrated output mirror written to `synapta-table-segmentation/output/`.

**Pipeline Stages**  
Running `python -m Segmentation_pipeline` prints coarse progress logs:
1. Load and normalize MinerU JSON
2. Layout correction and visual crop preparation
3. TOC extraction and document tree build
4. Build elements
5. Build chunks
6. Run table/image/formula enrichers
7. Build QA/derivation sidecars
8. Extract metadata and write outputs

**Run Full Pipeline**  
```bash
cd /Users/keyvanzhuo/Documents/CodeProjects/Segmentation/MinerU
venv/bin/python -m Segmentation_pipeline
```

Input convention:
- MinerU JSON: `outputs/MinerU-Parser/<doc_id>.json`
- PDF: `inputs/<doc_id>.pdf`

**Output Layout**  
Unified pipeline outputs:
- `outputs/<doc_id>_elements.json`
- `outputs/<doc_id>_chunks.json`
- `outputs/<doc_id>_qa_segments.json`
- `outputs/<doc_id>_kg_segments.json`
- `outputs/<doc_id>_metadata.json`
- `outputs/visuals/<doc_id>/` (local crops)

Synapta-native mirrored outputs:
- Formula: `synapta-formula-segmentation/outputs/<doc_id>_segments.json`
  - Shape: `metadata`, `chapters`, `segments`, `edges`
- Image: `synapta-image-segmentation/output/<doc_id>_visual_segments.json`
- Image CSV: `synapta-image-segmentation/output/<doc_id>_visual_summary.csv`
- Table summary: `synapta-table-segmentation/output/extraction_summary.json`
- Table per-item files: `synapta-table-segmentation/output/table_<n>.json` and `table_<n>.md`

**Integration Contract (Chunk-Level Writeback)**  
Each enricher writes back into the corresponding chunk:
- `formula` -> `chunk.synapta_formula`
- `image` -> `chunk.image_data`
- `table` -> `chunk.table_data`

Each enricher also writes per-chunk status:
- `chunk.enrichment_status.formula`
- `chunk.enrichment_status.image`
- `chunk.enrichment_status.table`

Status values are coarse (`ok`, `skipped`, `empty`, `error`) and do not stop the pipeline.

**Reference Linking**  
`Segmentation_pipeline/reference_extractor.py` extracts and links:
- `Figure/Fig.`
- `Table/Tbl.`
- `Equation/Eq.` including `Eq. (x.y)` style
- `Appendix`

Links are attached as `ref_target_id` in chunk `references`.

**Validation**  
```bash
venv/bin/python scripts/check_qa_sidecar.py outputs/Investments_qa_segments.json --min-match-rate 0.98
venv/bin/python scripts/check_kg_sidecar.py outputs/Investments_kg_segments.json
```

**Known Warnings / Runtime Notes**  
- `NotOpenSSLWarning` from `urllib3`: compatibility warning on local Python SSL build, usually non-fatal.
- `Mistral API error: 401`: invalid/unauthorized key; pipeline continues with degraded visual/LLM enrichment.
- Missing external metadata APIs (Google/OpenLibrary): metadata falls back to local extraction (typically ISBN-first).

**Design Principle**  
Priority is end-to-end robustness:
- Pipeline always produces unified outputs.
- Submodule errors degrade gracefully and are surfaced via `enrichment_status`.
- Module-native mirrored outputs are still emitted for audit and regression comparison.
