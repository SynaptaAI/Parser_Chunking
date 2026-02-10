"""
Script to run table extraction for all pages with error reporting
"""

import sys
import traceback
import shutil
from pathlib import Path
from table_extractor_impl import TableExtractorImpl
from table_serializer import table_to_markdown, table_to_json, save_table_output
import json

def main():
    # Ensure stdout can handle unicode characters from PDF text
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    # Try to find PDF file
    pdf_candidates = [
        "book.pdf",
        "document.pdf",
        "investments.pdf",
        "../*.pdf",
    ]
    
    pdf_path = None
    if len(sys.argv) > 1:
        pdf_path = sys.argv[1]
    else:
        # Try to find PDF in current directory or parent
        current_dir = Path(".")
        parent_dir = Path("..")
        
        for candidate in pdf_candidates:
            if "*" in candidate:
                # Search pattern
                pattern = candidate.replace("../", "")
                for pdf_file in parent_dir.glob(pattern):
                    pdf_path = str(pdf_file)
                    break
            else:
                if (current_dir / candidate).exists():
                    pdf_path = str(current_dir / candidate)
                    break
                if (parent_dir / candidate).exists():
                    pdf_path = str(parent_dir / candidate)
                    break
    
    if not pdf_path or not Path(pdf_path).exists():
        print("="*70)
        print("ERROR: PDF file not found!")
        print("="*70)
        print("\nUsage:")
        print("  python run_extraction.py <path_to_pdf>")
        print("\nOr place a PDF file named 'book.pdf' or 'document.pdf' in the current directory")
        print("\nSearching for PDF files...")
        
        # Search more broadly
        search_dirs = [Path("."), Path(".."), Path("../..")]
        found_pdfs = []
        for search_dir in search_dirs:
            if search_dir.exists():
                for pdf_file in search_dir.rglob("*.pdf"):
                    found_pdfs.append(str(pdf_file))
                    if len(found_pdfs) >= 5:
                        break
        
        if found_pdfs:
            print(f"\nFound {len(found_pdfs)} PDF file(s):")
            for i, pdf in enumerate(found_pdfs, 1):
                print(f"  {i}. {pdf}")
            print("\nRun: python run_extraction.py <path_from_above>")
        else:
            print("\nNo PDF files found. Please provide the path to your PDF file.")
        
        sys.exit(1)
    
    print("="*70)
    print("Table Extraction - All Pages")
    print("="*70)
    print(f"PDF: {pdf_path}")
    
    try:
        # Get total page count
        import pdfplumber
        with pdfplumber.open(pdf_path) as pdf:
            total_pages = len(pdf.pages)
        print(f"Total Pages: {total_pages}")
        print("Page Range: All pages")
        print("="*70)
        print()
        
        # Initialize extractor without page range (processes all pages)
        print("Initializing extractor...")
        extractor = TableExtractorImpl(pdf_path, use_ocr=False, page_range=None)
        print("[OK] Extractor initialized")
        
        # Extract tables
        print("\nExtracting tables (this may take a while)...")
        tables = extractor.extract_all_tables()
        print(f"[OK] Extraction complete. Found {len(tables)} valid tables")
        
        if len(tables) == 0:
            print("\n" + "="*70)
            print("WARNING: No tables found in the document")
            print("="*70)
            print("\nPossible reasons:")
            print("  1. The PDF doesn't contain tables")
            print("  2. Tables are in a format that's hard to detect")
            print("  3. Tables are image-based (may need OCR)")
            print("\nTry running with OCR enabled")
            return
        
        # Process each table
        output_dir = "output"
        # Clear old output folder before starting (so old files don't remain)
        output_path = Path(output_dir)
        if output_path.exists():
            shutil.rmtree(output_dir)
            print(f"[INFO] Cleared existing output folder")
        output_path.mkdir(exist_ok=True)
        
        print(f"\n{'='*70}")
        print(f"Processing {len(tables)} tables...")
        print(f"{'='*70}")
        
        for idx, table in enumerate(tables):
            print(f"\nTable {idx + 1}/{len(tables)}: {table.segment_id}")
            print(f"  Page: {table.source_anchor.page_number}")
            print(f"  Dimensions: {len(table.cells)} rows × {len(table.col_headers)} columns")
            print(f"  Caption: {table.caption[:80]}..." if len(table.caption) > 80 else f"  Caption: {table.caption}")
            
            # Save outputs
            try:
                md_path, json_path = save_table_output(table, output_dir, f"table_{idx + 1}")
                print(f"  [OK] Saved: {md_path}, {json_path}")
            except Exception as e:
                print(f"  [ERROR] Error saving table {idx + 1}: {e}")
                traceback.print_exc()
        
        # Save summary
        summary = {
            "pdf_path": pdf_path,
            "page_range": "all",
            "total_pages": total_pages,
            "total_tables": len(tables),
            "tables": [
                {
                    "segment_id": t.segment_id,
                    "table_number": t.table_number,
                    "caption": t.caption[:100] if t.caption else "",
                    "page": t.source_anchor.page_number,
                    "dimensions": f"{len(t.cells)}×{len(t.col_headers)}",
                    "schema_hint": t.table_schema_hint,
                }
                for t in tables
            ]
        }
        
        summary_path = Path(output_dir) / "extraction_summary.json"
        with open(summary_path, 'w', encoding='utf-8') as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)
        
        print(f"\n{'='*70}")
        print("Extraction Summary")
        print(f"{'='*70}")
        print(f"Total tables found: {len(tables)}")
        print(f"Summary saved to: {summary_path}")
        print(f"{'='*70}")
        
    except Exception as e:
        print("\n" + "="*70)
        print("ERROR during extraction!")
        print("="*70)
        print(f"\nError type: {type(e).__name__}")
        print(f"Error message: {str(e)}")
        print("\nFull traceback:")
        traceback.print_exc()
        print("\n" + "="*70)
        print("Common issues:")
        print("  1. PDF file is corrupted or password-protected")
        print("  2. Missing dependencies (run: pip install -r requirements.txt)")
        print("  3. PDF is image-based (may need OCR)")
        print("  4. File path is incorrect")
        print("="*70)
        sys.exit(1)

if __name__ == "__main__":
    main()

