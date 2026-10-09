"""PDF text extraction and arXiv/URL fetching."""

import ipaddress
import re
import socket
from urllib.parse import urlparse

import httpx2 as httpx
from pypdf import PdfReader

MAX_TEXT_CHARS = 400_000  # hard ceiling: ~100k tokens, well inside Haiku's 1M window
MAX_PDF_BYTES = 32 * 1024 * 1024
MAX_REDIRECTS = 5

ARXIV_PATTERNS = [
    # https://arxiv.org/abs/2401.12345 or /pdf/2401.12345
    re.compile(r"(?<![a-z0-9])arxiv\.org/(?:abs|pdf)/([0-9]{4}\.[0-9]{4,5})(v[0-9]+)?", re.I),
    # old-style: https://arxiv.org/abs/cs/0112017 (categories may be dotted, e.g. math.GT/0309136)
    re.compile(r"(?<![a-z0-9])arxiv\.org/(?:abs|pdf)/([a-z.\-]+/[0-9]{7})(v[0-9]+)?", re.I),
]


def _host_is_public(hostname: str) -> bool:
    """Reject loopback/private/link-local targets (basic SSRF guard)."""
    try:
        addr = ipaddress.ip_address(hostname)
    except ValueError:
        pass  # a domain name; resolve it
    else:
        return addr.is_global
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return False
    if not infos:
        return False
    return all(ipaddress.ip_address(i[4][0]).is_global for i in infos)


def check_url_target(url: str) -> None:
    """Raise ValueError if the URL points at a non-public host."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("URL must be http:// or https://")
    host = (parsed.hostname or "").lower()
    if not host or not _host_is_public(host):
        raise ValueError("URL host is not allowed (private, loopback, or unresolvable).")


def extract_pdf_text(data: bytes) -> tuple[str, str]:
    """Return (title, full_text) from raw PDF bytes."""
    import io

    reader = PdfReader(io.BytesIO(data))
    pages = []
    for page in reader.pages:
        pages.append(page.extract_text() or "")
    full_text = "\n\n".join(pages).strip()
    if not full_text:
        raise ValueError("Could not extract any text from this PDF "
                         "(it may be a scanned/image-only document).")
    full_text = full_text[:MAX_TEXT_CHARS]

    # Best-effort title: first page, first non-empty line, trimmed.
    title = ""
    first_page = pages[0] if pages else ""
    for line in first_page.splitlines():
        line = line.strip()
        if len(line) >= 5:
            title = line[:200]
            break
    return title or "Untitled paper", full_text


def arxiv_pdf_url(url: str) -> str | None:
    """If url is an arXiv abs/pdf page, return the direct PDF URL (else None)."""
    for pat in ARXIV_PATTERNS:
        m = pat.search(url)
        if m:
            ident = m.group(1) + (m.group(2) or "")
            return f"https://arxiv.org/pdf/{ident}"
    return None


def fetch_pdf_from_url(url: str) -> tuple[str, str, bytes]:
    """Fetch a PDF from a URL (arXiv links are converted to their PDF endpoint).

    Returns (title, source_url, pdf_bytes). Raises ValueError on failure.
    """
    target = arxiv_pdf_url(url) or url
    check_url_target(target)
    # arXiv likes a real-looking User-Agent.
    headers = {"User-Agent": "research-paper-faq/1.0 (mailto:ryansanford4@gmail.com)"}
    try:
        # Follow redirects manually so every hop's host is validated BEFORE
        # we connect to it — httpx's automatic redirects would fetch first
        # and let us check only afterwards.
        with httpx.Client(follow_redirects=False, timeout=60.0) as client:
            for _ in range(MAX_REDIRECTS + 1):
                with client.stream("GET", target, headers=headers) as resp:
                    if resp.is_redirect:
                        location = resp.headers.get("location")
                        if not location:
                            raise ValueError("Redirect without a Location header.")
                        target = str(httpx.URL(target).join(location))
                        check_url_target(target)  # validate the hop before fetching it
                        continue
                    resp.raise_for_status()
                    content_type = resp.headers.get("content-type", "")
                    content_length = resp.headers.get("content-length")
                    if content_length and int(content_length) > MAX_PDF_BYTES:
                        raise ValueError("Downloaded PDF exceeds the 32 MB limit.")
                    # Stream with a cap so a huge body can't exhaust memory.
                    chunks, size = [], 0
                    for chunk in resp.iter_bytes(1024 * 1024):
                        size += len(chunk)
                        if size > MAX_PDF_BYTES:
                            raise ValueError("Downloaded PDF exceeds the 32 MB limit.")
                        chunks.append(chunk)
                    data = b"".join(chunks)
                    break
            else:
                raise ValueError("Too many redirects.")
    except httpx.HTTPError as e:
        raise ValueError(f"Could not download {target}: {e}") from e

    if "pdf" not in content_type and not data[:5].startswith(b"%PDF-"):
        raise ValueError(
            f"{target} did not return a PDF (content-type: {content_type or 'unknown'}). "
            "Paste an arXiv abs/pdf link or a direct .pdf URL."
        )
    return target, url, data
