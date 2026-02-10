"""
Table Segmentation Module

Extracts tables from PDFs with full structure reconstruction, formula detection,
and linking to concept taxonomy.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple, Any, Union
from enum import Enum
import json
import re
from pathlib import Path


class LinkType(Enum):
    """Typed edges for table linking"""
    TABLE_OF = "TABLE_OF"  # Table links to concept IDs
    REFERENCES = "REFERENCES"  # Paragraphs/examples reference table
    EXPLAINS = "EXPLAINS"  # Explanatory paragraphs explain table
    NEAR = "NEAR"  # Adjacency links (same heading/page window)


class CellType(Enum):
    """Detected cell content types"""
    TEXT = "text"
    NUMBER = "number"
    PERCENT = "percent"
    CURRENCY = "currency"
    DATE = "date"
    BLANK = "blank"
    FORMULA = "formula"


@dataclass
class SourceAnchor:
    """Precise source location for traceability"""
    page_number: int
    bbox: Tuple[float, float, float, float]  # (x0, y0, x1, y1)
    extractor: str  # e.g., "pdfplumber", "camelot", "ocr"
    confidence: float = 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CellMeta:
    """Metadata for individual cells"""
    row_index: int
    col_index: int
    bbox: Tuple[float, float, float, float]
    cell_type: CellType
    raw_value: str
    normalized_value: Optional[Union[str, float, int]] = None
    rowspan: int = 1
    colspan: int = 1
    formula_text: Optional[str] = None
    units: Optional[str] = None  # e.g., "USD", "%", "millions"


@dataclass
class FormulaCell:
    """Detected formula within table"""
    cell_address: str  # e.g., "C5"
    formula_text: str
    bbox: Tuple[float, float, float, float]
    source_anchor: SourceAnchor
    confidence: float = 1.0


@dataclass
class DerivedColumn:
    """Inferred computed column"""
    column_index: int
    column_header: str
    rule_description: str  # e.g., "interest = balance × rate"
    input_columns: List[int]
    confidence: float
    is_inferred: bool = True  # True if inferred, False if explicit formula found


@dataclass
class TableLink:
    """Typed link to other objects"""
    link_type: LinkType
    target_id: str  # Concept ID or segment ID
    source_anchor: SourceAnchor
    evidence: str  # Why this link exists
    confidence: float = 1.0


@dataclass
class TableSegment:
    """Complete table segment with all metadata"""
    segment_id: str
    table_id: Optional[str] = None
    table_number: Optional[str] = None  # e.g., "2.1", "Exhibit 3"
    
    # Core structure
    caption: str = ""
    row_headers: List[List[str]] = field(default_factory=list)
    col_headers: List[str] = field(default_factory=list)
    cells: List[List[str]] = field(default_factory=list)  # Raw cell matrix
    
    # Enhanced metadata
    cell_meta: List[CellMeta] = field(default_factory=list)
    footnotes: List[str] = field(default_factory=list)
    units: List[str] = field(default_factory=list)
    
    # Formula handling
    formula_cells: List[FormulaCell] = field(default_factory=list)
    derived_columns: List[DerivedColumn] = field(default_factory=list)
    variable_candidates: Dict[str, str] = field(default_factory=dict)  # header -> variable name
    
    # Description and interpretation
    description: str = ""
    table_summary: str = ""
    table_schema_hint: Optional[str] = None  # e.g., "amortization schedule"
    
    # Linking
    linked_concept_ids: List[str] = field(default_factory=list)
    links: List[TableLink] = field(default_factory=list)
    
    # Source tracking
    source_anchor: SourceAnchor = None
    confidence: float = 1.0
    
    # Context
    heading_path: Optional[str] = None  # e.g., "Chapter 2 / Section 3.1"
    page_span: Tuple[int, int] = None  # (start_page, end_page)


class TableExtractor:
    """Main table extraction and segmentation class"""
    
    def __init__(self, pdf_path: str, use_ocr: bool = False):
        self.pdf_path = pdf_path
        self.use_ocr = use_ocr
        self.tables: List[TableSegment] = []
        
    def extract_all_tables(self) -> List[TableSegment]:
        """Extract all tables from PDF"""
        # Implementation will use pdfplumber, camelot, etc.
        pass
    
    def detect_table_regions(self) -> List[Dict]:
        """Detect table boundaries using multiple strategies"""
        pass
    
    def reconstruct_structure(self, table_region: Dict) -> TableSegment:
        """Reconstruct table structure from detected region"""
        pass
    
    def extract_cell_metadata(self, cells: List[List[str]], bboxes: List[List[Tuple]]) -> List[CellMeta]:
        """Extract metadata for each cell"""
        pass
    
    def detect_formulas(self, table: TableSegment) -> List[FormulaCell]:
        """Detect formulas within table"""
        pass
    
    def infer_computed_columns(self, table: TableSegment) -> List[DerivedColumn]:
        """Infer computed/derived columns"""
        pass
    
    def generate_description(self, table: TableSegment) -> str:
        """Generate table description for embeddings"""
        pass
    
    def link_to_concepts(self, table: TableSegment, concept_taxonomy: Dict) -> List[str]:
        """Link table to concept IDs"""
        pass
    
    def find_cross_references(self, table: TableSegment, document_text: str) -> List[TableLink]:
        """Find paragraphs that reference this table"""
        pass


def generate_segment_id(table_number: Optional[str], page: int, index: int) -> str:
    """Generate stable segment ID"""
    if table_number:
        clean_num = re.sub(r'[^\w]', '_', table_number)
        return f"table_{clean_num}_p{page}"
    return f"table_p{page}_idx{index}"


def normalize_cell_value(raw: str, cell_type: CellType) -> Optional[Union[str, float, int]]:
    """Normalize cell values (e.g., "$1,200" -> 1200)"""
    if cell_type == CellType.CURRENCY:
        # Remove $, commas, convert to float
        cleaned = re.sub(r'[\$,\s]', '', raw)
        try:
            return float(cleaned)
        except:
            return raw
    elif cell_type == CellType.PERCENT:
        cleaned = re.sub(r'[%\s]', '', raw)
        try:
            return float(cleaned) / 100.0
        except:
            return raw
    elif cell_type == CellType.NUMBER:
        cleaned = re.sub(r'[,,\s]', '', raw)
        try:
            if '.' in cleaned:
                return float(cleaned)
            return int(cleaned)
        except:
            return raw
    return raw


def detect_cell_type(value: str) -> CellType:
    """Detect cell content type"""
    if not value or value.strip() == "":
        return CellType.BLANK
    
    value = value.strip()
    
    # Formula patterns
    if re.match(r'^[=@]', value) or re.search(r'[+\-*/=]', value):
        if any(op in value for op in ['SUM', 'AVG', 'IF', 'VLOOKUP', '=']):
            return CellType.FORMULA
    
    # Currency
    if re.match(r'^\$[\d,]+(\.\d{2})?$', value) or 'USD' in value.upper():
        return CellType.CURRENCY
    
    # Percent
    if value.endswith('%') or 'percent' in value.lower():
        return CellType.PERCENT
    
    # Date patterns
    if re.match(r'\d{1,2}[/-]\d{1,2}[/-]\d{2,4}', value):
        return CellType.DATE
    
    # Number
    if re.match(r'^[\d,]+(\.\d+)?$', value.replace(',', '')):
        return CellType.NUMBER
    
    return CellType.TEXT





