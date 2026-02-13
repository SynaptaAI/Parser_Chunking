# Table Extraction System - User Guide

## 📋 Overview

This system extracts tables from PDFs with full structure reconstruction, concept linking, and support for both text-based and image-based tables (via OCR). Uses **smart page type detection** to automatically choose the best extraction method for each page, making it fast and efficient even for large books (1000+ pages).

## 🚀 How to Run

### Step 1: Install Dependencies

```bash
# Install Python dependencies
pip install -r requirements.txt

# Install Tesseract OCR (required for image-based tables)
# Windows: Already installed via winget
# Or download from: https://github.com/UB-Mannheim/tesseract/wiki
```

### Step 2: Run Extraction

**Recommended: Pass PDF as argument**
```bash
python run_extraction.py "Zvi Bodie, Alex Kane, Alan J. Marcus - Investments (2023, McGraw Hill).pdf"
```

**Alternative: Place PDF in project folder**
- Rename your PDF to `book.pdf` or `document.pdf`
- Place it in the project root directory
- Run: `python run_extraction.py`

**Note:** The extraction will:
- Automatically detect page types (text/grid/image)
- Use the best extraction method for each page
- Show progress every 50 pages
- Save all tables to `output/` folder
- Complete in reasonable time (no hanging on large PDFs)

### Step 3: Check Output

All extracted tables are saved in the `output/` folder:
- `table_1.json`, `table_1.md` - First table (JSON + Markdown)
- `table_2.json`, `table_2.md` - Second table
- ...
- `extraction_summary.json` - Summary of all tables

**Note:** The `output/` folder is automatically cleared before each run, so old tables from previous extractions won't remain.

## 🏗️ System Architecture

### Smart Page Type Detection

The system uses **intelligent page type detection** to automatically choose the best extraction method, preventing hanging and improving speed:

#### Phase 1: Page Analysis (Samples First 100 Pages)

The system analyzes each page to detect its type:

- **Text-based Pages**: 
  - Detection: Pages with extractable text (>200 characters)
  - Method: Uses `pdfplumber` (fast, accurate for text tables)
  - Example: Most academic book pages with text-based tables

- **Grid-based Pages**: 
  - Detection: Pages with visible borders/lines (10+ lines, mix of horizontal/vertical)
  - Method: Uses `camelot` (good for tables with visible grid lines)
  - Example: Financial statements with clear borders

- **Image-based Pages**: 
  - Detection: Pages with minimal text (<50 chars) but has images
  - Method: Uses `OCR` (converts images to text, then finds tables)
  - Example: Scanned pages or screenshot tables

- **Unknown Pages**: 
  - Detection: Pages that don't clearly fit above categories
  - Method: Tries pdfplumber first, then camelot if needed

#### Phase 2: Smart Extraction

Based on detected page types, the system runs:

1. **pdfplumber** on all pages (finds text-based tables quickly)
2. **camelot** only on:
   - Pages detected as grid-based
   - Pages where pdfplumber found no tables
   - Limited to 300 pages total (processed in chunks of 100)
3. **OCR** only on:
   - Pages detected as image-based
   - Pages with no tables found yet
   - Limited to 200 pages maximum

#### Phase 3: Validation & Linking

1. **Table Validation**: Filters out false positives:
   - Excludes figures/charts (labeled as "Figure", "Chart", "Graph", "Diagram")
   - Excludes text blocks (single column with long text)
   - Excludes MCQ boxes (insufficient structure)
   - Requires "Table" keyword or strong structure evidence

2. **Concept Linking**: Links valid tables to concepts:
   - Semantic matching (sentence-transformers)
   - Keyword matching (from captions/headers)
   - Combined confidence scoring
   - Threshold: 0.4 minimum confidence

3. **Output Generation**: Saves each table as:
   - JSON file (full metadata, concept links, cell types)
   - Markdown file (human-readable table)

### Extraction Methods Comparison

| Method | Best For | Speed | When Used |
|--------|----------|-------|-----------|
| **pdfplumber** | Text-based tables | ⚡ Fast | All pages (primary method) |
| **camelot** | Grid-based tables with borders | 🐌 Slow | Grid pages only, max 300 pages |
| **tabula** | Fallback for text tables | 🐢 Medium | Rarely (if pdfplumber fails) |
| **OCR** | Image-based/scanned tables | 🐌 Very Slow | Image pages only, max 200 pages |

### Processing Flow Diagram

```
PDF Input
    ↓
[Page Type Detection] → Sample first 100 pages
    ↓
    ├─→ Text Pages → pdfplumber (all pages)
    ├─→ Grid Pages → camelot (chunks of 100, max 300)
    ├─→ Image Pages → OCR (max 200 pages)
    └─→ Unknown Pages → pdfplumber → camelot fallback
    ↓
[Merge Results] → Deduplicate tables from different methods
    ↓
[Validate Tables] → Filter figures/charts, text blocks, MCQ boxes
    ↓
[Link Concepts] → Semantic + keyword matching
    ↓
[Save Output] → JSON + Markdown files
```

### Key Optimizations

1. **Smart Detection**: Only runs appropriate method per page (avoids wasting time)
2. **Chunked Processing**: Camelot processes in chunks of 100 pages (prevents hanging)
3. **Intelligent Limits**: 
   - Camelot: Max 300 pages total
   - OCR: Max 200 pages total
   - Prevents hours-long processing on large books
4. **Progress Tracking**: Shows progress every 50 pages
5. **Figure Filtering**: Automatically excludes charts/graphs (not tables)

## 📍 Where to See Concept Linking

Concept linking results appear in **two places**:

### 1. In Individual Table JSON Files

Open any `output/table_X.json` file and look for:

```json
{
  "linked_concept_ids": ["portfolio_theory", "risk_management"],
  "links": [
    {
      "link_type": "TABLE_OF",
      "target_id": "portfolio_theory",
      "confidence": 0.52,
      "evidence": "Semantic similarity: 0.32 + Keywords: risk, return"
    }
  ]
}
```

**Fields:**
- `linked_concept_ids`: Array of concept IDs this table is linked to
- `links`: Detailed link information with confidence scores and evidence

### 2. In Extraction Summary

Open `output/extraction_summary.json` and search for tables with concept links.

**Example tables with concept links:**
- `table_1.json` → linked to `portfolio_theory`
- `table_100.json` → linked to `portfolio_theory`
- `table_101.json` → linked to `portfolio_theory`, `risk_management`, `financial_analysis`
- `table_104.json` → linked to `amortization`, `bond_pricing`

## 🔧 Configuration for Different Books

### Changing the PDF File

**Method 1: Command line (Recommended)**
```bash
python run_extraction.py "path/to/new/book.pdf"
```

**Method 2: Modify `run_extraction.py`**
- Line 20-24: Add your PDF name to `pdf_candidates` list:
```python
pdf_candidates = [
    "book.pdf",
    "document.pdf",
    "your_book_name.pdf",  # Add here
    "../*.pdf",
]
```

### Custom Concept Taxonomy

The system automatically looks for concept taxonomy in this order:
1. `concept_taxonomy.json` (in project root) ← **Recommended**
2. `<pdf_name>.concepts.json` (same folder as PDF)
3. `concept_taxonomy.json` (in PDF's parent folder)

**To use a different taxonomy:**
- Edit `concept_taxonomy.json` in the project root
- Or create a new file following the same format
- See `concept_taxonomy.json` for the structure

**Taxonomy Format:**
```json
{
  "concept_id": {
    "name": "Concept Name",
    "keywords": ["keyword1", "keyword2"],
    "description": "Description of the concept"
  }
}
```

### Page Range (Optional)

To extract only specific pages, modify `run_extraction.py` line 96:

```python
# Extract all pages (current)
extractor = TableExtractorImpl(pdf_path, use_ocr=False, page_range=None)

# Extract pages 1-50 only
extractor = TableExtractorImpl(pdf_path, use_ocr=False, page_range=(1, 50))

# Extract pages 100-200
extractor = TableExtractorImpl(pdf_path, use_ocr=False, page_range=(100, 200))
```


## 📊 Understanding the Output

### JSON Output Structure

Each `table_X.json` contains:

```json
{
  "segment_id": "table_p21_idx0",
  "table_number": "2.1",
  "caption": "Table caption text",
  "col_headers": ["Header1", "Header2"],
  "cells": [["cell1", "cell2"], ["cell3", "cell4"]],
  "linked_concept_ids": ["portfolio_theory"],
  "links": [{
    "link_type": "TABLE_OF",
    "target_id": "portfolio_theory",
    "confidence": 0.52,
    "evidence": "Semantic similarity: 0.32 + Keywords: risk, return"
  }],
  "source_anchor": {
    "extractor": "pdfplumber",  // or "ocr", "camelot", "tabula"
    "page_number": 21,
    "confidence": 0.9
  }
}
```

### Markdown Output

Each `table_X.md` contains a human-readable markdown table.

### Extraction Summary

`extraction_summary.json` contains:
- Total tables found
- List of all tables with basic info
- Page range processed

## 🔍 How Concept Linking Works

1. **Semantic Matching**: Uses sentence-transformers to compute similarity between table descriptions and concept descriptions
2. **Keyword Matching**: Matches keywords from captions/headers to concept keywords
3. **Combined**: Both methods work together for higher confidence
4. **Threshold**: Only links above 0.4 confidence are created

**To see which concepts were linked:**
- Check `linked_concept_ids` in any table JSON
- Check `links` array for detailed evidence

## 🖼️ OCR for Image-Based Tables

OCR automatically runs on:
- Pages detected as **image-based** (minimal text, has images)
- Limited to **200 pages maximum** to avoid extremely long processing times
- Only runs on pages where other methods found no tables

**How to identify OCR tables:**
- Look for `"extractor": "ocr"` in `source_anchor` field
- OCR tables have lower confidence (0.6 vs 0.9 for text-based)

**Note:** For very large books (1000+ pages), OCR is limited to the first 200 image-based pages to balance completeness with processing time.

## ⚙️ Advanced Configuration

### Extract Specific Page Range

Edit `run_extraction.py` line 97:
```python
# Extract all pages (default)
extractor = TableExtractorImpl(pdf_path, use_ocr=False, page_range=None)

# Extract pages 1-100 only
extractor = TableExtractorImpl(pdf_path, use_ocr=False, page_range=(1, 100))

# Extract pages 500-600
extractor = TableExtractorImpl(pdf_path, use_ocr=False, page_range=(500, 600))
```

### Adjust Concept Linking Thresholds

Edit `table_extractor_impl.py`:
- Line ~1519: Semantic threshold (default: 0.3)
- Line ~1577: Final linking threshold (default: 0.4)

### Adjust Smart Detection Limits

Edit `table_extractor_impl.py`:
- Line ~92: Page sampling size (default: 100 pages)
- Line ~512: Max camelot pages (default: 300)
- Line ~112: Max OCR pages (default: 200)

## 🐛 Troubleshooting

### "PDF file not found"
- Make sure PDF path is correct
- Or place PDF in project folder as `book.pdf`

### "Tesseract not found"
- Install Tesseract OCR
- Windows: `winget install UB-Mannheim.TesseractOCR`
- Or download from: https://github.com/UB-Mannheim/tesseract/wiki

### "No concept links found"
- Check that `concept_taxonomy.json` exists
- Verify taxonomy has relevant keywords for your book
- Lower thresholds in `table_extractor_impl.py` if needed

### Type errors during extraction
- Should be fixed, but if you see them:
  - All headers are automatically converted to strings
  - Check `table_serializer.py` line 24

## 📝 Example Workflow

1. **Place your PDF** in the project folder
2. **Run extraction**: `python run_extraction.py "your_book.pdf"`
3. **Check output**: Look in `output/` folder
4. **View concept links**: Open any `table_X.json` and check `linked_concept_ids`
5. **Review summary**: Open `extraction_summary.json` for overview

## 🎯 Quick Reference

| Task | Command/File |
|------|--------------|
| Run extraction | `python run_extraction.py "book.pdf"` |
| View concept links | Open `output/table_X.json` → `linked_concept_ids` |
| Change PDF | Pass as argument or edit `run_extraction.py` line 20-24 |
| Custom taxonomy | Edit `concept_taxonomy.json` |
| Page range | Edit `run_extraction.py` line 96 |
| View OCR tables | Search for `"extractor": "ocr"` in JSON files |

## 📞 Support

For issues or questions:
1. Check `extraction_summary.json` for extraction stats
2. Review error messages in console output
3. Verify all dependencies are installed (`pip install -r requirements.txt`)

