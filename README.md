**Architecture Overview**  
This project is a segmentation system orchestrated by `json_pipeline`. The pipeline builds document structure, segments content, and links references from MinerU outputs, then calls three submodules for enrich. The submodules return structured results that are written back into chunks, producing a unified JSON output.

**Module Responsibilities**  
`json_pipeline/`: orchestration and data-structure layer, including cleaning, layout correction, TOC/heading alignment, tree construction, segmentation, and reference linking.  
`synapta-image-segmentation/`: image/chart/diagram recognition and OCR, producing structured visual descriptions and summaries.  
`synapta-table-segmentation/`: table detection and structured reconstruction, producing serializable table structures.  
`synapta-formula-segmentation/`: formula/worked-example extraction and normalization, producing structured formula segments.  

**Collaboration Flow (Technical Mainline)**  
1. `json_pipeline` reads `outputs/MinerU-Parser/*.json`, parses into `ContentBlock`, and performs text cleaning and layout stitching to stabilize page order and paragraph boundaries.  
2. If the matching PDF exists, page sizes are loaded and image/table/formula regions are cropped to `outputs/visuals/<doc_id>/` for later enrich.  
3. TOC is preferred from the PDF and aligned to headings; if missing, heading blocks are used to infer hierarchy, building `DocumentTree` with full `heading_path`.  
4. The tree emits `elements` (fine-grained) and `chunks` (semantic aggregation), with type tagging for lists/steps/definitions.  
5. References are extracted on `chunks` (Figure/Table/Eq), and cross-segment reference links are created (referrer → target).  
6. Enrich is triggered by chunk type: image → `synapta-image-segmentation`, table → `synapta-table-segmentation`, formula → `synapta-formula-segmentation`.  
7. Submodule results are written back into the corresponding chunk fields, producing the final structured JSON output.  

**Key Control Points / Optional Paths / Fallbacks**  
Control: visual cropping runs only if the PDF exists; otherwise the pipeline stays text/structure-only.  
Control: TOC-first, heading-fallback ensures `heading_path` is always available.  
Control: enrich is invoked only when both type and inputs are valid.  
Optional: image/table/formula enrich can be enabled or skipped independently.  
Optional: metadata fetch and external API dependencies can be disabled or run offline.  
Fallback: missing submodules/dependencies will skip enrich without breaking `chunks` generation.  
Fallback: crop failures do not write enrich fields, keeping the main output stable.  

**Integration Boundaries & Interfaces**  
image enrich: triggered when `chunk.type == image` and a local crop exists.  
Input: crop path, caption, page_no, heading_path, doc_id.  
Method: PaddleOCR for text + Mistral Vision for visual classification/summary, assembled into structured visual metadata.  
Output: writes to `chunk.image_data` (visual type, OCR text/blocks, summary, confidence).  
table enrich: triggered when `chunk.type == table` and a local crop exists.  
Input: crop path, page_no, caption.  
Method: Tesseract OCR + row/column clustering to reconstruct table structure and produce headers/cells/metadata.  
Output: writes to `chunk.table_data` (serializable table structure).  
formula enrich: triggered when `chunk.type == formula` and text is non-empty.  
Input: formula text, page_no, bbox, heading_path, doc_id.  
Method: equation-number regex + normalized hashing + variable symbol extraction to build formula segments.  
Output: writes to `chunk.synapta_formula`.  
Note: if inputs are incomplete or dependencies are missing, the corresponding enrich is skipped.  

**Outputs**  
`outputs/*_elements.json`: fine-grained element sequence.  
`outputs/*_chunks.json`: semantically aggregated chunks (including enrich results).  
`outputs/visuals/<doc_id>/`: cropped images/tables/formulas for enrich.  
