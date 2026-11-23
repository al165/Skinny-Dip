"""Source loaders. Each loader turns a file into a `Parsed` record: a type, a title and the
text that the chunker will structure. To support a new format, write a loader function and
register its extensions in LOADERS (see 02_Documentation/adding-data.md)."""
import datetime as dt
import functools
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from dipa import config, llm


@dataclass
class Parsed:
    type: str           # text | pdf | html | web | image
    title: str | None   # None = let the chunker take the first heading
    text: str
    source: str | None = None  # original URL, if known


SOURCE_SUFFIX = ".source.txt"


def is_source_file(path: Path) -> bool:
    """A `<file>.source.txt` sidecar: metadata for the file next to it, not a document."""
    return path.name.lower().endswith(SOURCE_SUFFIX)


def _as_url(value) -> str | None:
    value = str(value or "").strip()
    return value if is_url(value) else None


def read_source(path: Path) -> str | None:
    """The original URL of a local file: from `<file>.source.txt` next to it (a bare URL, or a
    `source: <url>` line), else from a markdown file's front matter `source:`. None if neither."""
    side = path.with_name(path.name + SOURCE_SUFFIX)
    if side.is_file():
        for line in side.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            m = re.match(r"^source:\s*(.*)$", line, flags=re.I)
            if url := _as_url(m.group(1) if m else line):
                return url
    if path.suffix.lower() in (".md", ".markdown"):
        import frontmatter
        try:
            return _as_url(frontmatter.loads(path.read_text(encoding="utf-8", errors="replace")).get("source"))
        except Exception:
            return None
    return None


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 16), b""):
            h.update(block)
    return h.hexdigest()


def load_text(path: Path) -> Parsed:
    return Parsed("text", None, path.read_text(encoding="utf-8", errors="replace"))


def load_markdown(path: Path) -> Parsed:
    """Markdown, with any YAML front matter removed from the text (its `title`, if any, is used)."""
    import frontmatter  # python-frontmatter

    raw = path.read_text(encoding="utf-8", errors="replace")
    try:
        post = frontmatter.loads(raw)
    except Exception:  # malformed front matter: index the file as it is
        return Parsed("text", None, raw)
    title = post.metadata.get("title")
    return Parsed("text", str(title).strip() if title else None, post.content)


def _has_text_layer(path: Path) -> bool:
    import pymupdf
    with pymupdf.open(path) as doc:
        return any(page.get_text().strip() for page in doc)


def _is_slide_deck(doc) -> bool:
    """Mostly landscape pages: an exported presentation. Docling's layout model reads each slide
    as one picture and drops its text, so these go to the PyMuPDF loader."""
    wide = sum(1 for page in doc if page.rect.width > page.rect.height * 1.1)
    return wide > len(doc) / 2


@functools.cache
def _pdf_converter(ocr: bool):
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    opts = PdfPipelineOptions(do_ocr=ocr, do_table_structure=True)
    return DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})


def _pdf_docling(path: Path) -> str:
    """PDF -> markdown via Docling: a local layout model finds headings, paragraphs, lists,
    tables and captions in reading order (multi-column pages included), and drops running
    headers/footers and page numbers. Scanned PDFs (no text layer) are OCR'd; OCR is skipped
    otherwise, since it is the slow part. Models download from Hugging Face on first use."""
    result = _pdf_converter(not _has_text_layer(path)).convert(path)
    return result.document.export_to_markdown(image_placeholder="").replace("\ufffd", "")  # icon-font glyphs


def _pdf_pymupdf(doc) -> str:
    """PDF -> markdown from the text layer alone. Each text block becomes a paragraph (wrapped
    lines joined); blocks set clearly larger than the body text become `##` headings; page
    numbers and running headers/footers (the same short text on many pages) are dropped.
    OCR'd pages (invisible text, e.g. from ocrmypdf) have only approximate sizes, so they need a
    much larger size to count as a heading, and Tesseract's "Al" is read back as "AI"."""
    import collections

    pages, ocr = [], []
    sizes = collections.Counter()
    for page in doc:
        trace = page.get_texttrace()
        ocr.append(bool(trace) and sum(t["type"] == 3 for t in trace) > len(trace) / 2)
        blocks = []
        for b in page.get_text("dict", sort=True)["blocks"]:
            lines = []
            for ln in b.get("lines", []):
                spans = [s for s in ln["spans"] if s["text"].strip()]
                if spans:
                    text = "".join(s["text"] for s in ln["spans"]).strip()
                    size = max(s["size"] for s in spans)
                    lines.append((text, size))
                    sizes[round(size)] += len(text)
            if lines:
                blocks.append(lines)
        pages.append(blocks)
    if not sizes:
        return ""
    body = sizes.most_common(1)[0][0]

    def norm(t):
        return re.sub(r"\d+", "#", " ".join(t.split()).lower())

    seen, seen_lines = collections.Counter(), collections.Counter()
    for blocks in pages:
        seen.update({norm(" ".join(t for t, _ in lines)) for lines in blocks})
        seen_lines.update({norm(t) for lines in blocks for t, _ in lines})
    # single lines on many pages: running headers that OCR merged into the block below them
    repeated_lines = {t for t, n in seen_lines.items() if n >= 3 and n >= len(pages) * 0.3 and len(t) < 80}

    # first/last line of a page repeated on 3+ pages, compared on letters only (OCR varies the
    # symbols, e.g. "® ESOTERIC AI ©® INTRODUCTION"): per-section slide headers and footers
    def letters(t):
        return re.sub(r"[^a-z]+", " ", t.lower()).strip()

    def edges(blocks):
        return ((0, 0), (len(blocks) - 1, len(blocks[-1]) - 1)) if blocks else ()

    edge_seen = collections.Counter()
    for blocks in pages:
        edge_seen.update({letters(blocks[b][i][0]) for b, i in edges(blocks)})
    for blocks in pages:
        for b, i in sorted(set(edges(blocks)), reverse=True):
            t = blocks[b][i][0]
            if edge_seen[letters(t)] >= 3 and letters(t) and len(t) < 60:
                del blocks[b][i]
        blocks[:] = [lines for lines in blocks if lines]
    # running headers/footers: short text repeated on many pages (digits ignored, so "Chapter 2"
    # and "Chapter 3" count as one), or very short text repeated a few times (slide labels)
    repeated = {t for t, n in seen.items()
                if n >= 3 and (n >= len(pages) * 0.3 and len(t) < 80 or len(t) < 40)}

    def join(lines):
        text = ""
        for t, _ in lines:
            text = text[:-1] + t if text.endswith("-") and t[:1].islower() else (f"{text} {t}" if text else t)
        return " ".join(text.split())

    out = []
    for blocks, is_ocr in zip(pages, ocr):
        factor = 1.8 if is_ocr else 1.2
        for lines in blocks:
            lines = [ln for ln in lines if norm(ln[0]) not in repeated_lines]
            if is_ocr:
                lines = [(re.sub(r"\bAl\b", "AI", t), sz) for t, sz in lines]
            text = join(lines)
            if not text or re.fullmatch(r"[\d\s/|.-]+", text) or norm(text) in repeated:
                continue  # page numbers, running headers/footers
            if sum(ch.isalnum() for ch in text) < len(text.replace(" ", "")) * 0.5:
                continue  # decorative symbols / text art
            big = [s >= body * factor for _, s in lines]
            if all(big) and len(text) <= 150:
                out.append(f"## {text}")
            elif any(big) and not all(big):  # heading lines followed by body lines in one block
                k = big.index(False)
                head, rest = join(lines[:k]), join(lines[k:])
                # a larger first line that runs on into the body ("The tribe explores how" + "fluidity
                # and ...") is a styled opening, not a heading
                runs_on = rest[:1].islower() or head.endswith((",", "-")) or \
                    re.search(r"\b(and|or|the|a|an|of|to|in|how|that|with|for)$", head, flags=re.I)
                if all(big[:k]) and len(head) <= 150 and not runs_on:
                    out += [f"## {head}", rest]
                else:
                    out.append(text)
            else:
                out.append(text)
    return "\n\n".join(out).replace("\ufffd", "")


def load_pdf(path: Path) -> Parsed:
    """PDF -> markdown with the parser chosen by `DIP_PDF_PARSER` (see config): Docling for
    documents, PyMuPDF for slide decks, low-memory machines, or when Docling loses the text."""
    import pymupdf  # imported lazily: only needed for PDFs

    from dipa.ingest.common import log

    with pymupdf.open(path) as doc:
        title = (doc.metadata or {}).get("title") or None
        layer = sum(len(p.get_text().strip()) for p in doc)
        mode = config.PDF_PARSER
        if mode == "auto":
            mode = "pymupdf" if layer and _is_slide_deck(doc) else "docling"
            if mode == "pymupdf":
                log(f"  {path.name}: slide deck (landscape pages): reading the text layer with PyMuPDF")
        if mode == "pymupdf":
            if not layer:
                raise ValueError(f"{path.name} has no text layer: OCR it (ocrmypdf) or use DIP_PDF_PARSER=docling")
            text = _pdf_pymupdf(doc)
        else:
            log(f"  {path.name}: Docling layout model (~2 s/page on CPU, 2-4 GB RAM)")
            text = _pdf_docling(path)
            if layer and len(text.strip()) < config.PDF_FALLBACK_RATIO * layer and config.PDF_PARSER == "auto":
                log(f"  {path.name}: Docling recovered {len(text.strip())} of ~{layer} characters in the "
                    f"text layer; using PyMuPDF instead")
                text = _pdf_pymupdf(doc)
    if not text.strip():
        raise ValueError(f"no text found in {path.name}")
    return Parsed("pdf", title, text)


def is_url(source) -> bool:
    return isinstance(source, str) and source.startswith(("http://", "https://"))


def source_name(path: str) -> str:
    """The short name of an item's `path`, used as its key in the wiki and in holder ids: a file's
    name, or for a web page a slug of host + path ("https://www.example.com/blog/post/" ->
    "example-com-blog-post"; no dots, so Path(name).stem keeps all of it)."""
    if not is_url(path):
        return path.rsplit("/", 1)[-1]
    from urllib.parse import urlsplit
    u = urlsplit(path)
    raw = u.netloc.removeprefix("www.") + u.path + (f"-{u.query}" if u.query else "")
    return re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")[:120] or "web-page"


def _extract_html(html: str, url: str | None = None):
    """Main text of an HTML page as markdown, extracted with trafilatura (navigation, ads,
    comments and footers removed, headings, lists and tables kept), plus its metadata."""
    import trafilatura  # imported lazily: only needed for web pages

    text = trafilatura.extract(html, url=url, output_format="markdown", include_formatting=True,
                               include_links=False, include_tables=True, include_comments=False)
    return text, trafilatura.extract_metadata(html, default_url=url)


def load_html(path: Path) -> Parsed:
    """A saved web page or other local HTML file, extracted like a web article."""
    text, meta = _extract_html(path.read_text(encoding="utf-8", errors="replace"))
    if not text:
        raise ValueError(f"no main text found in {path.name}")
    return Parsed("html", (meta.title if meta else None) or None, text)


def load_url(url: str) -> Parsed:
    """Web article -> markdown. The page is fetched once and its main text extracted locally
    (see `_extract_html`). A copy is saved to data/sources/ so the indexed text can always be
    read back, even if the page changes or disappears."""
    import trafilatura

    html = trafilatura.fetch_url(url)
    if not html:
        raise ValueError(f"could not fetch {url}")
    text, meta = _extract_html(html, url)
    if not text or len(text) < 200:
        raise ValueError(f"no article text found at {url} (paywall, JS-only page or not an article?)")
    title = (meta.title if meta else None) or url

    config.SOURCES_DIR.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60] or "article"
    header = (f"---\nsource: {url}\ntitle: {title}\nauthor: {getattr(meta, 'author', '') or ''}\n"
              f"date: {getattr(meta, 'date', '') or ''}\nfetched: {dt.date.today()}\n---\n\n")
    (config.SOURCES_DIR / f"{slug}.md").write_text(header + text, encoding="utf-8")
    return Parsed("web", title, text, source=url)


IMAGE_PROMPT = (
    "Describe this image for a research archive, as JSON: `title` (a short title), "
    "`description` (a paragraph describing what is shown) and `text` (any visible text, "
    "transcribed verbatim; empty if none)."
)
IMAGE_SCHEMA = {
    "type": "object",
    "properties": {"title": {"type": "string"}, "description": {"type": "string"}, "text": {"type": "string"}},
    "required": ["title", "description", "text"],
}


def load_image(path: Path) -> Parsed:
    """Images are indexed through a local vision-language model: its description (+ any OCR'd
    text) becomes the item's text, so images take part in every query type."""
    out = llm.generate(IMAGE_PROMPT, schema=IMAGE_SCHEMA, images=[path.read_bytes()], model=config.VLM_MODEL)
    body = out["description"].strip()
    if out["text"].strip():
        body += "\n\nText in image: " + out["text"].strip()
    return Parsed("image", out["title"].strip() or path.stem, body)


LOADERS = {
    ".md": load_markdown,
    ".markdown": load_markdown,
    ".txt": load_text,
    ".pdf": load_pdf,
    ".html": load_html,
    ".htm": load_html,
    ".png": load_image,
    ".jpg": load_image,
    ".jpeg": load_image,
    ".webp": load_image,
}


def load(path: Path) -> Parsed:
    loader = LOADERS.get(path.suffix.lower())
    if loader is None:
        raise ValueError(f"No loader for {path.suffix!r} ({path.name}). Supported: {sorted(LOADERS)}")
    return loader(path)
