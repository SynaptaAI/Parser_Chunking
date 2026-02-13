import re
import logging
from typing import Dict, List, Optional

import requests

from .models import ContentBlock

logger = logging.getLogger(__name__)


ISBN_PATTERN = re.compile(r"ISBN(?:-1[03])?:?\s*((?:97[89][- ]?)?(?:\d[- ]?){9}[\dXx])")


def extract_isbn(blocks: List[ContentBlock], max_pages: int = 15) -> Optional[str]:
    for b in blocks:
        if b.page_idx >= max_pages:
            break
        text = b.text or ""
        if not text:
            continue
        m = ISBN_PATTERN.search(text)
        if m:
            raw = m.group(1)
            return re.sub(r"[\s-]", "", raw)
    return None


def fetch_google_books(isbn: str) -> Optional[Dict[str, str]]:
    url = f"https://www.googleapis.com/books/v1/volumes?q=isbn:{isbn}"
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if data.get("items"):
                info = data["items"][0].get("volumeInfo", {})
                return {
                    "isbn": isbn,
                    "title": info.get("title"),
                    "authors": info.get("authors", []),
                    "publisher": info.get("publisher"),
                    "publishedDate": info.get("publishedDate"),
                    "description": info.get("description"),
                    "edition": info.get("edition"),
                    "source": "Google Books",
                }
    except Exception as e:
        logger.warning("Google Books fetch failed: %s", e)
    return None


def fetch_open_library(isbn: str) -> Optional[Dict[str, str]]:
    key = f"ISBN:{isbn}"
    url = f"https://openlibrary.org/api/books?bibkeys={key}&jscmd=data&format=json"
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if key in data:
                info = data[key]
                authors = [a.get("name") for a in info.get("authors", [])]
                return {
                    "isbn": isbn,
                    "title": info.get("title"),
                    "authors": authors,
                    "publisher": info.get("publishers", [{}])[0].get("name") if info.get("publishers") else None,
                    "publishedDate": info.get("publish_date"),
                    "description": info.get("notes") or info.get("description"),
                    "edition": info.get("edition_name"),
                    "source": "Open Library",
                }
    except Exception as e:
        logger.warning("Open Library fetch failed: %s", e)
    return None


def extract_metadata(blocks: List[ContentBlock], max_pages: int = 15) -> Dict[str, str]:
    isbn = extract_isbn(blocks, max_pages=max_pages)
    base = {
        "isbn": isbn,
        "title": None,
        "authors": [],
        "edition": None,
        "publisher": None,
    }
    if not isbn:
        return base
    meta = fetch_google_books(isbn)
    if meta:
        base.update(meta)
        return base
    meta = fetch_open_library(isbn)
    if meta:
        base.update(meta)
        return base
    return base
