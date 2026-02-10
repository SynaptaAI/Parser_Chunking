"""
Implementation of table extraction using pdfplumber, camelot, and fallback methods
"""

import pdfplumber
import pymupdf
import numpy as np
import pandas as pd
from typing import List, Dict, Optional, Tuple, Any, Set
import re
import json
from pathlib import Path

# Optional dependencies
try:
    import camelot
    CAMELOT_AVAILABLE = True
except ImportError:
    CAMELOT_AVAILABLE = False
    camelot = None

try:
    import tabula
    TABULA_AVAILABLE = True
except ImportError:
    TABULA_AVAILABLE = False
    tabula = None

# Optional OCR dependencies
try:
    import pytesseract
    from PIL import Image
    OCR_AVAILABLE = True
    # Auto-detect Tesseract installation on Windows
    import os
    import platform
    if platform.system() == "Windows":
        tesseract_paths = [
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        ]
        for path in tesseract_paths:
            if os.path.exists(path):
                pytesseract.pytesseract.tesseract_cmd = path
                break
except ImportError:
    OCR_AVAILABLE = False
    pytesseract = None
    Image = None
from table_segment import (
    TableSegment, TableExtractor, CellMeta, CellType, FormulaCell,
    DerivedColumn, TableLink, LinkType, SourceAnchor, generate_segment_id,
    normalize_cell_value, detect_cell_type
)


class TableExtractorImpl(TableExtractor):
    """Concrete implementation of table extraction"""
    
    def __init__(self, pdf_path: str, use_ocr: bool = False, page_range: Optional[Tuple[int, int]] = None):
        super().__init__(pdf_path, use_ocr)
        self.pdf_doc = None
        self.pages_text = []
        self.page_range = page_range  # (start_page, end_page) inclusive, 1-indexed
        self.document_text = ""
        self.page_text_by_number: Dict[int, str] = {}
        self.concept_taxonomy: Dict[str, Any] = {}
        self._load_document_text()
        self.concept_taxonomy = self._load_concept_taxonomy()
        
    def extract_all_tables(self) -> List[TableSegment]:
        """Extract all tables from PDF using smart detection to choose best method per page"""
        self.tables = []
        
        # Step 1: Detect page types for all pages (smart detection)
        print("\nAnalyzing page types to choose best extraction method...")
        page_types = {}  # page_num -> 'text', 'grid', 'image', 'unknown'
        text_pages = []
        grid_pages = []
        image_pages = []
        unknown_pages = []
        
        try:
            import pdfplumber
            with pdfplumber.open(self.pdf_path) as pdf:
                total_pages = len(pdf.pages)
                if self.page_range:
                    start_page, end_page = self.page_range
                else:
                    start_page, end_page = 1, total_pages
                
                pages_to_check = min(100, end_page - start_page + 1)  # Sample first 100 pages for detection
                print(f"  Sampling {pages_to_check} pages for type detection...")
                
                for page_num, page in enumerate(pdf.pages, 1):
                    if page_num < start_page or page_num > end_page:
                        continue
                    if page_num > start_page + pages_to_check - 1:
                        # For remaining pages, use pattern from detected pages
                        break
                    
                    page_type = self._detect_page_type(page_num, page)
                    page_types[page_num] = page_type
                    
                    if page_type == 'text':
                        text_pages.append(page_num)
                    elif page_type == 'grid':
                        grid_pages.append(page_num)
                    elif page_type == 'image':
                        image_pages.append(page_num)
                    else:
                        unknown_pages.append(page_num)
                
                # For pages beyond sample, use most common type or 'text' as default
                most_common_type = 'text'
                if len(text_pages) > len(grid_pages) and len(text_pages) > len(image_pages):
                    most_common_type = 'text'
                elif len(grid_pages) > len(image_pages):
                    most_common_type = 'grid'
                else:
                    most_common_type = 'image'
                
                print(f"  Detected: {len(text_pages)} text-based, {len(grid_pages)} grid-based, {len(image_pages)} image-based, {len(unknown_pages)} unknown")
                print(f"  Using '{most_common_type}' as default for remaining pages")
        except Exception as e:
            print(f"  Warning: Page type detection failed: {e}")
            print(f"  Falling back to standard extraction methods")
            page_types = {}
        
        # Step 2: Extract tables using appropriate method based on page type
        pdfplumber_tables = []
        camelot_tables = []
        tabula_tables = []
        ocr_tables = []
        
        # Strategy 1: pdfplumber for text-based pages
        print("\nExtracting from text-based pages (pdfplumber)...")
        pdfplumber_tables = self._extract_with_pdfplumber()
        pages_with_pdfplumber_tables = {region['page'] for region in pdfplumber_tables}
        
        # Strategy 2: camelot for grid-based pages (only pages not already found by pdfplumber)
        if CAMELOT_AVAILABLE and grid_pages:
            grid_pages_to_check = [p for p in grid_pages if p not in pages_with_pdfplumber_tables]
            if grid_pages_to_check:
                print(f"\nExtracting from grid-based pages (camelot) - {len(grid_pages_to_check)} pages...")
                camelot_tables = self._extract_with_camelot(specific_pages=set(grid_pages_to_check))
        
        # Strategy 3: OCR for image-based pages (only pages with no tables found yet)
        pages_with_tables = pages_with_pdfplumber_tables | {region['page'] for region in camelot_tables}
        if OCR_AVAILABLE and image_pages:
            image_pages_to_check = [p for p in image_pages if p not in pages_with_tables]
            if image_pages_to_check:
                # Limit OCR to reasonable number
                MAX_OCR_PAGES = 200
                if len(image_pages_to_check) > MAX_OCR_PAGES:
                    image_pages_to_check = image_pages_to_check[:MAX_OCR_PAGES]
                    print(f"\nExtracting from image-based pages (OCR) - first {len(image_pages_to_check)} pages (limit: {MAX_OCR_PAGES})...")
                else:
                    print(f"\nExtracting from image-based pages (OCR) - {len(image_pages_to_check)} pages...")
                ocr_tables = self._extract_with_ocr(pages_with_tables, specific_pages=set(image_pages_to_check))
        
        # Strategy 4: For unknown pages, try pdfplumber first, then camelot if needed
        if unknown_pages:
            unknown_pages_to_check = [p for p in unknown_pages if p not in pages_with_tables]
            if unknown_pages_to_check:
                print(f"\nExtracting from unknown-type pages - {len(unknown_pages_to_check)} pages (trying pdfplumber then camelot)...")
                # These will be handled by pdfplumber (already ran) and camelot fallback
        
        # Merge and deduplicate
        all_detected = self._merge_table_regions(pdfplumber_tables, camelot_tables, tabula_tables)

        # Merge OCR results
        if ocr_tables:
            all_detected = self._merge_table_regions(all_detected, ocr_tables)
        
        print(f"Total table regions detected: {len(all_detected)}")
        
        # Process each detected table
        skipped_count = 0
        for idx, region in enumerate(all_detected):
            try:
                table_segment = self.reconstruct_structure(region)
                if table_segment:
                    # Check if it's a valid table (including "Table" keyword check)
                    is_valid, reason = self._is_valid_table(table_segment, region)
                    if is_valid:
                        self.tables.append(table_segment)
                    else:
                        skipped_count += 1
                        print(f"Skipping invalid table {idx + 1} on page {region['page']}: "
                              f"{len(table_segment.cells)} rows × {len(table_segment.col_headers)} cols - {reason}")
                else:
                    skipped_count += 1
                    print(f"Could not reconstruct table {idx + 1} on page {region['page']}")
            except Exception as e:
                skipped_count += 1
                print(f"Error processing table {idx + 1} on page {region.get('page', 'unknown')}: {e}")
                import traceback
                traceback.print_exc()
                continue
        
        if skipped_count > 0:
            print(f"\nSkipped {skipped_count} invalid/error tables, kept {len(self.tables)} valid tables")
        
        return self.tables

    def _load_document_text(self) -> None:
        """Load full document text for reference linking"""
        try:
            with pdfplumber.open(self.pdf_path) as pdf:
                texts = []
                for page_num, page in enumerate(pdf.pages, 1):
                    if self.page_range:
                        start_page, end_page = self.page_range
                        if page_num < start_page or page_num > end_page:
                            continue
                    page_text = page.extract_text() or ""
                    self.page_text_by_number[page_num] = page_text
                    texts.append(page_text)
                self.document_text = "\n".join(texts)
        except Exception as e:
            print(f"Warning: could not load document text: {e}")
            self.document_text = ""
            self.page_text_by_number = {}

    def _load_concept_taxonomy(self) -> Dict[str, Any]:
        """Load concept taxonomy from a local JSON file if present"""
        candidates = [
            Path("concept_taxonomy.json"),
            Path(self.pdf_path).with_suffix(".concepts.json"),
            Path(self.pdf_path).parent / "concept_taxonomy.json",
        ]
        for candidate in candidates:
            if candidate.exists():
                try:
                    with open(candidate, "r", encoding="utf-8") as f:
                        return json.load(f)
                except Exception as e:
                    print(f"Warning: could not load concept taxonomy from {candidate}: {e}")
                    return {}
        return {}
    
    def _has_table_keyword(self, page_obj, table_bbox) -> bool:
        """Check if 'Table' keyword exists near the table (above or below)"""
        if not table_bbox or not page_obj:
            return False
        
        x0, y0, x1, y1 = table_bbox
        table_width = x1 - x0
        table_center_x = (x0 + x1) / 2
        
        try:
            words = page_obj.extract_words()
            if not words:
                return False
            
            # Look for "Table" keyword above or below the table
            for word in words:
                word_text = word.get('text', '').strip()
                word_x0 = word.get('x0', 0)
                word_x1 = word.get('x1', 0)
                word_y0 = word.get('top', 0)
                word_y1 = word.get('bottom', 0)
                word_center_x = (word_x0 + word_x1) / 2
                word_center_y = (word_y0 + word_y1) / 2
                
                # Check if word is "Table" (case-insensitive, can be part of "Table X.Y")
                # But NOT "Figure" or "Chart"
                word_lower = word_text.lower()
                if 'table' in word_lower and 'figure' not in word_lower and 'chart' not in word_lower:
                    # Check if it's horizontally aligned with table (within 60% of table width)
                    if abs(word_center_x - table_center_x) < table_width * 0.3:
                        # Check if it's above the table (within 80 points) or below (within 40 points)
                        if (word_y1 < y0 and (y0 - word_y1) < 80) or \
                           (word_y0 > y1 and (word_y0 - y1) < 40):
                            return True
            
            return False
        except Exception:
            return False
    
    def _detect_page_type(self, page_num: int, page_obj) -> str:
        """Detect page type to determine best extraction method
        
        Returns:
            'text' - Page has extractable text (use pdfplumber/tabula)
            'grid' - Page has grid lines/borders (use camelot)
            'image' - Page is mostly image-based (use OCR)
            'unknown' - Couldn't determine, try all methods
        """
        try:
            # Check 1: Extract text to see if page has text content
            text = page_obj.extract_text() or ""
            text_length = len(text.strip())
            
            # Check 2: Look for lines/rectangles (indicating grid structure)
            lines = page_obj.lines
            rects = page_obj.rects
            
            # Count horizontal and vertical lines (grid indicators)
            horizontal_lines = sum(1 for line in lines if abs(line['y1'] - line['y0']) < 2)
            vertical_lines = sum(1 for line in lines if abs(line['x1'] - line['x0']) < 2)
            total_lines = len(lines)
            
            # Check 3: Look for images (using pymupdf for better image detection)
            has_images = False
            try:
                doc = pymupdf.open(self.pdf_path)
                if page_num <= len(doc):
                    page_pymupdf = doc[page_num - 1]
                    image_list = page_pymupdf.get_images()
                    has_images = len(image_list) > 0
                    doc.close()
            except Exception:
                pass
            
            # Decision logic:
            # 1. If page has substantial text (>200 chars), it's text-based
            if text_length > 200:
                return 'text'
            
            # 2. If page has many lines forming a grid (10+ lines, mix of horizontal/vertical), it's grid-based
            if total_lines >= 10 and horizontal_lines >= 3 and vertical_lines >= 3:
                return 'grid'
            
            # 3. If page has images but minimal text (<50 chars), it's image-based
            if has_images and text_length < 50:
                return 'image'
            
            # 4. If page has some text (50-200 chars) and some lines, could be grid or text
            if text_length >= 50 and total_lines >= 5:
                # Prefer grid if more lines, otherwise text
                if total_lines >= 15:
                    return 'grid'
                return 'text'
            
            # 5. If minimal text and no clear structure, likely image-based
            if text_length < 50:
                return 'image'
            
            # Default: unknown, will try all methods
            return 'unknown'
            
        except Exception as e:
            # If detection fails, return unknown
            return 'unknown'
    
    def _has_figure_or_chart_keyword(self, page_obj, table_bbox, caption: str = "") -> bool:
        """Check if 'Figure' or 'Chart' keyword exists near the table - indicates it's NOT a table"""
        if not table_bbox or not page_obj:
            # Check caption if provided
            if caption:
                caption_lower = caption.lower()
                if any(keyword in caption_lower for keyword in ['figure', 'chart', 'graph', 'diagram']):
                    return True
            return False
        
        x0, y0, x1, y1 = table_bbox
        table_width = x1 - x0
        table_center_x = (x0 + x1) / 2
        
        try:
            # First check caption
            if caption:
                caption_lower = caption.lower()
                if any(keyword in caption_lower for keyword in ['figure', 'chart', 'graph', 'diagram']):
                    return True
            
            words = page_obj.extract_words()
            if not words:
                return False
            
            # Look for "Figure", "Chart", "Graph", "Diagram" keywords above or below the table
            for word in words:
                word_text = word.get('text', '').strip()
                word_x0 = word.get('x0', 0)
                word_x1 = word.get('x1', 0)
                word_y0 = word.get('top', 0)
                word_y1 = word.get('bottom', 0)
                word_center_x = (word_x0 + word_x1) / 2
                
                word_lower = word_text.lower()
                # Check for figure/chart keywords
                if any(keyword in word_lower for keyword in ['figure', 'chart', 'graph', 'diagram']):
                    # Check if it's horizontally aligned with table (within 60% of table width)
                    if abs(word_center_x - table_center_x) < table_width * 0.3:
                        # Check if it's above the table (within 80 points) or below (within 40 points)
                        if (word_y1 < y0 and (y0 - word_y1) < 80) or \
                           (word_y0 > y1 and (word_y0 - y1) < 40):
                            return True
            
            return False
        except Exception:
            return False
    
    def _is_valid_table(self, table: TableSegment, region: Dict = None) -> Tuple[bool, str]:
        """Filter out false positives - tables that are too small or just text blocks
        
        Returns:
            Tuple[bool, str]: (is_valid, reason_if_invalid)
        """
        # CRITICAL CHECK: Reject anything labeled as "Figure", "Chart", "Graph", or "Diagram"
        # These are visualizations, not tables
        if region and region.get('extractor') == 'pdfplumber':
            page_obj = region.get('page_obj')
            table_bbox = region.get('bbox')
            if page_obj and table_bbox:
                has_figure_keyword = self._has_figure_or_chart_keyword(page_obj, table_bbox, table.caption)
                if has_figure_keyword:
                    return False, "labeled as Figure/Chart/Graph/Diagram (not a table)"
        
        # Also check caption directly
        if table.caption:
            caption_lower = table.caption.lower()
            if any(keyword in caption_lower for keyword in ['figure', 'chart', 'graph', 'diagram']):
                return False, f"caption contains Figure/Chart keyword: '{table.caption[:50]}...'"
        
        # CRITICAL CHECK: Look for "Table" keyword near the table
        # This helps filter out MCQ boxes, text blocks, and other false positives
        if region and region.get('extractor') == 'pdfplumber':
            page_obj = region.get('page_obj')
            table_bbox = region.get('bbox')
            if page_obj and table_bbox:
                has_table_keyword = self._has_table_keyword(page_obj, table_bbox)
                if not has_table_keyword:
                    # Allow exception: if table has strong structure indicators, it might still be valid
                    # But prioritize tables with "Table" keyword
                    pass  # We'll check this after other validations
        
        # Must have at least 1 row (allow single-row tables if they have structure)
        if len(table.cells) < 1:
            return False, "no rows"
        
        # Must have at least 1 column
        if len(table.col_headers) < 1:
            return False, "no columns"
        
        # Check total cells
        total_cells = sum(len(row) for row in table.cells)
        if total_cells < 2:  # At least 2 cells total
            return False, "too few cells"
        
        # Check if cells contain very long text (likely a text block, not a table)
        long_text_count = 0
        max_cell_length = 0
        for row in table.cells:
            for cell in row:
                if cell:
                    cell_len = len(cell)
                    max_cell_length = max(max_cell_length, cell_len)
                    if cell_len > 500:  # Very long cell text (paragraph)
                        long_text_count += 1
        
        # If more than 50% of non-empty cells are very long text (>500 chars), it's probably not a table
        non_empty_cells = sum(1 for row in table.cells for cell in row if cell and cell.strip())
        if non_empty_cells > 0 and (long_text_count / non_empty_cells) > 0.5:
            return False, "too many long text cells (likely text block)"
        
        # If the table has a single column but most cells are very long, it's probably just text
        if len(table.col_headers) == 1 and max_cell_length > 300:
            # Single column with long text - likely a text block, not a table
            return False, "single column with long text (likely text block)"
        
        # CRITICAL: Check for "Table" keyword - this is the main filter for MCQ boxes
        has_table_keyword = False
        if region and region.get('extractor') == 'pdfplumber':
            page_obj = region.get('page_obj')
            table_bbox = region.get('bbox')
            if page_obj and table_bbox:
                has_table_keyword = self._has_table_keyword(page_obj, table_bbox)
        
        # If no "Table" keyword found, require stronger evidence it's a real table
        if not has_table_keyword:
            # Require: multiple columns AND multiple rows AND structured data
            if len(table.col_headers) < 2 or len(table.cells) < 3:
                return False, "no 'Table' keyword found and insufficient structure (likely MCQ box or text block)"
        
        # Check if table has reasonable structure (at least some numeric or structured data)
        # A valid table should have at least some cells that look like data
        has_structure = False
        numeric_cells = 0
        short_text_cells = 0
        
        # Check all rows (including first row for single-row tables)
        start_row = 0 if len(table.cells) == 1 else 1  # Check first row if only one row
        
        for row in table.cells[start_row:]:
            for cell in row:
                if cell and cell.strip():
                    cell_text = cell.strip()
                    # Check if it looks like structured data
                    if len(cell_text) < 150:  # Not a paragraph
                        short_text_cells += 1
                        has_structure = True
                    # Check for numeric patterns
                    cell_type = detect_cell_type(cell_text)
                    if cell_type in [CellType.NUMBER, CellType.CURRENCY, CellType.PERCENT]:
                        numeric_cells += 1
                        has_structure = True
                    # Check for structured patterns like "X.Y" (section numbers)
                    if re.match(r'^\d+\.\d+', cell_text):
                        has_structure = True
        
        # If we have at least some structured cells, it's likely a table
        if has_structure:
            # If it has "Table" keyword, definitely valid
            if has_table_keyword:
                return True, ""
            # If no keyword but strong structure, still allow (but with lower confidence)
            if len(table.col_headers) >= 2 and len(table.cells) >= 3:
                return True, ""
        
        # Multi-column tables with "Table" keyword are definitely valid
        if has_table_keyword and len(table.col_headers) >= 2 and total_cells >= 4:
            return True, ""
        
        # Multi-column tables are more likely to be real tables (but require keyword if small)
        if len(table.col_headers) >= 2 and total_cells >= 6:
            if has_table_keyword:
                return True, ""
            # Large multi-column tables might be valid even without keyword
            if len(table.cells) >= 5:
                return True, ""
        
        # Single-row tables with multiple columns and short text are likely valid
        if len(table.cells) == 1 and len(table.col_headers) >= 2:
            # Check if cells are short (not paragraphs)
            all_short = all(
                not cell or len(cell.strip()) < 100 
                for cell in table.cells[0]
            )
            if all_short:
                # Only allow if has "Table" keyword (single-row tables are often false positives)
                if has_table_keyword:
                    return True, ""
                return False, "single-row table without 'Table' keyword (likely not a real table)"
        
        return False, "insufficient structure and no 'Table' keyword"
    
    def _extract_with_pdfplumber(self) -> List[Dict]:
        """Extract tables using pdfplumber"""
        tables = []
        try:
            with pdfplumber.open(self.pdf_path) as pdf:
                total_pages = len(pdf.pages)
                print(f"PDF has {total_pages} total pages")
                
                pages_checked = 0
                total_pages_in_range = len(pdf.pages)
                if self.page_range:
                    start_page, end_page = self.page_range
                    total_pages_in_range = end_page - start_page + 1
                
                for page_num, page in enumerate(pdf.pages, 1):
                    # Apply page range filter
                    if self.page_range:
                        start_page, end_page = self.page_range
                        if page_num < start_page or page_num > end_page:
                            continue
                    
                    pages_checked += 1
                    # Show progress every 50 pages
                    if pages_checked % 50 == 0 or pages_checked == total_pages_in_range:
                        print(f"  Progress: {pages_checked}/{total_pages_in_range} pages checked...")
                    
                    # Detect tables by finding aligned columns
                    tables_on_page = page.find_tables()
                    
                    if len(tables_on_page) > 0:
                        print(f"  Page {page_num}: Found {len(tables_on_page)} table(s)")
                    
                    for table in tables_on_page:
                        bbox = table.bbox
                        tables.append({
                            'page': page_num,
                            'bbox': bbox,
                            'extractor': 'pdfplumber',
                            'table_obj': table,
                            'page_obj': page
                        })
                
                print(f"Checked {pages_checked} pages in range, found {len(tables)} table regions via pdfplumber")
        except Exception as e:
            print(f"pdfplumber extraction error: {e}")
            import traceback
            traceback.print_exc()
        
        return tables
    
    def _extract_with_camelot(self, pages_to_skip: Set[int] = None, specific_pages: Set[int] = None) -> List[Dict]:
        """Extract tables using camelot (grid-based)
        
        Args:
            pages_to_skip: Set of page numbers where pdfplumber already found tables
                          (camelot will only run on other pages to avoid duplicate work)
        """
        tables = []
        if not CAMELOT_AVAILABLE:
            return tables
        
        if pages_to_skip is None:
            pages_to_skip = set()
        
        if specific_pages is not None:
            # If specific pages provided, ignore pages_to_skip (specific_pages takes priority)
            pages_to_skip = set()
        
        try:
            # Determine which pages to process
            try:
                import pdfplumber
                with pdfplumber.open(self.pdf_path) as pdf:
                    total_pages = len(pdf.pages)
                    if specific_pages is not None:
                        # Use specific pages if provided (from smart detection)
                        pages_to_check = [p for p in specific_pages if p not in (pages_to_skip or set())]
                    elif self.page_range:
                        start_page, end_page = self.page_range
                        pages_to_check = [p for p in range(start_page, end_page + 1) if p not in (pages_to_skip or set())]
                    else:
                        pages_to_check = [p for p in range(1, total_pages + 1) if p not in (pages_to_skip or set())]
                    
                    pages_needing_camelot = len(pages_to_check)
                    
                    # Process camelot in chunks to avoid hanging on large PDFs
                    MAX_CAMELOT_PAGES_PER_CHUNK = 100
                    MAX_TOTAL_CAMELOT_PAGES = 300  # Total limit across all chunks
                    
                    if pages_needing_camelot == 0:
                        print("Skipping camelot: all pages already have tables from pdfplumber")
                        return tables
                    
                    if pages_needing_camelot > MAX_TOTAL_CAMELOT_PAGES:
                        print(f"Skipping camelot: {pages_needing_camelot} pages need checking (limit: {MAX_TOTAL_CAMELOT_PAGES})")
                        print(f"  Camelot is extremely slow. Using pdfplumber results + OCR for remaining pages.")
                        return tables
                    
                    # Process in chunks
                    print(f"Running camelot on {pages_needing_camelot} pages (in chunks of {MAX_CAMELOT_PAGES_PER_CHUNK})...")
                    
                    for chunk_start in range(0, len(pages_to_check), MAX_CAMELOT_PAGES_PER_CHUNK):
                        chunk_pages = pages_to_check[chunk_start:chunk_start + MAX_CAMELOT_PAGES_PER_CHUNK]
                        chunk_pages_str = ','.join(str(p) for p in chunk_pages)
                        
                        print(f"  Camelot chunk: pages {chunk_pages[0]}-{chunk_pages[-1]} ({len(chunk_pages)} pages)...")
                        
                        try:
                            camelot_tables = camelot.read_pdf(self.pdf_path, pages=chunk_pages_str, flavor='lattice')
                            
                            for table in camelot_tables:
                                # Apply page range filter
                                if self.page_range:
                                    start_page, end_page = self.page_range
                                    if table.page < start_page or table.page > end_page:
                                        continue
                                
                                tables.append({
                                    'page': table.page,
                                    'bbox': None,
                                    'extractor': 'camelot',
                                    'table_obj': table,
                                    'df': table.df
                                })
                            
                            print(f"    Found {len(camelot_tables)} table(s) in this chunk")
                        except Exception as chunk_error:
                            print(f"    Camelot chunk error: {chunk_error}")
                            continue  # Continue with next chunk
                    
                    print(f"Camelot total: found {len(tables)} table(s)")
                    
            except Exception as e:
                print(f"camelot extraction error: {e}")
                import traceback
                traceback.print_exc()
        
        except Exception as e:
            print(f"camelot extraction error: {e}")
            import traceback
            traceback.print_exc()
        
        return tables
    
    def _extract_with_tabula(self) -> List[Dict]:
        """Extract tables using tabula (fallback)"""
        tables = []
        if not TABULA_AVAILABLE:
            return tables
        
        try:
            # Build page list for tabula
            if self.page_range:
                start_page, end_page = self.page_range
                pages_list = list(range(start_page, end_page + 1))
            else:
                pages_list = 'all'
            
            dfs = tabula.read_pdf(self.pdf_path, pages=pages_list, multiple_tables=True)
            
            for page_num, df in enumerate(dfs, 1):
                # Apply page range filter
                if self.page_range:
                    start_page, end_page = self.page_range
                    if page_num < start_page or page_num > end_page:
                        continue
                
                if df is not None and not df.empty:
                    tables.append({
                        'page': page_num,
                        'bbox': None,
                        'extractor': 'tabula',
                        'df': df
                    })
        except Exception as e:
            print(f"tabula extraction error: {e}")
        
        return tables

    def _extract_with_ocr(self, pages_with_tables: Set[int], specific_pages: Set[int] = None) -> List[Dict]:
        """OCR fallback for image-based tables - runs on pages where text extraction found no tables"""
        if not OCR_AVAILABLE:
            print("Warning: OCR dependencies (pytesseract/Pillow) not available. Install with: pip install pytesseract Pillow")
            print("         Also ensure Tesseract OCR is installed: https://github.com/tesseract-ocr/tesseract")
            return []
        
        tables = []
        total_pages_to_check = 0
        try:
            doc = pymupdf.open(self.pdf_path)
            total_pages = len(doc)
            
            # Determine which pages need OCR
            pages_to_ocr = []
            for page_index in range(len(doc)):
                page_num = page_index + 1
                if self.page_range:
                    start_page, end_page = self.page_range
                    if page_num < start_page or page_num > end_page:
                        continue
                if page_num not in pages_with_tables:
                    # If specific_pages is provided, only process those pages
                    if specific_pages is not None:
                        if page_num not in specific_pages:
                            continue
                    pages_to_ocr.append((page_index, page_num))
                    total_pages_to_check += 1
            
            if total_pages_to_check == 0:
                print("  No pages need OCR (all pages already have detected tables)")
                doc.close()
                return []
            
            print(f"  Checking {total_pages_to_check} page(s) with OCR...")
            
            for page_index, page_num in pages_to_ocr:
                try:
                    page = doc[page_index]
                    
                    # Check if page has extractable text first (skip if it does)
                    text = page.get_text()
                    if text and len(text.strip()) > 50:
                        # Page has text, likely not image-based, skip OCR
                        continue
                    
                    # Render page as image for OCR
                    pix = page.get_pixmap(dpi=200)
                    mode = "RGB" if pix.alpha == 0 else "RGBA"
                    image = Image.frombytes(mode, [pix.width, pix.height], pix.samples)
                    if mode == "RGBA":
                        image = image.convert("RGB")

                    # Run OCR
                    ocr_data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
                    table_data = self._ocr_words_to_table(ocr_data, page.rect.width, page.rect.height, pix.width, pix.height)
                    
                    if table_data and len(table_data.get("cells", [])) >= 2:
                        print(f"    Page {page_num}: OCR detected table structure ({len(table_data['cells'])} rows)")
                        tables.append({
                            "page": page_num,
                            "bbox": table_data["bbox"],
                            "extractor": "ocr",
                            "cells": table_data["cells"],
                            "cell_bboxes": table_data["cell_bboxes"],
                        })
                except Exception as e:
                    # Continue with other pages if one fails
                    print(f"    Page {page_num}: OCR error - {e}")
                    continue
                    
            doc.close()
        except Exception as e:
            print(f"OCR extraction error: {e}")
            import traceback
            traceback.print_exc()
        
        return tables

    def _ocr_words_to_table(
        self,
        ocr_data: Dict[str, List[Any]],
        page_width: float,
        page_height: float,
        image_width: int,
        image_height: int
    ) -> Optional[Dict[str, Any]]:
        """Convert OCR words to a table-like structure when alignment is detected"""
        words = []
        scale_x = page_width / image_width
        scale_y = page_height / image_height

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

        # Require at least 5 words for OCR table detection (more lenient)
        if len(words) < 5:
            return None

        # Cluster words into rows by y-center proximity
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

        # Require at least 2 rows (more lenient for OCR)
        if len(rows) < 2:
            return None

        # Cluster columns by x0 alignment
        x_positions = [w["x0"] for w in words]
        median_w = np.median([w["w"] for w in words]) if words else 10
        col_threshold = max(8.0, median_w * 1.2)  # More lenient column detection
        col_centers = []
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
        # Allow single column if it has multiple rows (could be a list/table)
        if len(col_centers) < 1:
            return None

        # Build cell grid
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

        # Check if we have enough filled cells (more lenient for OCR)
        avg_filled = np.mean([sum(1 for c in row if c.strip()) for row in cells])
        if avg_filled < 1.5:  # More lenient threshold
            return None
        
        # Additional check: ensure we have at least some structure
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
    
    def _merge_table_regions(self, *table_lists) -> List[Dict]:
        """Merge and deduplicate table regions from multiple extractors"""
        merged = []
        seen_regions = set()
        
        for table_list in table_lists:
            for region in table_list:
                # Simple deduplication by page and approximate position
                key = (region['page'], region.get('bbox'))
                if key not in seen_regions:
                    seen_regions.add(key)
                    merged.append(region)
        
        return merged
    
    def reconstruct_structure(self, table_region: Dict) -> Optional[TableSegment]:
        """Reconstruct table structure from detected region"""
        extractor = table_region['extractor']
        page_num = table_region['page']
        
        if extractor == 'pdfplumber':
            return self._reconstruct_from_pdfplumber(table_region)
        elif extractor == 'camelot':
            return self._reconstruct_from_camelot(table_region)
        elif extractor == 'tabula':
            return self._reconstruct_from_tabula(table_region)
        elif extractor == 'ocr':
            return self._reconstruct_from_ocr(table_region)
        
        return None
    
    def _reconstruct_from_pdfplumber(self, region: Dict) -> Optional[TableSegment]:
        """Reconstruct from pdfplumber table object"""
        table_obj = region['table_obj']
        page_obj = region['page_obj']
        page_num = region['page']
        
        # Extract cells - handle different pdfplumber formats
        cells = []
        cell_bboxes = []
        
        try:
            # Use extract() method which returns list of lists
            extracted = table_obj.extract()
            if extracted:
                for row in extracted:
                    row_cells = []
                    row_bboxes = []
                    for cell in row:
                        # Handle None, string, or other types
                        if cell is None:
                            text = ""
                            bbox = None
                        else:
                            text = str(cell).strip()
                            bbox = None  # extract() doesn't provide bbox
                        row_cells.append(text)
                        row_bboxes.append(bbox)
                    cells.append(row_cells)
                    cell_bboxes.append(row_bboxes)
            else:
                # Fallback: try rows.cells
                for row in table_obj.rows:
                    row_cells = []
                    row_bboxes = []
                    for cell in row.cells:
                        # Handle both cell objects and tuples
                        if isinstance(cell, tuple):
                            text = str(cell[0]).strip() if cell and len(cell) > 0 and cell[0] else ""
                            bbox = cell[1] if len(cell) > 1 else None
                        elif hasattr(cell, 'text'):
                            text = cell.text.strip() if cell.text else ""
                            bbox = cell.bbox if hasattr(cell, 'bbox') else None
                        else:
                            text = str(cell).strip() if cell else ""
                            bbox = None
                        row_cells.append(text)
                        row_bboxes.append(bbox)
                    cells.append(row_cells)
                    cell_bboxes.append(row_bboxes)
        except Exception as e:
            print(f"Could not extract cells from pdfplumber table: {e}")
            return None
        
        if not cells or len(cells) < 1:
            return None
        
        # Detect caption (look above table)
        caption = self._detect_caption(page_obj, region['bbox'])
        table_number = self._extract_table_number(caption)
        
        # Extract headers (first row typically)
        col_headers = [str(h) for h in (cells[0] if cells else [])]
        row_headers = []
        
        # Check if first column contains row headers
        if len(cells) > 1:
            row_headers = [[row[0]] for row in cells[1:]] if cells[0] else []
        
        # Create cell metadata
        cell_meta = []
        for r_idx, row in enumerate(cells):
            for c_idx, cell_text in enumerate(row):
                bbox = cell_bboxes[r_idx][c_idx] if r_idx < len(cell_bboxes) and c_idx < len(cell_bboxes[r_idx]) else None
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
        
        # Create source anchor
        source_anchor = SourceAnchor(
            page_number=page_num,
            bbox=region['bbox'],
            extractor='pdfplumber',
            confidence=0.9
        )
        
        # Generate segment ID
        segment_id = generate_segment_id(table_number, page_num, len(self.tables))
        
        # Create table segment
        table_segment = TableSegment(
            segment_id=segment_id,
            table_number=table_number,
            caption=caption,
            col_headers=col_headers,
            row_headers=row_headers,
            cells=cells,
            cell_meta=cell_meta,
            source_anchor=source_anchor,
            page_span=(page_num, page_num)
        )
        
        # Post-process: detect formulas, generate description, etc.
        self._post_process_table(table_segment, page_obj)
        
        return table_segment
    
    def _reconstruct_from_camelot(self, region: Dict) -> Optional[TableSegment]:
        """Reconstruct from camelot table"""
        df = region['df']
        page_num = region['page']
        
        if df is None or df.empty:
            return None
        
        # Convert DataFrame to cell matrix
        cells = df.values.tolist()
        cells = [[str(cell) if pd.notna(cell) else "" for cell in row] for row in cells]
        
        # Headers - convert to strings (pandas columns can be integers)
        col_headers = [str(h) for h in (df.columns.tolist() if hasattr(df, 'columns') else [])]
        row_headers = []
        
        # Create cell metadata (simplified for camelot)
        cell_meta = []
        for r_idx, row in enumerate(cells):
            for c_idx, cell_text in enumerate(row):
                cell_type = detect_cell_type(cell_text)
                normalized = normalize_cell_value(cell_text, cell_type)
                
                meta = CellMeta(
                    row_index=r_idx,
                    col_index=c_idx,
                    bbox=(0, 0, 0, 0),  # Camelot doesn't provide per-cell bbox easily
                    cell_type=cell_type,
                    raw_value=cell_text,
                    normalized_value=normalized
                )
                cell_meta.append(meta)
        
        source_anchor = SourceAnchor(
            page_number=page_num,
            bbox=(0, 0, 0, 0),
            extractor='camelot',
            confidence=0.85
        )
        
        segment_id = generate_segment_id(None, page_num, len(self.tables))
        
        table_segment = TableSegment(
            segment_id=segment_id,
            caption="",  # Would need to detect separately
            col_headers=col_headers,
            row_headers=row_headers,
            cells=cells,
            cell_meta=cell_meta,
            source_anchor=source_anchor,
            page_span=(page_num, page_num)
        )
        self._post_process_table(table_segment, None)
        return table_segment
    
    def _reconstruct_from_tabula(self, region: Dict) -> Optional[TableSegment]:
        """Reconstruct from tabula DataFrame"""
        df = region['df']
        page_num = region['page']
        
        if df is None or df.empty:
            return None
        
        # Similar to camelot
        cells = df.values.tolist()
        cells = [[str(cell) if pd.notna(cell) else "" for cell in row] for row in cells]
        
        # Headers - convert to strings (pandas columns can be integers)
        col_headers = [str(h) for h in df.columns.tolist()]
        
        cell_meta = []
        for r_idx, row in enumerate(cells):
            for c_idx, cell_text in enumerate(row):
                cell_type = detect_cell_type(cell_text)
                normalized = normalize_cell_value(cell_text, cell_type)
                
                meta = CellMeta(
                    row_index=r_idx,
                    col_index=c_idx,
                    bbox=(0, 0, 0, 0),
                    cell_type=cell_type,
                    raw_value=cell_text,
                    normalized_value=normalized
                )
                cell_meta.append(meta)
        
        source_anchor = SourceAnchor(
            page_number=page_num,
            bbox=(0, 0, 0, 0),
            extractor='tabula',
            confidence=0.8
        )
        
        segment_id = generate_segment_id(None, page_num, len(self.tables))
        
        table_segment = TableSegment(
            segment_id=segment_id,
            col_headers=col_headers,
            cells=cells,
            cell_meta=cell_meta,
            source_anchor=source_anchor,
            page_span=(page_num, page_num)
        )
        self._post_process_table(table_segment, None)
        return table_segment

    def _reconstruct_from_ocr(self, region: Dict) -> Optional[TableSegment]:
        """Reconstruct table from OCR-derived cells"""
        page_num = region["page"]
        cells = region.get("cells") or []
        cell_bboxes = region.get("cell_bboxes") or []
        if not cells:
            return None

        # Headers - convert to strings
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
            page_number=page_num,
            bbox=region.get("bbox") or (0, 0, 0, 0),
            extractor="ocr",
            confidence=0.6
        )

        segment_id = generate_segment_id(None, page_num, len(self.tables))
        table_segment = TableSegment(
            segment_id=segment_id,
            caption="",
            col_headers=col_headers,
            row_headers=row_headers,
            cells=cells,
            cell_meta=cell_meta,
            source_anchor=source_anchor,
            page_span=(page_num, page_num)
        )
        self._post_process_table(table_segment, None)
        return table_segment
    
    def _detect_caption(self, page_obj, table_bbox) -> str:
        """Detect table caption above or below the table"""
        if not table_bbox:
            return ""
        
        x0, y0, x1, y1 = table_bbox
        table_width = x1 - x0
        table_center_x = (x0 + x1) / 2
        
        # Common patterns: "Table X.Y", "Exhibit X", etc.
        # Note: We exclude "Figure" patterns since we only want table captions
        caption_patterns = [
            r'Table\s+\d+\.?\d*[:\s].*',
            r'Exhibit\s+\d+[:\s].*'
        ]
        
        try:
            words = page_obj.extract_words()
            if not words:
                return ""
            
            # Look for text above the table (within 50 points above)
            above_words = []
            below_words = []
            
            for word in words:
                word_x0 = word.get('x0', 0)
                word_x1 = word.get('x1', 0)
                word_y0 = word.get('top', 0)
                word_y1 = word.get('bottom', 0)
                word_text = word.get('text', '')
                word_center_x = (word_x0 + word_x1) / 2
                word_center_y = (word_y0 + word_y1) / 2
                
                # Check if word is horizontally aligned with table (within 80% of table width)
                if abs(word_center_x - table_center_x) < table_width * 0.4:
                    # Text above table (within 50 points)
                    if word_y1 < y0 and (y0 - word_y1) < 50:
                        above_words.append((word_y1, word_text))
                    # Text below table (within 30 points)
                    elif word_y0 > y1 and (word_y0 - y1) < 30:
                        below_words.append((word_y0, word_text))
            
            # Sort by position (top to bottom for above, top to bottom for below)
            above_words.sort(key=lambda x: x[0], reverse=True)  # Bottom to top
            below_words.sort(key=lambda x: x[0])  # Top to bottom
            
            # Check above words first (captions are usually above)
            if above_words:
                # Get last 15 words above (captions can be multi-line)
                caption_words = [w[1] for w in above_words[-15:]]
                caption_text = ' '.join(caption_words)
                
                # Check if it matches caption patterns
                for pattern in caption_patterns:
                    match = re.search(pattern, caption_text, re.IGNORECASE)
                    if match:
                        matched_text = caption_text[match.start():].strip()
                        # Only return if it's a "Table" caption, not "Figure" or "Chart"
                        matched_lower = matched_text.lower()
                        if 'figure' in matched_lower or 'chart' in matched_lower or 'graph' in matched_lower:
                            # Skip this - it's a figure/chart, not a table
                            continue
                        # Return the matched portion and following text
                        return matched_text
                
                # If no pattern match, return the text if it's short (likely a caption)
                # But only if it doesn't contain figure/chart keywords
                if len(caption_text) < 200:
                    caption_lower = caption_text.lower()
                    if not any(keyword in caption_lower for keyword in ['figure', 'chart', 'graph', 'diagram']):
                        return caption_text.strip()
            
            # Check below words (some tables have captions below)
            if below_words:
                caption_words = [w[1] for w in below_words[:10]]
                caption_text = ' '.join(caption_words)
                
                for pattern in caption_patterns:
                    match = re.search(pattern, caption_text, re.IGNORECASE)
                    if match:
                        matched_text = caption_text[match.start():].strip()
                        # Only return if it's a "Table" caption, not "Figure" or "Chart"
                        matched_lower = matched_text.lower()
                        if 'figure' in matched_lower or 'chart' in matched_lower or 'graph' in matched_lower:
                            # Skip this - it's a figure/chart, not a table
                            continue
                        return matched_text
        
        except Exception as e:
            # Silently fail - caption detection is optional
            pass
        
        return ""
    
    def _extract_table_number(self, caption: str) -> Optional[str]:
        """Extract table number from caption"""
        patterns = [
            r'Table\s+(\d+\.?\d*)',
            r'Exhibit\s+(\d+)',
            r'Figure\s+(\d+\.?\d*)'
        ]
        
        for pattern in patterns:
            match = re.search(pattern, caption, re.IGNORECASE)
            if match:
                return match.group(1)
        
        return None
    
    def _post_process_table(self, table: TableSegment, page_obj):
        """Post-process table: formulas, descriptions, linking"""
        # Detect formulas
        formula_cells = self.detect_formulas(table)
        table.formula_cells = formula_cells
        
        # Infer computed columns
        derived_columns = self.infer_computed_columns(table)
        table.derived_columns = derived_columns
        
        # Generate description
        description = self.generate_description(table)
        table.description = description
        table.table_summary = description

        # Concept linking (if taxonomy available)
        if self.concept_taxonomy:
            self.link_to_concepts(table, self.concept_taxonomy)

        # Cross-reference linking (if document text available)
        if self.document_text:
            self.find_cross_references(table, self.document_text)
    
    def detect_formulas(self, table: TableSegment) -> List[FormulaCell]:
        """Detect formulas within table"""
        formula_cells = []
        
        for meta in table.cell_meta:
            if meta.cell_type == CellType.FORMULA:
                formula = FormulaCell(
                    cell_address=f"{chr(65 + meta.col_index)}{meta.row_index + 1}",
                    formula_text=meta.raw_value,
                    bbox=meta.bbox,
                    source_anchor=table.source_anchor,
                    confidence=0.9
                )
                formula_cells.append(formula)
        
        return formula_cells
    
    def infer_computed_columns(self, table: TableSegment) -> List[DerivedColumn]:
        """Infer computed/derived columns"""
        derived = []
        
        if len(table.cells) < 3:
            return derived
        
        # Heuristic: if a column has numeric values that correlate with
        # operations on other columns, it might be computed
        
        num_cols = len(table.cells[0])
        num_rows = len(table.cells)
        
        for col_idx in range(num_cols):
            # Skip header row
            col_values = []
            for row_idx in range(1, min(num_rows, 10)):  # Check first 10 rows
                if row_idx < len(table.cells) and col_idx < len(table.cells[row_idx]):
                    val = table.cells[row_idx][col_idx]
                    try:
                        num_val = float(re.sub(r'[^\d.]', '', val))
                        col_values.append(num_val)
                    except:
                        pass
            
            if len(col_values) < 3:
                continue
            
            # Check if this column might be a product/sum of other columns
            # This is simplified - real implementation would be more sophisticated
            for other_col_idx in range(num_cols):
                if other_col_idx == col_idx:
                    continue
                
                other_values = []
                for row_idx in range(1, min(num_rows, 10)):
                    if row_idx < len(table.cells) and other_col_idx < len(table.cells[row_idx]):
                        val = table.cells[row_idx][other_col_idx]
                        try:
                            num_val = float(re.sub(r'[^\d.]', '', val))
                            other_values.append(num_val)
                        except:
                            pass
                
                if len(other_values) == len(col_values):
                    # Check for multiplication relationship
                    ratios = [col_values[i] / other_values[i] if other_values[i] != 0 else 0 
                             for i in range(len(col_values))]
                    if len(set(ratios)) == 1 and ratios[0] != 0:
                        # Potential multiplication
                        header = table.col_headers[col_idx] if col_idx < len(table.col_headers) else f"Col{col_idx}"
                        derived.append(DerivedColumn(
                            column_index=col_idx,
                            column_header=header,
                            rule_description=f"{header} = {table.col_headers[other_col_idx]} × {ratios[0]:.2f}",
                            input_columns=[other_col_idx],
                            confidence=0.7,
                            is_inferred=True
                        ))
        
        return derived
    
    def generate_description(self, table: TableSegment) -> str:
        """Generate table description for embeddings"""
        parts = []
        
        if table.caption:
            parts.append(table.caption)
        
        # Analyze headers to infer schema
        headers_text = " ".join(str(h) for h in table.col_headers if h is not None)
        
        # Common finance table patterns
        if any(term in headers_text.lower() for term in ['payment', 'principal', 'interest', 'balance']):
            table.table_schema_hint = "amortization schedule"
            parts.append("This table shows an amortization schedule with payment breakdowns.")
        elif any(term in headers_text.lower() for term in ['cashflow', 'coupon', 'yield', 'maturity']):
            table.table_schema_hint = "bond cashflows"
            parts.append("This table presents bond cashflow information.")
        elif any(term in headers_text.lower() for term in ['npv', 'irr', 'discount', 'rate']):
            table.table_schema_hint = "NPV sensitivity grid"
            parts.append("This table shows NPV sensitivity analysis across different scenarios.")
        else:
            parts.append(f"This table contains {len(table.cells)} rows and {len(table.col_headers)} columns.")
        
        if table.derived_columns:
            parts.append(f"It includes {len(table.derived_columns)} computed columns.")
        
        return " ".join(parts)
    
    def link_to_concepts(self, table: TableSegment, concept_taxonomy: Dict) -> List[str]:
        """Link table to concept IDs using keyword and semantic matching"""
        linked = []
        linked_concepts = []
        
        # Build search text from table content
        search_text = f"{table.caption} {' '.join(str(h) for h in table.col_headers if h is not None)} {table.description}"
        search_text_lower = search_text.lower()
        
        # Try semantic matching first (if available)
        semantic_matches = []
        try:
            from sentence_transformers import SentenceTransformer
            import numpy as np
            
            # Lazy load model (cache it)
            if not hasattr(self, '_semantic_model'):
                self._semantic_model = SentenceTransformer('all-MiniLM-L6-v2')
            
            # Embed table description
            table_embedding = self._semantic_model.encode([search_text], convert_to_numpy=True)[0]
            
            # Embed all concepts
            concept_texts = []
            concept_ids = []
            for concept_id, concept_info in concept_taxonomy.items():
                concept_text = f"{concept_info.get('name', '')} {concept_info.get('description', '')} {' '.join(concept_info.get('keywords', []))}"
                concept_texts.append(concept_text)
                concept_ids.append(concept_id)
            
            if concept_texts:
                concept_embeddings = self._semantic_model.encode(concept_texts, convert_to_numpy=True)
                
                # Compute cosine similarities
                similarities = np.dot(concept_embeddings, table_embedding) / (
                    np.linalg.norm(concept_embeddings, axis=1) * np.linalg.norm(table_embedding)
                )
                
                # Get top matches above threshold
                threshold = 0.3  # Lower threshold for semantic matching
                for i, similarity in enumerate(similarities):
                    if similarity >= threshold:
                        semantic_matches.append((concept_ids[i], similarity))
            
            # Sort by similarity
            semantic_matches.sort(key=lambda x: x[1], reverse=True)
            
        except ImportError:
            # sentence-transformers not available, skip semantic matching
            pass
        except Exception as e:
            # If semantic matching fails, fall back to keyword matching
            pass
        
        # Keyword matching
        keyword_matches = []
        for concept_id, concept_info in concept_taxonomy.items():
            concept_keywords = concept_info.get('keywords', [])
            concept_name = concept_info.get('name', '')
            
            # Check keyword matches
            matches = []
            for kw in concept_keywords + [concept_name]:
                if kw.lower() in search_text_lower:
                    matches.append(kw)
            
            if matches:
                # Score based on number of keyword matches
                score = min(1.0, len(matches) / max(len(concept_keywords), 1))
                keyword_matches.append((concept_id, score, matches))
        
        # Combine semantic and keyword matches
        all_matches = {}
        
        # Add semantic matches (higher confidence)
        for concept_id, similarity in semantic_matches[:5]:  # Top 5 semantic matches
            all_matches[concept_id] = {
                'confidence': float(similarity),
                'method': 'semantic',
                'evidence': f"Semantic similarity: {similarity:.2f}"
            }
        
        # Add keyword matches (merge with semantic if exists)
        for concept_id, score, matches in keyword_matches:
            if concept_id in all_matches:
                # Boost confidence if both methods agree
                all_matches[concept_id]['confidence'] = min(1.0, all_matches[concept_id]['confidence'] + 0.2)
                all_matches[concept_id]['method'] = 'semantic+keyword'
                all_matches[concept_id]['evidence'] += f" + Keywords: {', '.join(matches[:3])}"
            else:
                all_matches[concept_id] = {
                    'confidence': score * 0.8,  # Slightly lower than semantic
                    'method': 'keyword',
                    'evidence': f"Keyword match: {', '.join(matches[:3])}"
                }
        
        # Create links for matches above threshold
        final_threshold = 0.4
        for concept_id, match_info in all_matches.items():
            if match_info['confidence'] >= final_threshold:
                linked.append(concept_id)
                linked_concepts.append((concept_id, match_info))
                
                concept_name = concept_taxonomy[concept_id].get('name', concept_id)
                table.links.append(TableLink(
                    link_type=LinkType.TABLE_OF,
                    target_id=concept_id,
                    source_anchor=table.source_anchor,
                    evidence=match_info['evidence'],
                    confidence=match_info['confidence']
                ))
        
        # Sort by confidence
        linked_concepts.sort(key=lambda x: x[1]['confidence'], reverse=True)
        table.linked_concept_ids = [c[0] for c in linked_concepts]
        
        if linked:
            methods_used = set(m['method'] for _, m in linked_concepts)
            print(f"  Linked to {len(linked)} concept(s) using: {', '.join(methods_used)}")
        
        return table.linked_concept_ids
    
    def find_cross_references(self, table: TableSegment, document_text: str) -> List[TableLink]:
        """Find paragraphs that reference this table"""
        links = []
        
        if not table.table_number:
            return links
        
        # Look for references like "see Table 2.1", "Table 2.1 shows", etc.
        patterns = [
            rf'see\s+Table\s+{re.escape(table.table_number)}',
            rf'Table\s+{re.escape(table.table_number)}\s+shows',
            rf'refer\s+to\s+Table\s+{re.escape(table.table_number)}',
        ]
        
        for pattern in patterns:
            matches = re.finditer(pattern, document_text, re.IGNORECASE)
            for match in matches:
                link = TableLink(
                    link_type=LinkType.REFERENCES,
                    target_id=table.segment_id,
                    source_anchor=SourceAnchor(
                        page_number=table.source_anchor.page_number,
                        bbox=(0, 0, 0, 0),
                        extractor='text_search',
                        confidence=0.9
                    ),
                    evidence=match.group(0),
                    confidence=0.9
                )
                links.append(link)
        
        table.links.extend(links)
        return links

