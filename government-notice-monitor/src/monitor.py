#!/usr/bin/env python3
"""
Government & Banking Notice Monitor
Autonomous scraper monitoring configured Indian banks, regulators, and government
financial institutions for new official notices with Telegram alerts.

Features:
- Complete website error isolation: failure of one site never stops the run.
- Strict SSL certificate verification: never bypasses certificate validation.
- Preserves existing baseline and prevents alerting on historical/old notices.
- Multi-layer permanent deduplication: official Notice/Document IDs, canonical URLs,
  normalized titles, and legacy SHA-256 fingerprint compatibility.
- Delivery state tracking: marks delivered only after Telegram API confirmation.
- State-write protection: avoids empty timestamp-only commits and merges state safely.
"""

from __future__ import annotations

import argparse
import dataclasses
from datetime import date, datetime, timezone
import gzip
import hashlib
import html
from html.parser import HTMLParser
import json
import logging
import os
import re
import ssl
import sys
import time
from typing import Any, Dict, List, Optional, Set, Tuple
import urllib.error
import urllib.parse
import urllib.request
import zlib

# Setup clean structured logger
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("NoticeMonitor")

# Optional third-party imports with graceful standard-library fallback
try:
    import requests
    from bs4 import BeautifulSoup
    HAS_THIRD_PARTY = True
except ImportError:
    HAS_THIRD_PARTY = False

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

TELEGRAM_MAX_MESSAGE_LENGTH = 4096

# Genuine recruitment, examination, vacancy, and official announcement keywords
NOTICE_KEYWORDS = [
    "recruitment", "recruitments", "vacancy", "vacancies",
    "admit card", "call letter", "hall ticket", "result", "results",
    "selection list", "merit list", "answer key", "examination", "exam schedule",
    "interview", "notification", "advt", "advertisement",
    "current opening", "current openings", "current opportunities",
    "emp-news", "probationary", "specialist officer", "management trainee",
    "apprentice", "grade a", "grade b", "corrigendum", "shortlist",
    "syllabus", "engagement", "deputation", "empanelment", "crp", "po/mt",
    "clerk", "assistant manager", "medical officer", "security officer",
    "waitlist", "joining", "marks"
]

# Routine non-recruitment noise (daily money market operations, vendor RFPs, static menus)
NON_RECRUITMENT_NOISE_PHRASES = [
    "money market operations",
    "variable rate repo",
    "variable rate reverse repo",
    "liquidity adjustment facility",
    "standing deposit facility",
    "marginal standing facility",
    "treasury bills auction",
    "state development loans",
    "auction of government of india",
    "technical bid opening",
    "rfp for selection",
    "rfp for procurement",
    "supply, installation",
    "private car package policy",
    "international year of cooperatives",
    "functions of departments / divisions",
    "history in brief",
    "apply online for home loan",
    "leasing-in of",
    "visit uco bank on whatsapp",
]

# Navigation & static hub link labels (English & Hindi) to discard
IGNORE_PHRASES = {
    "home", "about us", "contact us", "privacy policy", "terms of use",
    "terms & conditions", "disclaimer", "sitemap", "login", "feedback",
    "skip to main content", "skip to content", "screen reader access", "accessibility",
    "facebook", "twitter", "linkedin", "youtube", "instagram", "search",
    "read more", "view all", "click here", "download", "back", "next",
    "english", "hindi", "हिंदी", "close", "menu", "archive", "archives",
    "faqs", "faq", "help", "rti", "right to information", "tenders", "tender",
    "tenders / empanelment", "empanelled vendors", "empaneled vendors",
    "print this page", "customer feedback", "show — top menu", "preferred banking",
    "careers", "career", "recruitments", "recruitment", "current openings",
    "current vacancies", "opportunities@nhb", "master circulars", "circulars",
    "press releases", "notifications", "announcements", "public notice",
    "public notices", "what's new", "latest news", "important links",
    "chief general managers / general managers", "zonal managers", "web manager",
    "chief vigilance officer", "key managerial personnel", "board of directors",
    "important announcement to physical shareholders", "policies, rules & guidelines",
    "institutional finance", "products & services", "ai botsite",
    "apply online", "online application", "register online",
    "निविदाएं", "मुख्य महाप्रबंधक/महाप्रबंधक", "अंचल प्रबंधक",
    "भौतिक शेयरधारकों हेतु महत्वपूर्ण उद्घोषणा", "भर्तियाँ", "वेब मैनेजर",
    "english file download", "hindi file download"
}

# Tracking and transient query parameters to strip during URL canonicalization
STRIP_QUERY_PARAMS = {
    "jsessionid", "phpsessid", "aspsessionid", "sid", "sessionid", "token",
    "auth", "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "fbclid", "gclid", "msclkid", "dclid", "twclid", "_ga", "_gl",
    "mc_cid", "mc_eid", "ref", "referer", "referrer", "source", "src",
    "timestamp", "ts", "t", "_", "rnd", "rand", "random", "cache", "cachebust",
    "nocache", "v", "ver", "version", "lang", "language", "locale", "print"
}

# Query parameters that uniquely identify an official notice or document
NOTICE_ID_QUERY_KEYS = {
    "prid", "id", "nid", "notice_id", "noticeid", "docid", "doc_id",
    "advt", "advtid", "advt_no", "circular_id", "cid", "post_id", "pid", "fileno"
}

# Generic document filenames that do NOT uniquely identify a single notice across a site
GENERIC_DOC_FILENAMES = {
    "notice.pdf", "recruitment.pdf", "advertisement.pdf", "advt.pdf",
    "notification.pdf", "result.pdf", "results.pdf", "corrigendum.pdf",
    "circular.pdf", "document.pdf", "download.pdf", "file.pdf", "index.pdf",
    "english.pdf", "hindi.pdf", "annexure.pdf", "syllabus.pdf", "instructions.pdf"
}

DOCUMENT_EXTENSIONS = (".pdf", ".doc", ".docx", ".xls", ".xlsx")

MONTH_MAP = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}

DATE_PATTERNS = [
    re.compile(r"(?:^|(?<=[^0-9A-Za-z]))(\d{1,2}[-./]\d{1,2}[-./]\d{2,4})(?=$|[^0-9A-Za-z])"),
    re.compile(
        r"(?:^|(?<=[^0-9A-Za-z]))(\d{1,2}(?:st|nd|rd|th)?[\s_./-]+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*[\s_,./-]+\d{2,4})(?=$|[^0-9A-Za-z])",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:^|(?<=[^0-9A-Za-z]))((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*[\s_./-]+\d{1,2}(?:st|nd|rd|th)?[\s_,./-]+\d{2,4})(?=$|[^0-9A-Za-z])",
        re.IGNORECASE,
    ),
    re.compile(r"(?:^|(?<=[^0-9A-Za-z]))(\d{4}-\d{2}-\d{2})(?=$|[^0-9A-Za-z])"),
]

# Official advertisement / circular reference pattern in titles
OFFICIAL_REF_PATTERN = re.compile(
    r"\b((?:CRPD|CRP|HRMD|HRD|CO|HO|ADVT|REF|NOTIFICATION|CIRCULAR)[/._:-]+[A-Z0-9][/A-Z0-9._:-]{4,35})\b",
    re.IGNORECASE,
)


@dataclasses.dataclass
class Notice:
    website_id: str
    org_name: str
    title: str
    url: str
    published_date: Optional[str]
    fingerprint: str
    first_seen: str
    delivered: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Notice:
        return cls(
            website_id=str(data.get("website_id", "")),
            org_name=str(data.get("org_name", "")),
            title=str(data.get("title", "")),
            url=str(data.get("url", "")),
            published_date=data.get("published_date"),
            fingerprint=str(data.get("fingerprint", "")),
            first_seen=str(data.get("first_seen", datetime.now(timezone.utc).isoformat())),
            delivered=bool(data.get("delivered", False)),
        )


class StdlibLinkParser(HTMLParser):
    """State-machine HTML link and context extractor using Python's built-in HTMLParser."""

    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.links: List[Tuple[str, str, Optional[str]]] = []
        self._in_a = False
        self._ignored_depth = 0
        self._current_href: Optional[str] = None
        self._current_title_parts: List[str] = []
        self._current_a_attrs: Dict[str, str] = {}
        self._context_buffer: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        tag_lower = tag.lower()
        if tag_lower in {"script", "style", "noscript"}:
            self._ignored_depth += 1
            return
        if tag_lower in {"tr", "li", "article", "section"}:
            self._context_buffer = []
        if tag_lower == "a":
            attr_dict = {k.lower(): (v or "") for k, v in attrs if k}
            href = attr_dict.get("href", "").strip()
            if href:
                self._in_a = True
                self._current_href = href
                self._current_title_parts = []
                self._current_a_attrs = attr_dict

    def handle_data(self, data: str) -> None:
        if self._ignored_depth > 0:
            return
        cleaned = " ".join(data.split())
        if not cleaned:
            return
        self._context_buffer.append(cleaned)
        if len(self._context_buffer) > 12:
            self._context_buffer.pop(0)
        if self._in_a:
            self._current_title_parts.append(cleaned)

    def handle_endtag(self, tag: str) -> None:
        tag_lower = tag.lower()
        if tag_lower in {"script", "style", "noscript"}:
            if self._ignored_depth > 0:
                self._ignored_depth -= 1
            return
        if tag_lower == "a" and self._in_a:
            self._in_a = False
            raw_href = self._current_href or ""
            title = " ".join(self._current_title_parts).strip()
            if not title:
                title = (
                    self._current_a_attrs.get("title", "").strip()
                    or self._current_a_attrs.get("aria-label", "").strip()
                )
            if raw_href and title:
                full_url = urllib.parse.urljoin(self.base_url, raw_href)
                context_str = " ".join(self._context_buffer)
                pub_date = extract_date(title) or extract_date(context_str)
                self.links.append((full_url, title, pub_date))
            self._current_href = None
            self._current_title_parts = []
            self._current_a_attrs = {}


def legacy_normalize_url(url: str) -> str:
    """Original URL normalization retained for 100% backward-compatible fingerprint lookups
    against existing state/notices.json entries."""
    try:
        if not url or not isinstance(url, str):
            return ""
        parsed = urllib.parse.urlsplit(url.strip())
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
            return ""
        query_params = urllib.parse.parse_qsl(parsed.query)
        cleaned_params = [
            (k, v) for k, v in query_params
            if k.lower() not in {
                "jsessionid", "phpsessid", "sid", "token",
                "utm_source", "utm_medium", "utm_campaign"
            }
        ]
        new_query = urllib.parse.urlencode(cleaned_params)
        clean_path = re.sub(r";jsessionid=[^/?#]+", "", parsed.path, flags=re.IGNORECASE)
        return urllib.parse.urlunsplit((
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            clean_path.rstrip("/") or "/",
            new_query,
            "",
        ))
    except Exception:
        return ""


def normalize_url(url: str) -> str:
    """Safely normalizes URL to prevent duplicates from tracking params, session tokens,
    parameter ordering, redundant slashes, or fragments."""
    try:
        if not url or not isinstance(url, str):
            return ""
        parsed = urllib.parse.urlsplit(url.strip())
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
            return ""
        netloc = parsed.netloc.lower()
        if netloc.endswith(":80"):
            netloc = netloc[:-3]
        elif netloc.endswith(":443"):
            netloc = netloc[:-4]

        query_params = urllib.parse.parse_qsl(parsed.query, keep_blank_values=False)
        cleaned_params = [
            (k, v.strip()) for k, v in query_params
            if k.lower() not in STRIP_QUERY_PARAMS and not k.lower().startswith("utm_")
        ]
        cleaned_params.sort(key=lambda item: (item[0].lower(), item[1]))
        new_query = urllib.parse.urlencode(cleaned_params)

        clean_path = re.sub(r";jsessionid=[^/?#]+", "", parsed.path, flags=re.IGNORECASE)
        clean_path = re.sub(r"/{2,}", "/", clean_path)
        clean_path = clean_path.rstrip("/") or "/"

        return urllib.parse.urlunsplit((
            parsed.scheme.lower(),
            netloc,
            clean_path,
            new_query,
            "",
        ))
    except Exception:
        return ""


def canonical_url_key(url: str) -> str:
    """Scheme- and www-agnostic canonical URL key so http vs https or www. vs non-www
    variants of the exact same URL never trigger duplicate alerts."""
    norm = normalize_url(url)
    if not norm:
        return ""
    parsed = urllib.parse.urlsplit(norm)
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = urllib.parse.unquote(parsed.path).strip().lower()
    path = re.sub(r"/{2,}", "/", path).rstrip("/") or "/"
    return f"{host}{path}?{parsed.query}" if parsed.query else f"{host}{path}"


def normalize_title(title: str) -> str:
    """Normalizes title formatting while preserving distinct identifiers (grades, post names,
    phases, dates, and advertisement numbers) so distinct notices are never merged."""
    if not title:
        return ""
    text = html.unescape(title)
    text = re.sub(r"\s+", " ", text).strip()
    # Remove transient UI badges and file size suffixes that change over time
    text = re.sub(r"\b(?:new|latest)\b\s*!?\s*[-:|]*\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"[\(\[]\s*(?:new|latest|pdf)[^\]\)]*[\)\]]", "", text, flags=re.IGNORECASE)
    text = re.sub(r"[\(\[]\s*\d+(?:\.\d+)?\s*(?:kb|mb|bytes)\s*[\)\]]", "", text, flags=re.IGNORECASE)
    text = re.sub(r"[\(\[]\s*[\)\]]", "", text)  # remove leftover empty brackets/parens
    # Normalize dashes and quotes
    text = text.replace("–", "-").replace("—", "-").replace("’", "'").replace("“", '"').replace("”", '"')
    text = re.sub(r"\s*[-:|]+\s*", " - ", text)
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def compute_fingerprint(normalized_url: str, title: str) -> str:
    """Deterministic SHA-256 fingerprint from stable URL and normalized whitespace title."""
    clean_title = re.sub(r"\s+", " ", title).strip().lower()
    combined = f"{normalized_url}|{clean_title}".encode("utf-8")
    return hashlib.sha256(combined).hexdigest()


def site_group_key(website_id: str) -> str:
    """Groups paired portal IDs pointing to the same institution so a notice discovered
    on both 'sbi' and 'sbi-careers' (or 'rbi' and 'rbi-opportunities', 'idbi-bank' and 'idbi-careers')
    is never sent twice."""
    wid = (website_id or "").strip().lower()
    if wid in {"sbi", "sbi-careers"}:
        return "sbi"
    if wid in {"rbi", "rbi-opportunities"}:
        return "rbi"
    if wid in {"idbi-bank", "idbi-careers"}:
        return "idbi"
    return wid


def extract_notice_id(url: str, title: str, website_id: str = "") -> Optional[str]:
    """Extracts the strongest official notice or document identifier available:
    1. Explicit query parameter ID (e.g. ?prid=63824, ?id=412)
    2. Specific non-generic PDF/document filename (e.g. advt-crpd-po-2026.pdf)
    3. Explicit official advertisement/reference code in title (e.g. CRPD/PO/2026-27/01)"""
    grp = site_group_key(website_id)
    if url:
        try:
            parsed = urllib.parse.urlsplit(url.strip())
            for k, v in urllib.parse.parse_qsl(parsed.query):
                kl = k.lower()
                val = v.strip().lower()
                if kl in NOTICE_ID_QUERY_KEYS and val:
                    return f"{grp}|qid:{kl}={val}"

            path_lower = urllib.parse.unquote(parsed.path).strip().lower()
            if path_lower.endswith(DOCUMENT_EXTENSIONS):
                filename = os.path.basename(path_lower)
                if filename and filename not in GENERIC_DOC_FILENAMES and len(filename) >= 8:
                    return f"{grp}|doc:{filename}"
        except Exception:
            pass

    if title:
        ref_match = OFFICIAL_REF_PATTERN.search(title)
        if ref_match:
            ref_code = re.sub(r"\s+", "", ref_match.group(1)).lower().strip(".,;:-/")
            if len(ref_code) >= 7 and any(ch.isdigit() for ch in ref_code):
                return f"{grp}|ref:{ref_code}"

    return None


def parse_publication_date(date_str: Optional[str]) -> Optional[date]:
    """Parses a date string into a validated datetime.date object.
    Returns None if missing, unparseable, or out of valid calendar bounds."""
    if not date_str or not isinstance(date_str, str):
        return None
    cleaned = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", date_str.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,.-")

    # 1. ISO format YYYY-MM-DD
    m_iso = re.match(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$", cleaned)
    if m_iso:
        try:
            y, m, d = int(m_iso.group(1)), int(m_iso.group(2)), int(m_iso.group(3))
            if 1995 <= y <= 2100:
                return date(y, m, d)
        except ValueError:
            return None

    # 2. Indian numeric format DD-MM-YYYY or DD/MM/YY
    m_dmy = re.match(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})$", cleaned)
    if m_dmy:
        try:
            d, m, y = int(m_dmy.group(1)), int(m_dmy.group(2)), int(m_dmy.group(3))
            if y < 100:
                y += 2000 if y < 70 else 1900
            if 1995 <= y <= 2100:
                return date(y, m, d)
        except ValueError:
            return None

    # 3. DD Month YYYY (e.g. 18 October 2026, 16 Oct, 2026, 08-Oct-2026, 10_oct_2026)
    m_text1 = re.match(r"^(\d{1,2})[\s_,./-]+([A-Za-z]+)[\s_,./-]+(\d{2,4})$", cleaned)
    if m_text1:
        try:
            d = int(m_text1.group(1))
            mon_name = m_text1.group(2).lower()[:4].rstrip(".")
            m = MONTH_MAP.get(mon_name) or MONTH_MAP.get(mon_name[:3])
            y = int(m_text1.group(3))
            if y < 100:
                y += 2000
            if m and 1995 <= y <= 2100:
                return date(y, m, d)
        except ValueError:
            return None

    # 4. Month DD, YYYY (e.g. October 16, 2026, Oct-16-2026)
    m_text2 = re.match(r"^([A-Za-z]+)[\s_,./-]+(\d{1,2})[\s_,./-]+(\d{2,4})$", cleaned)
    if m_text2:
        try:
            mon_name = m_text2.group(1).lower()[:4].rstrip(".")
            m = MONTH_MAP.get(mon_name) or MONTH_MAP.get(mon_name[:3])
            d = int(m_text2.group(2))
            y = int(m_text2.group(3))
            if y < 100:
                y += 2000
            if m and 1995 <= y <= 2100:
                return date(y, m, d)
        except ValueError:
            return None

    return None


def extract_date(text: str) -> Optional[str]:
    """Extracts first valid calendar date from text if available; otherwise returns None."""
    if not text:
        return None
    for pattern in DATE_PATTERNS:
        for match in pattern.finditer(text):
            candidate = match.group(1).strip()
            if len(candidate) >= 6 and parse_publication_date(candidate) is not None:
                return candidate
    return None


def has_only_historical_years(title: str, url: str, baseline_year: int) -> bool:
    """Checks if a notice without an explicit full date only references past years
    strictly older than baseline_year (e.g., '/uploads/2023/', 'Recruitment 2022-23', 'Exam 2024')."""
    combined = f"{title} {urllib.parse.unquote(url or '')}"
    years_found: Set[int] = set()
    for m in re.finditer(r"\b(20\d{2})\b", combined):
        yr = int(m.group(1))
        if 2000 <= yr <= baseline_year + 2:
            years_found.add(yr)
            # Check for academic/financial year range like 2025-26
            after = combined[m.end(): m.end() + 4]
            m_range = re.match(r"^[-/](\d{2})\b", after)
            if m_range:
                sub_yr = 2000 + int(m_range.group(1))
                if sub_yr == yr + 1:
                    years_found.add(sub_yr)
    if not years_found:
        return False
    return max(years_found) < baseline_year


def format_telegram_message(notice: Notice) -> str:
    """Formats a single notice for Telegram HTML delivery within 4096-character limit.
    Includes publication date when available and omits it when unavailable."""
    clean_org = html.escape(notice.org_name.strip() or "Official Portal")
    if len(clean_org) > 200:
        clean_org = clean_org[:197] + "..."

    safe_url = html.escape(notice.url.strip())
    if len(safe_url) > 1000:
        safe_url = safe_url[:1000]

    pub_line = ""
    if notice.published_date and parse_publication_date(notice.published_date) is not None:
        clean_date = html.escape(notice.published_date.strip())[:100]
        pub_line = f"\nPublished: {clean_date}"

    raw_title = " ".join(notice.title.split()).strip()
    clean_title = html.escape(raw_title)

    overhead = len(f"<b>New Official Notice — {clean_org}</b>\n\nTitle: {pub_line}\nLink: {safe_url}") + 32
    max_title_len = max(100, min(2500, TELEGRAM_MAX_MESSAGE_LENGTH - overhead))
    if len(clean_title) > max_title_len:
        clean_title = clean_title[: max_title_len - 3] + "..."

    lines = [
        f"<b>New Official Notice — {clean_org}</b>",
        "",
        f"Title: {clean_title}",
    ]
    if pub_line:
        lines.append(pub_line.lstrip("\n"))
    lines.append(f"Link: {safe_url}")
    msg = "\n".join(lines)
    return msg[:TELEGRAM_MAX_MESSAGE_LENGTH]


_CACHED_SSL_CONTEXT: Optional[ssl.SSLContext] = None
_CACHED_CA_BUNDLE_PATH: Optional[str] = None
_AIA_INTERMEDIATE_URLS = (
    "http://secure.globalsign.com/cacert/gsgccr46alphasslca2025.crt",
    "http://secure.globalsign.com/cacert/gsgccr46ovtlsca2025.crt",
    "http://secure.globalsign.com/cacert/gsgccr46evtlsca2025.crt",
)


def _create_verified_ssl_context() -> ssl.SSLContext:
    """Creates a strict SSL context enforcing certificate and hostname validation
    (CERT_REQUIRED + check_hostname=True) with RFC 5280 AIA intermediate CA support."""
    global _CACHED_SSL_CONTEXT, _CACHED_CA_BUNDLE_PATH
    if _CACHED_SSL_CONTEXT is not None:
        return _CACHED_SSL_CONTEXT

    cafile = os.environ.get("CA_BUNDLE_PATH") or os.environ.get("SSL_CERT_FILE") or None
    ctx = ssl.create_default_context(cafile=cafile)
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    if hasattr(ssl, "OP_LEGACY_SERVER_CONNECT"):
        ctx.options |= ssl.OP_LEGACY_SERVER_CONNECT

    pem_intermediates: List[str] = []
    for aia_url in _AIA_INTERMEDIATE_URLS:
        try:
            with urllib.request.urlopen(aia_url, timeout=4) as resp:
                if resp.status == 200:
                    der_bytes = resp.read()
                    if der_bytes:
                        pem_intermediates.append(ssl.DER_cert_to_PEM_cert(der_bytes))
        except Exception:
            continue
    if pem_intermediates:
        try:
            ctx.load_verify_locations(cadata="\n".join(pem_intermediates))
            default_ca = ssl.get_default_verify_paths().cafile
            if default_ca and os.path.exists(default_ca):
                combined_path = f"/tmp/notice_monitor_ca_{os.getpid()}.pem"
                with open(default_ca, "r", encoding="utf-8", errors="ignore") as f_in:
                    base_pem = f_in.read()
                with open(combined_path, "w", encoding="utf-8") as f_out:
                    f_out.write(base_pem + "\n" + "\n".join(pem_intermediates))
                _CACHED_CA_BUNDLE_PATH = combined_path
        except Exception:
            pass

    _CACHED_SSL_CONTEXT = ctx
    return ctx


def _decode_http_body(raw_bytes: bytes, content_encoding: str = "") -> str:
    """Safely decompresses gzip/deflate HTTP payloads and decodes text."""
    encoding = (content_encoding or "").lower()
    data = raw_bytes
    try:
        if "gzip" in encoding or data[:2] == b"\x1f\x8b":
            data = gzip.decompress(data)
        elif "deflate" in encoding:
            data = zlib.decompress(data)
    except Exception:
        data = raw_bytes
    for charset in ("utf-8", "iso-8859-1", "cp1252"):
        try:
            return data.decode(charset)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def merge_state_files(base_state_path: str, other_state_path: str, output_path: str) -> None:
    """Safely merges two state/notices.json files union-wise without losing any recorded
    notices or downgrading delivered=True back to False."""
    with open(base_state_path, "r", encoding="utf-8") as f1:
        s1 = json.load(f1)
    with open(other_state_path, "r", encoding="utf-8") as f2:
        s2 = json.load(f2)

    merged_notices: Dict[str, Any] = dict(s1.get("notices", {}))
    for fp, n_dict in s2.get("notices", {}).items():
        if fp not in merged_notices:
            merged_notices[fp] = n_dict
        else:
            if n_dict.get("delivered") or merged_notices[fp].get("delivered"):
                merged_notices[fp]["delivered"] = True

    b1 = s1.get("baselined_websites", [])
    b2 = s2.get("baselined_websites", [])
    merged_baselined = sorted(set(b1) | set(b2))

    s1["baseline_completed"] = bool(s1.get("baseline_completed") or s2.get("baseline_completed"))
    if merged_baselined:
        s1["baselined_websites"] = merged_baselined
    s1["notices"] = merged_notices
    if "stats" in s1 and isinstance(s1["stats"], dict):
        s1["stats"]["total_recorded_notices"] = len(merged_notices)

    with open(output_path, "w", encoding="utf-8") as fout:
        json.dump(s1, fout, indent=2, ensure_ascii=False)


class NoticeMonitor:
    def __init__(
        self,
        config_path: str = "config/websites.json",
        state_path: str = "state/notices.json",
        telegram_token: Optional[str] = None,
        telegram_chat_id: Optional[str] = None,
        timeout: int = 15,
        max_retries: int = 2,
        dry_run: bool = False,
    ):
        self.config_path = config_path
        self.state_path = state_path
        self.telegram_token = (
            telegram_token if telegram_token is not None
            else os.environ.get("TELEGRAM_BOT_TOKEN", "")
        ).strip()
        self.telegram_chat_id = (
            telegram_chat_id if telegram_chat_id is not None
            else os.environ.get("TELEGRAM_CHAT_ID", "")
        ).strip()
        self.timeout = timeout
        self.max_retries = max_retries
        self.dry_run = dry_run

        self.session: Optional[Any] = None
        if HAS_THIRD_PARTY:
            self.session = requests.Session()
            self.session.headers.update({
                "User-Agent": DEFAULT_USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9,hi;q=0.8",
            })

        self.websites: List[Dict[str, Any]] = []
        self.configured_hub_urls: Set[str] = set()
        self.state: Dict[str, Any] = {}
        self.existing_notices: Dict[str, Notice] = {}
        self.baselined_websites: Set[str] = set()
        self.baseline_mode: bool = False
        self.baseline_date: date = datetime.now(timezone.utc).date()
        self._state_dirty: bool = False

        # Multi-layer deduplication lookup sets
        self._seen_fingerprints: Set[str] = set()
        self._seen_notice_ids: Set[str] = set()
        self._seen_title_keys: Set[Tuple[str, str]] = set()
        self._seen_specific_urls: Set[str] = set()
        self._seen_all_urls_by_group: Set[Tuple[str, str]] = set()

        # Metrics for run summary
        self.total_configured: int = 0
        self.websites_attempted: int = 0
        self.websites_fetched_ok: int = 0
        self.websites_extraction_ok: int = 0
        self.websites_empty_extraction: int = 0
        self.checked_success: int = 0
        self.checked_errors: int = 0
        self.new_notices_found: int = 0
        self.old_notices_suppressed: int = 0
        self.duplicates_suppressed: int = 0
        self.telegram_sent: int = 0
        self.telegram_failed: int = 0
        self.state_committed: bool = False
        self.error_details: List[Dict[str, str]] = []
        self.site_results: List[Dict[str, Any]] = []

    def _redact(self, text: str) -> str:
        """Redacts any secret values from log strings."""
        out = str(text)
        if self.telegram_token:
            out = out.replace(self.telegram_token, "***REDACTED_TOKEN***")
        if self.telegram_chat_id and len(self.telegram_chat_id) > 4:
            out = out.replace(self.telegram_chat_id, "***REDACTED_CHAT_ID***")
        return out

    def validate_environment(self) -> List[str]:
        """Checks environment variable bindings and returns clear warnings without exposing values."""
        missing: List[str] = []
        if not self.telegram_token:
            missing.append("TELEGRAM_BOT_TOKEN")
        if not self.telegram_chat_id:
            missing.append("TELEGRAM_CHAT_ID")
        if missing and not self.baseline_mode and not self.dry_run:
            logger.warning(
                f"Environment check: Missing {', '.join(missing)}. "
                "Live Telegram alerts cannot be delivered until these secrets are configured."
            )
        return missing

    def load_config(self) -> None:
        """Loads and validates website configuration."""
        if not os.path.exists(self.config_path):
            raise FileNotFoundError(f"Configuration file not found: {self.config_path}")

        with open(self.config_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        websites = data.get("websites", [])
        if not isinstance(websites, list) or not websites:
            raise ValueError(f"Invalid or empty 'websites' list in {self.config_path}")

        seen_ids: Set[str] = set()
        hub_urls: Set[str] = set()
        for idx, site in enumerate(websites, start=1):
            sid = site.get("id")
            if not sid or not site.get("name"):
                raise ValueError(f"Website entry #{idx} is missing required 'id' or 'name'")
            if sid in seen_ids:
                raise ValueError(f"Duplicate website id '{sid}' in {self.config_path}")
            seen_ids.add(sid)
            official = site.get("official_url") or site.get("homepage_url")
            if not official or not normalize_url(official):
                raise ValueError(f"Website '{site.get('name')}' has an invalid URL: {official}")

            for u in [site.get("official_url"), site.get("homepage_url")] + list(site.get("notice_urls", [])):
                if u:
                    ck = canonical_url_key(u)
                    if ck:
                        hub_urls.add(ck)

        self.websites = websites
        self.configured_hub_urls = hub_urls
        self.total_configured = len(self.websites)
        logger.info(f"Loaded {self.total_configured} websites from {self.config_path}")

    def _index_notice_for_dedup(self, notice: Notice) -> None:
        """Indexes a delivered/baselined notice across all deduplication keys."""
        self._seen_fingerprints.add(notice.fingerprint)

        leg_url = legacy_normalize_url(notice.url)
        norm_url = normalize_url(notice.url)
        if leg_url:
            self._seen_fingerprints.add(compute_fingerprint(leg_url, notice.title))
        if norm_url:
            self._seen_fingerprints.add(compute_fingerprint(norm_url, notice.title))
            self._seen_fingerprints.add(compute_fingerprint(norm_url, normalize_title(notice.title)))

        ck_url = canonical_url_key(notice.url)
        grp = site_group_key(notice.website_id)
        if ck_url:
            self._seen_fingerprints.add(compute_fingerprint(ck_url, normalize_title(notice.title)))
            self._seen_all_urls_by_group.add((grp, ck_url))
            # If the URL is a document or has an explicit query ID, index as a specific URL
            lower_url = notice.url.lower()
            if any(lower_url.split("?")[0].endswith(ext) for ext in DOCUMENT_EXTENSIONS) or "?" in norm_url:
                self._seen_specific_urls.add(ck_url)

        nid = extract_notice_id(notice.url, notice.title, notice.website_id)
        if nid:
            self._seen_notice_ids.add(nid)

        ntitle = normalize_title(notice.title)
        if ntitle and len(ntitle) >= 6:
            self._seen_title_keys.add((grp, ntitle))

    def is_duplicate_notice(self, candidate: Notice) -> bool:
        """Checks whether a candidate notice matches any already-delivered notice via:
        1. Exact or canonicalized fingerprint
        2. Official Notice ID / Document filename / Advertisement reference
        3. Specific document/query URL match
        4. Same institution + normalized title match (e.g., notice page vs PDF or URL path move)
        5. Same institution + exact target URL when anchor text switched language (e.g., English -> Hindi)"""
        if candidate.fingerprint in self._seen_fingerprints:
            return True

        leg_url = legacy_normalize_url(candidate.url)
        norm_url = normalize_url(candidate.url)
        ntitle = normalize_title(candidate.title)
        ck_url = canonical_url_key(candidate.url)
        grp = site_group_key(candidate.website_id)

        if leg_url and compute_fingerprint(leg_url, candidate.title) in self._seen_fingerprints:
            return True
        if norm_url:
            if compute_fingerprint(norm_url, candidate.title) in self._seen_fingerprints:
                return True
            if compute_fingerprint(norm_url, ntitle) in self._seen_fingerprints:
                return True
        if ck_url and compute_fingerprint(ck_url, ntitle) in self._seen_fingerprints:
            return True

        nid = extract_notice_id(candidate.url, candidate.title, candidate.website_id)
        if nid and nid in self._seen_notice_ids:
            return True

        if ck_url and ck_url in self._seen_specific_urls:
            return True

        if ntitle and (grp, ntitle) in self._seen_title_keys:
            return True

        # If the exact non-hub target URL was already recorded for this institution, treat as duplicate
        if ck_url and ck_url not in self.configured_hub_urls and (grp, ck_url) in self._seen_all_urls_by_group:
            return True

        # If the target URL is a configured hub URL that was already recorded for this institution,
        # suppress self-referential / menu links pointing back to the hub URL itself
        if ck_url and ck_url in self.configured_hub_urls and (grp, ck_url) in self._seen_all_urls_by_group:
            return True

        return False

    def is_old_notice(self, candidate: Notice) -> Tuple[bool, str]:
        """Determines if a newly discovered notice was published before the monitoring baseline started.
        Never uses an arbitrary recent-date cutoff; compares strictly against the repository's
        actual baseline initialization date or historical archive years."""
        if candidate.published_date:
            pub_dt = parse_publication_date(candidate.published_date)
            if pub_dt is not None and pub_dt < self.baseline_date:
                return True, f"published_date {pub_dt.isoformat()} is older than baseline {self.baseline_date.isoformat()}"

        # Also check if the title itself embeds a full parseable date older than baseline
        title_date_str = extract_date(candidate.title)
        if title_date_str:
            title_dt = parse_publication_date(title_date_str)
            if title_dt is not None and title_dt < self.baseline_date:
                return True, f"title date {title_dt.isoformat()} is older than baseline {self.baseline_date.isoformat()}"

        # Check if the URL filename embeds a full parseable date older than baseline
        url_date_str = extract_date(urllib.parse.unquote(candidate.url or ""))
        if url_date_str:
            url_dt = parse_publication_date(url_date_str)
            if url_dt is not None and url_dt < self.baseline_date:
                return True, f"URL date {url_dt.isoformat()} is older than baseline {self.baseline_date.isoformat()}"

        # If no full date is present, check if title/URL explicitly references only past years (< baseline_date.year)
        if not candidate.published_date and not title_date_str and not url_date_str:
            if has_only_historical_years(candidate.title, candidate.url, self.baseline_date.year):
                return True, f"references only historical archive year(s) prior to {self.baseline_date.year}"

        return False, ""

    def load_state(self) -> None:
        """Loads persistent state file; preserves existing baseline and builds deduplication indices."""
        if not os.path.exists(self.state_path):
            logger.info(f"State file {self.state_path} not found. Initializing new state structure.")
            self.state = {
                "version": 1,
                "last_run_timestamp": None,
                "baseline_completed": False,
                "baseline_timestamp": None,
                "baselined_websites": [],
                "stats": {
                    "total_recorded_notices": 0,
                    "last_run_new_notices": 0,
                    "last_run_errors": 0,
                },
                "notices": {},
            }
            self._state_dirty = True
        else:
            try:
                with open(self.state_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if not isinstance(loaded, dict):
                    raise ValueError("State root must be a JSON object")
                self.state = loaded
            except Exception as e:
                raise RuntimeError(
                    f"Refusing to reset existing state file {self.state_path} after read error: {e}"
                )

        self.baseline_mode = not bool(self.state.get("baseline_completed", False))
        raw_notices = self.state.get("notices", {})
        if not isinstance(raw_notices, dict):
            raw_notices = {}
        self.existing_notices = {
            fp: Notice.from_dict(val)
            for fp, val in raw_notices.items()
            if isinstance(val, dict)
        }

        # Determine baseline date from state or earliest recorded notice timestamp
        baseline_ts = self.state.get("baseline_timestamp")
        earliest_dt: Optional[date] = None
        if baseline_ts and isinstance(baseline_ts, str):
            try:
                earliest_dt = datetime.fromisoformat(baseline_ts.replace("Z", "+00:00")).date()
            except Exception:
                earliest_dt = None

        if earliest_dt is None and self.existing_notices:
            parsed_first_seens: List[date] = []
            for n in self.existing_notices.values():
                if n.first_seen:
                    try:
                        parsed_first_seens.append(
                            datetime.fromisoformat(n.first_seen.replace("Z", "+00:00")).date()
                        )
                    except Exception:
                        continue
            if parsed_first_seens:
                earliest_dt = min(parsed_first_seens)

        self.baseline_date = earliest_dt or datetime.now(timezone.utc).date()

        # Populate per-website baseline tracking from state and existing notices
        saved_baselined = set(self.state.get("baselined_websites", []))
        groups_with_notices: Set[str] = set()
        for n in self.existing_notices.values():
            if n.website_id:
                saved_baselined.add(n.website_id)
                groups_with_notices.add(site_group_key(n.website_id))

        for site in self.websites:
            sid = site.get("id", "")
            if sid and site_group_key(sid) in groups_with_notices:
                saved_baselined.add(sid)

        self.baselined_websites = saved_baselined

        # Build deduplication indices for all already-delivered notices
        self._seen_fingerprints.clear()
        self._seen_notice_ids.clear()
        self._seen_title_keys.clear()
        self._seen_specific_urls.clear()
        self._seen_all_urls_by_group.clear()

        for n in self.existing_notices.values():
            if n.delivered:
                self._index_notice_for_dedup(n)

        if self.baseline_mode:
            logger.info("BASELINE MODE ACTIVE: First run will record notices without dispatching Telegram alerts.")
        else:
            logger.info(
                f"Loaded state with {len(self.existing_notices)} recorded notices "
                f"(baseline date: {self.baseline_date.isoformat()}, "
                f"baselined sites: {len(self.baselined_websites)})."
            )

    def fetch_url_with_reason(self, url: str) -> Tuple[Optional[str], Optional[str]]:
        """Fetches page content with strict SSL verification, timeout, and exponential backoff retry.
        Returns (html_text, error_reason)."""
        if not normalize_url(url):
            return None, f"Invalid or non-HTTP URL: {url}"

        last_error = "Unknown fetch failure"
        ctx = _create_verified_ssl_context()
        cafile = (
            os.environ.get("CA_BUNDLE_PATH")
            or os.environ.get("SSL_CERT_FILE")
            or _CACHED_CA_BUNDLE_PATH
            or True
        )

        for attempt in range(self.max_retries + 1):
            try:
                if HAS_THIRD_PARTY and self.session:
                    resp = self.session.get(url, timeout=(10, self.timeout), verify=cafile)
                    if resp.status_code == 200:
                        if not resp.text or not resp.text.strip():
                            return None, f"HTTP 200 with empty response body from {url}"
                        return resp.text, None
                    if resp.status_code in {403, 404, 410}:
                        last_error = f"HTTP {resp.status_code} client error on {url}"
                        logger.warning(f"{last_error} (skipping retry)")
                        return None, last_error
                    if resp.status_code in {429, 500, 502, 503, 504}:
                        last_error = f"HTTP {resp.status_code} transient error on {url}"
                        logger.warning(f"{last_error} (attempt {attempt + 1}/{self.max_retries + 1})")
                    else:
                        last_error = f"HTTP {resp.status_code} received from {url}"
                        logger.warning(last_error)
                        return None, last_error
                else:
                    ctx = _create_verified_ssl_context()
                    req = urllib.request.Request(
                        url,
                        headers={
                            "User-Agent": DEFAULT_USER_AGENT,
                            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                            "Accept-Language": "en-US,en;q=0.9,hi;q=0.8",
                            "Accept-Encoding": "gzip, deflate",
                        },
                    )
                    with urllib.request.urlopen(req, timeout=self.timeout, context=ctx) as resp:
                        if resp.status == 200:
                            raw_bytes = resp.read()
                            encoding = resp.info().get("Content-Encoding", "")
                            decoded = _decode_http_body(raw_bytes, encoding)
                            if not decoded or not decoded.strip():
                                return None, f"HTTP 200 with empty response body from {url}"
                            return decoded, None
                        last_error = f"HTTP {resp.status} received from {url}"
                        return None, last_error
            except urllib.error.HTTPError as http_err:
                last_error = f"HTTP {http_err.code} ({http_err.reason}) on {url}"
                if http_err.code in {403, 404, 410}:
                    logger.warning(f"{last_error} (skipping retry)")
                    return None, last_error
                logger.warning(f"{last_error} (attempt {attempt + 1}/{self.max_retries + 1})")
            except ssl.SSLCertVerificationError as ssl_err:
                last_error = f"SSL certificate verification failed on {url}: {ssl_err}"
                logger.warning(f"{last_error} (strict SSL enforced; skipping retry)")
                return None, last_error
            except Exception as exc:
                err_str = str(exc)
                if "CERTIFICATE_VERIFY_FAILED" in err_str.upper():
                    last_error = f"SSL certificate verification failed on {url}: {exc}"
                    logger.warning(f"{last_error} (strict SSL enforced; skipping retry)")
                    return None, last_error
                last_error = f"{type(exc).__name__} on {url}: {exc}"
                if attempt == self.max_retries:
                    logger.warning(f"Failed to fetch {url} after {self.max_retries + 1} attempts: {exc}")
                    return None, last_error

            if attempt < self.max_retries:
                time.sleep(1.5 * (attempt + 1))

        return None, last_error

    def fetch_url(self, url: str) -> Optional[str]:
        """Compatibility wrapper returning HTML text or None."""
        text, _ = self.fetch_url_with_reason(url)
        return text

    def parse_links_from_html(self, html_content: str, base_url: str) -> List[Tuple[str, str, Optional[str]]]:
        """Parses links with titles and dates from HTML.
        Returns list of (full_url, title, published_date)."""
        results: List[Tuple[str, str, Optional[str]]] = []
        if not html_content or not isinstance(html_content, str):
            return results

        if HAS_THIRD_PARTY:
            try:
                soup = BeautifulSoup(html_content, "html.parser")
                for a_tag in soup.find_all("a", href=True):
                    raw_href = str(a_tag.get("href", "")).strip()
                    title_text = " ".join(a_tag.get_text().split())
                    if not title_text:
                        title_text = str(a_tag.get("title") or a_tag.get("aria-label") or "").strip()

                    if not raw_href or not title_text:
                        continue
                    if raw_href.lower().startswith(("javascript:", "mailto:", "tel:", "data:", "#")):
                        continue

                    full_url = urllib.parse.urljoin(base_url, raw_href)
                    if not normalize_url(full_url):
                        continue

                    parent_text = ""
                    parent = a_tag.find_parent(["tr", "li", "div", "p", "article"])
                    if parent:
                        parent_text = " ".join(parent.get_text().split())[:300]
                    extracted_date = extract_date(title_text) or extract_date(parent_text)

                    results.append((full_url, title_text, extracted_date))
                return results
            except Exception as exc:
                logger.warning(f"BeautifulSoup parser error on {base_url}: {exc}; falling back to stdlib parser")

        try:
            parser = StdlibLinkParser(base_url)
            parser.feed(html_content)
            for full_url, title, extracted_date in parser.links:
                if full_url.lower().startswith(("javascript:", "mailto:", "tel:", "data:", "#")):
                    continue
                if not normalize_url(full_url):
                    continue
                results.append((full_url, title, extracted_date))
        except Exception as exc:
            logger.warning(f"HTML parsing error on {base_url}: {exc}")

        return results

    def is_relevant_notice(self, title: str, url: str, is_dedicated_page: bool) -> bool:
        """Determines if a discovered link is a genuine official notice rather than
        navigation chrome, static hub links, vendor RFPs, or routine liquidity press releases."""
        clean_title = " ".join(title.lower().split())
        norm_title = normalize_title(title)
        clean_url = url.lower()

        if clean_title in IGNORE_PHRASES or norm_title in IGNORE_PHRASES or len(clean_title) < 5:
            return False

        if any(phrase in clean_title for phrase in NON_RECRUITMENT_NOISE_PHRASES):
            return False

        parsed_path = urllib.parse.urlsplit(clean_url).path
        if any(parsed_path.endswith(ext) for ext in [".jpg", ".jpeg", ".png", ".gif", ".svg", ".css", ".js", ".ico", ".mp4", ".mp3", ".zip"]):
            return False

        # Exclude links that merely point back to a configured top-level hub/navigation URL
        ck = canonical_url_key(url)
        if ck in self.configured_hub_urls:
            return False

        if is_dedicated_page:
            if any(parsed_path.endswith(ext) for ext in DOCUMENT_EXTENSIONS):
                return True
            if any(k in clean_url for k in [
                "notice", "recruitment", "result", "admit", "career",
                "circular", "vacancy", "vacancies", "opening", "advt", "press"
            ]):
                return True
            if any(k in clean_title for k in NOTICE_KEYWORDS):
                return True
            return len(clean_title) >= 15

        has_keyword = (
            any(k in clean_title for k in NOTICE_KEYWORDS)
            or (
                any(parsed_path.endswith(ext) for ext in DOCUMENT_EXTENSIONS)
                and any(k in clean_url for k in ["recruit", "career", "vacanc", "notice", "result", "admit", "advt", "exam"])
            )
        )
        return has_keyword

    def scrape_website_detailed(self, site: Dict[str, Any]) -> Tuple[List[Notice], int, List[str]]:
        """Scrapes a single website safely and deduplicates intra-site links.
        Returns (discovered_notices, pages_fetched_ok, page_error_messages)."""
        site_id = site.get("id", "unknown")
        org_name = site.get("name", site_id)
        notice_urls = site.get("notice_urls", [])
        homepage_url = site.get("official_url") or site.get("homepage_url", "")

        discovered_notices: List[Notice] = []
        seen_urls_in_site: Set[str] = set()
        seen_in_site_fps: Set[str] = set()
        seen_in_site_titles: Set[str] = set()
        seen_in_site_nids: Set[str] = set()
        seen_in_site_doc_urls: Set[str] = set()
        page_errors: List[str] = []

        pages_to_check: List[Tuple[str, bool]] = []
        for url in notice_urls:
            pages_to_check.append((url, True))
        if homepage_url and all(u != homepage_url for u, _ in pages_to_check):
            pages_to_check.append((homepage_url, False))

        pages_fetched_ok = 0
        uses_custom_fetch = "fetch_url" in self.__dict__

        for page_url, is_dedicated in pages_to_check:
            if not is_dedicated and (discovered_notices or pages_fetched_ok > 0):
                break

            try:
                if uses_custom_fetch:
                    html_text = self.fetch_url(page_url)
                    err_reason = None if html_text else f"Empty or failed response from {page_url}"
                else:
                    html_text, err_reason = self.fetch_url_with_reason(page_url)

                if not html_text:
                    if err_reason:
                        page_errors.append(err_reason)
                    continue

                pages_fetched_ok += 1
                links = self.parse_links_from_html(html_text, page_url)

                for full_url, title, pub_date in links:
                    norm_url = normalize_url(full_url)
                    if not norm_url:
                        continue

                    if not self.is_relevant_notice(title, norm_url, is_dedicated):
                        continue

                    # Preserve legacy fingerprint format for backward-compatible state keys
                    leg_url = legacy_normalize_url(full_url) or norm_url
                    fp = compute_fingerprint(leg_url, title)
                    ntitle = normalize_title(title)
                    ck_url = canonical_url_key(full_url)
                    nid = extract_notice_id(full_url, title, site_id)
                    is_doc = any(norm_url.split("?")[0].endswith(ext) for ext in DOCUMENT_EXTENSIONS)

                    # Intra-site deduplication (prevents duplicate links on the same page or across endpoints)
                    if norm_url in seen_urls_in_site or (ck_url and ck_url in seen_urls_in_site):
                        for prev_n in discovered_notices:
                            if normalize_url(prev_n.url) == norm_url or (ck_url and canonical_url_key(prev_n.url) == ck_url):
                                if not prev_n.published_date and pub_date:
                                    prev_n.published_date = pub_date
                                break
                        continue
                    if fp in seen_in_site_fps:
                        continue
                    if ntitle and ntitle in seen_in_site_titles:
                        continue
                    if nid and nid in seen_in_site_nids:
                        continue
                    if is_doc and ck_url in seen_in_site_doc_urls:
                        continue

                    seen_urls_in_site.add(norm_url)
                    if ck_url:
                        seen_urls_in_site.add(ck_url)
                    seen_in_site_fps.add(fp)
                    if ntitle:
                        seen_in_site_titles.add(ntitle)
                    if nid:
                        seen_in_site_nids.add(nid)
                    if is_doc and ck_url:
                        seen_in_site_doc_urls.add(ck_url)

                    notice = Notice(
                        website_id=site_id,
                        org_name=org_name,
                        title=title,
                        url=full_url,
                        published_date=pub_date,
                        fingerprint=fp,
                        first_seen=datetime.now(timezone.utc).isoformat(),
                        delivered=False,
                    )
                    discovered_notices.append(notice)
            except Exception as exc:
                err_msg = f"Error scraping {page_url} for {org_name}: {exc}"
                page_errors.append(err_msg)
                logger.warning(err_msg)

        if pages_fetched_ok == 0:
            detail = "; ".join(page_errors[:2]) if page_errors else f"All endpoints unreachable for {org_name}"
            raise RuntimeError(detail)

        return discovered_notices, pages_fetched_ok, page_errors

    def scrape_website(self, site: Dict[str, Any]) -> List[Notice]:
        """Scrapes a single website and returns discovered notices."""
        notices, _, _ = self.scrape_website_detailed(site)
        return notices

    def send_telegram_alert(self, notice: Notice) -> bool:
        """Sends a single Telegram alert formatted using safe HTML mode.
        Returns True only if Telegram confirms delivery (or if dry_run is enabled)."""
        if not normalize_url(notice.url):
            logger.error(f"Refusing to send alert with invalid non-HTTP notice URL: {notice.url}")
            return False

        message_html = format_telegram_message(notice)

        if self.dry_run:
            logger.info(f"[DRY-RUN ALERT] {notice.org_name}: {notice.title}")
            return True

        missing_vars: List[str] = []
        if not self.telegram_token:
            missing_vars.append("TELEGRAM_BOT_TOKEN")
        if not self.telegram_chat_id:
            missing_vars.append("TELEGRAM_CHAT_ID")
        if missing_vars:
            logger.error(
                f"Cannot send Telegram alert for '{notice.title}': "
                f"Missing required environment variable(s): {', '.join(missing_vars)}."
            )
            return False

        url_endpoint = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
        payload = {
            "chat_id": self.telegram_chat_id,
            "text": message_html,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        }

        for attempt in range(2):
            try:
                if HAS_THIRD_PARTY and self.session:
                    resp = self.session.post(url_endpoint, json=payload, timeout=12)
                    if resp.status_code == 200:
                        data = resp.json()
                        if data.get("ok"):
                            return True
                        logger.warning(f"Telegram API returned ok=false for {notice.org_name}")
                        return False
                    if resp.status_code == 429:
                        retry_after = 3
                        try:
                            retry_after = int(resp.json().get("parameters", {}).get("retry_after", 3))
                        except Exception:
                            pass
                        logger.warning(f"Telegram rate limited (429). Waiting {retry_after}s...")
                        time.sleep(min(retry_after, 10))
                        continue
                    logger.warning(f"Telegram HTTP {resp.status_code} for {notice.org_name}")
                    return False
                else:
                    data_bytes = json.dumps(payload).encode("utf-8")
                    req = urllib.request.Request(
                        url_endpoint,
                        data=data_bytes,
                        headers={"Content-Type": "application/json"},
                    )
                    with urllib.request.urlopen(req, timeout=12) as resp:
                        resp_data = json.loads(resp.read().decode("utf-8"))
                        return bool(resp_data.get("ok"))
            except Exception as exc:
                safe_err = self._redact(str(exc))
                logger.warning(f"Telegram send error for notice {notice.fingerprint[:8]}: {safe_err}")
                if attempt == 0:
                    time.sleep(1.5)
        return False

    def save_state(self, force: bool = False) -> None:
        """Atomically saves state to disk only when state records have meaningfully changed
        (or when force=True). Re-reads disk state before writing to merge any concurrent updates."""
        if not self._state_dirty and not force and os.path.exists(self.state_path):
            logger.info("No new notices or delivery state changes. Preserving existing state file without rewrite.")
            return

        state_dir = os.path.dirname(os.path.abspath(self.state_path))
        os.makedirs(state_dir, exist_ok=True)

        # Merge with any notices currently on disk so no previously recorded fingerprint is ever lost
        if os.path.exists(self.state_path):
            try:
                with open(self.state_path, "r", encoding="utf-8") as f_disk:
                    disk_state = json.load(f_disk)
                if isinstance(disk_state, dict):
                    for fp, disk_n in disk_state.get("notices", {}).items():
                        if isinstance(disk_n, dict):
                            if fp not in self.existing_notices:
                                self.existing_notices[fp] = Notice.from_dict(disk_n)
                            elif disk_n.get("delivered"):
                                self.existing_notices[fp].delivered = True
                    for wid in disk_state.get("baselined_websites", []):
                        self.baselined_websites.add(str(wid))
            except Exception:
                pass

        now_iso = datetime.now(timezone.utc).isoformat()
        self.state["version"] = 1
        self.state["last_run_timestamp"] = now_iso
        self.state["baseline_completed"] = True
        if not self.state.get("baseline_timestamp"):
            self.state["baseline_timestamp"] = f"{self.baseline_date.isoformat()}T00:00:00+00:00"
        self.state["baselined_websites"] = sorted(self.baselined_websites)
        self.state["stats"] = {
            "total_recorded_notices": len(self.existing_notices),
            "last_run_new_notices": self.new_notices_found,
            "last_run_errors": self.checked_errors,
            "last_run_extraction_ok": self.websites_extraction_ok,
            "last_run_fetched_ok": self.websites_fetched_ok,
        }
        self.state["notices"] = {
            fp: notice.to_dict() for fp, notice in self.existing_notices.items()
        }

        tmp_path = f"{self.state_path}.tmp.{os.getpid()}"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, self.state_path)
            self.state_committed = True
            logger.info(f"Successfully saved persistent state to {self.state_path}")
        except Exception as e:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise RuntimeError(f"Failed to persist state file: {e}")

    def run(self) -> int:
        """Executes the full monitoring run across all configured websites."""
        logger.info("=" * 60)
        logger.info("Starting Indian Government & Banking Notice Monitor Run")
        logger.info("=" * 60)

        self.load_config()
        self.load_state()
        self.validate_environment()

        # Collect previously undelivered notices for retry (unless they are now recognized as duplicates or old)
        new_candidates: List[Notice] = []
        for n in list(self.existing_notices.values()):
            if not n.delivered:
                if self.is_duplicate_notice(n):
                    n.delivered = True
                    self._state_dirty = True
                    continue
                is_old, _ = self.is_old_notice(n)
                if is_old:
                    n.delivered = True
                    self._index_notice_for_dedup(n)
                    self._state_dirty = True
                    continue
                new_candidates.append(n)

        # Track candidates queued in the current run so cross-page/cross-site duplicates in the same run are suppressed
        batch_fingerprints: Set[str] = {n.fingerprint for n in new_candidates}
        batch_notice_ids: Set[str] = set()
        batch_title_keys: Set[Tuple[str, str]] = set()
        batch_doc_urls: Set[str] = set()
        for n in new_candidates:
            nid = extract_notice_id(n.url, n.title, n.website_id)
            if nid:
                batch_notice_ids.add(nid)
            nt = normalize_title(n.title)
            if nt:
                batch_title_keys.add((site_group_key(n.website_id), nt))
            ck = canonical_url_key(n.url)
            if ck and any(n.url.lower().split("?")[0].endswith(ext) for ext in DOCUMENT_EXTENSIONS):
                batch_doc_urls.add(ck)

        for index, site in enumerate(self.websites, start=1):
            self.websites_attempted += 1
            site_id = site.get("id", f"site-{index}")
            org_name = site.get("name", site_id)
            site_is_first_baseline = (
                self.baseline_mode
                or (site_id not in self.baselined_websites and site_group_key(site_id) not in {
                    site_group_key(w) for w in self.baselined_websites
                })
            )
            logger.info(f"[{index}/{self.total_configured}] Checking: {org_name} ({site_id})")

            try:
                notices, pages_ok, page_errs = self.scrape_website_detailed(site)
                self.websites_fetched_ok += 1

                if len(notices) > 0:
                    self.websites_extraction_ok += 1
                    self.checked_success += 1
                    logger.info(f"  ✓ {org_name}: HTTP 200 & extracted {len(notices)} candidate notice links")
                else:
                    self.websites_empty_extraction += 1
                    logger.warning(
                        f"  ! {org_name}: HTTP 200 fetched ({pages_ok} page(s)), "
                        "but 0 relevant notices extracted"
                    )

                self.site_results.append({
                    "id": site_id,
                    "name": org_name,
                    "status": "extraction_ok" if len(notices) > 0 else "fetched_empty",
                    "http_success": True,
                    "content_success": True,
                    "parsing_success": len(notices) > 0,
                    "notices_found": len(notices),
                    "partial_errors": page_errs,
                })

                if site_is_first_baseline:
                    if not self.baseline_mode:
                        logger.info(
                            f"  [SITE BASELINE] Recording {len(notices)} initial notices for newly added source "
                            f"'{org_name}' ({site_id}) without sending historical alerts."
                        )
                    for n in notices:
                        if n.fingerprint not in self.existing_notices:
                            n.delivered = True
                            self.existing_notices[n.fingerprint] = n
                            self._index_notice_for_dedup(n)
                            self._state_dirty = True
                    if site_id not in self.baselined_websites:
                        self.baselined_websites.add(site_id)
                        self._state_dirty = True
                else:
                    if site_id not in self.baselined_websites:
                        self.baselined_websites.add(site_id)
                        self._state_dirty = True

                    for n in notices:
                        # 1. Check multi-layer deduplication against saved state
                        if self.is_duplicate_notice(n):
                            self.duplicates_suppressed += 1
                            continue

                        # 2. Check deduplication against candidates already queued in this run
                        nid = extract_notice_id(n.url, n.title, n.website_id)
                        nt = normalize_title(n.title)
                        grp = site_group_key(n.website_id)
                        ck = canonical_url_key(n.url)
                        is_doc = any(n.url.lower().split("?")[0].endswith(ext) for ext in DOCUMENT_EXTENSIONS)

                        if (
                            n.fingerprint in batch_fingerprints
                            or (nid and nid in batch_notice_ids)
                            or (nt and (grp, nt) in batch_title_keys)
                            or (is_doc and ck in batch_doc_urls)
                        ):
                            self.duplicates_suppressed += 1
                            continue

                        # 3. Validate publication date / historical year so old notices are never alerted
                        is_old, old_reason = self.is_old_notice(n)
                        if is_old:
                            self.old_notices_suppressed += 1
                            logger.info(
                                f"  [OLD NOTICE SUPPRESSED] {org_name}: '{n.title[:70]}' ({old_reason}). "
                                "Recording in state without Telegram alert."
                            )
                            n.delivered = True
                            self.existing_notices[n.fingerprint] = n
                            self._index_notice_for_dedup(n)
                            self._state_dirty = True
                            continue

                        # Genuinely new notice
                        new_candidates.append(n)
                        batch_fingerprints.add(n.fingerprint)
                        if nid:
                            batch_notice_ids.add(nid)
                        if nt:
                            batch_title_keys.add((grp, nt))
                        if is_doc and ck:
                            batch_doc_urls.add(ck)

            except Exception as exc:
                self.checked_errors += 1
                self.error_details.append({"site_id": site_id, "name": org_name, "error": str(exc)})
                self.site_results.append({
                    "id": site_id,
                    "name": org_name,
                    "status": "error",
                    "http_success": False,
                    "content_success": False,
                    "parsing_success": False,
                    "notices_found": 0,
                    "error": str(exc),
                })
                logger.error(f"  ✗ {org_name} failed: {exc}. Continuing to next website.")

        if self.baseline_mode:
            logger.info(
                f"[BASELINE INITIALIZATION] Initial baseline completed. "
                f"Recorded {len(self.existing_notices)} existing notices. Zero alerts dispatched."
            )
            self._state_dirty = True
            self.save_state(force=True)
            self.print_summary()
            return 0

        self.new_notices_found = len(new_candidates)
        logger.info(f"Discovered {self.new_notices_found} genuinely new/pending notices requiring alerts.")

        for n in new_candidates:
            # Double-check deduplication immediately before sending so no duplicate can ever slip through
            if n.delivered or self.is_duplicate_notice(n):
                n.delivered = True
                self.existing_notices[n.fingerprint] = n
                self._index_notice_for_dedup(n)
                continue

            success = self.send_telegram_alert(n)
            if success:
                self.telegram_sent += 1
                n.delivered = True
                self.existing_notices[n.fingerprint] = n
                self._index_notice_for_dedup(n)
                self._state_dirty = True
                time.sleep(0.25)
            else:
                self.telegram_failed += 1
                n.delivered = False
                self.existing_notices[n.fingerprint] = n
                self._state_dirty = True
                logger.warning(f"Telegram notification failed for: {n.title}. Preserving for future retry.")

        self.save_state()
        self.print_summary()
        return 0

    def print_summary(self) -> None:
        """Outputs structured run summary distinguishing HTTP fetch from notice extraction."""
        print("\n" + "=" * 64)
        print("               MONITOR EXECUTION SUMMARY")
        print("=" * 64)
        print(f"Total websites configured            : {self.total_configured}")
        print(f"Websites attempted                   : {self.websites_attempted}")
        print(f"Websites successfully fetched (HTTP) : {self.websites_fetched_ok}")
        print(f"Websites with notice extraction      : {self.websites_extraction_ok}")
        print(f"Websites fetched with 0 notices      : {self.websites_empty_extraction}")
        print(f"Websites with HTTP/SSL/conn errors   : {self.checked_errors}")
        print(f"Old notices suppressed               : {self.old_notices_suppressed}")
        print(f"New notices discovered               : {self.new_notices_found}")
        print(f"Telegram messages sent               : {self.telegram_sent}")
        print(f"Notifications failed / pending       : {self.telegram_failed}")
        print(f"State changes committed to disk      : {'Yes' if self.state_committed else 'No'}")
        if self.baseline_mode:
            print("Run type                             : BASELINE INITIALIZATION (No alerts sent)")
        elif self.dry_run:
            print("Run type                             : DRY-RUN SIMULATION")
        else:
            print("Run type                             : LIVE MONITORING RUN")
        print("=" * 64)

        if self.error_details:
            print("\nIsolated Website Errors (did not crash run):")
            for err in self.error_details:
                print(f" - [{err['site_id']}] {err['name']}: {err['error']}")
            print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Government & Banking Notice Monitor")
    parser.add_argument("--config", default="config/websites.json", help="Path to websites.json")
    parser.add_argument("--state", default="state/notices.json", help="Path to notices.json")
    parser.add_argument("--timeout", type=int, default=15, help="HTTP timeout in seconds")
    parser.add_argument("--dry-run", action="store_true", help="Simulate Telegram alerts without live API calls")
    parser.add_argument(
        "--merge-state",
        nargs=3,
        metavar=("BASE", "OTHER", "OUT"),
        help="Union-merge two state/notices.json files safely without losing delivered notices",
    )
    args = parser.parse_args()

    if args.merge_state:
        merge_state_files(args.merge_state[0], args.merge_state[1], args.merge_state[2])
        sys.exit(0)

    monitor = NoticeMonitor(
        config_path=args.config,
        state_path=args.state,
        timeout=args.timeout,
        dry_run=args.dry_run,
    )
    exit_code = monitor.run()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
