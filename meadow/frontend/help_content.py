"""Pure helpers for the in-app user handbook served at `/help`.

The handbook is **Markdown in the repository** (`docs/user/<language>/`, one file
per chapter) and is rendered here. That keeps one source of truth: the files stay
reviewable in Git, and no second toolchain or build artefact is needed. Rendering
happens once per process (`catalog` is cached), so a page build only reads memory.

Layout contract
---------------
- `docs/user/<language>/<NN>-<slug>.md` — `NN` orders the chapters, `<slug>` is the
  **page id** used in URLs (`/help?page=<slug>`).
- The first `#` heading is the chapter title; every other heading becomes a
  linkable section (`/help?page=<slug>#<anchor>`).
- Anchors are the ones the Markdown renderer generates (`toc` extension) — the
  renderer stays the single source of truth, `tests/test_help.py` asserts that.
- Fenced code blocks are rendered but **excluded from the search index**: they are
  samples, not prose, and would otherwise drown the prose matches.

The module deliberately imports no NiceGUI state (like `display.py`), so every
rule here is unit-testable without a server slot.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from urllib.parse import quote

import markdown
from markdown.extensions.toc import slugify

#: Handbook root (`docs/user/`); one subfolder per language.
DOCS_ROOT = Path(__file__).resolve().parents[2] / "docs" / "user"
#: Language used when the UI locale has no handbook folder of its own.
FALLBACK_LANGUAGE = "en"

#: Markdown extensions shared by every render (heading ids come from `toc`).
MARKDOWN_EXTENSIONS = ("toc", "tables", "fenced_code", "attr_list", "sane_lists")

_ORDER_PREFIX = re.compile(r"^(?P<position>\d+)[-_](?P<slug>.+)$")
#: Handbook folders are language codes (`de`, `en`, `pt-BR`). Anything else —
#: notably the `assets/` folder with the chapter images — is not a language.
_LANGUAGE_FOLDER = re.compile(r"^[a-z]{2}(?:-[a-z0-9]{2,8})?$")
_HEADING = re.compile(r"^(?P<hashes>#{1,6})\s+(?P<title>.+?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(?:```|~~~)")
_LIST_MARKER = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_HTML_TAG = re.compile(r"<[^>]+>")
_BOLD = re.compile(r"\*\*|__|~~")
_WHITESPACE = re.compile(r"\s+")

#: Umlaut folding so "uber" finds "Über" and "kategorien" finds "Kategorien".
_FOLD = str.maketrans({"ä": "a", "ö": "o", "ü": "u", "ß": "ss", "Ä": "a", "Ö": "o", "Ü": "u"})


@dataclass(frozen=True)
class Section:
    """One heading of a chapter (level 0 = the text before the first heading)."""

    level: int
    title: str
    anchor: str
    text: str

    @property
    def is_linkable(self) -> bool:
        """Whether the section has its own anchor (the intro has none)."""
        return bool(self.anchor)


@dataclass(frozen=True)
class Page:
    """One rendered handbook chapter."""

    id: str
    language: str
    title: str
    position: int
    sections: tuple[Section, ...]
    html: str

    @property
    def intro(self) -> str:
        """Plain text before the first heading (used as the list tooltip)."""
        return self.sections[0].text if self.sections and self.sections[0].level == 0 else ""


@dataclass(frozen=True)
class Hit:
    """One search result: a section of a chapter."""

    page_id: str
    page_title: str
    anchor: str
    heading: str
    snippet: str
    score: int
    matched: int

    @property
    def label(self) -> str:
        """`Chapter — section` (without "— section" when the section is the title)."""
        if not self.heading or self.heading == self.page_title:
            return self.page_title
        return f"{self.page_title} — {self.heading}"


def page_id(path: Path) -> str:
    """Returns the URL id of a handbook file (`03-produktformular.md` -> `produktformular`)."""
    stem = path.stem
    match = _ORDER_PREFIX.match(stem)
    return (match.group("slug") if match else stem).strip().lower()


def page_position(path: Path) -> int:
    """Returns the sort position of a handbook file (the numeric prefix, else 999)."""
    match = _ORDER_PREFIX.match(path.stem)
    return int(match.group("position")) if match else 999


def resolve_language(locale: str | None, available: list[str]) -> str:
    """Maps a UI locale to a handbook language (`de-AT` -> `de`, else the fallback)."""
    base = (locale or "").split("-")[0].strip().lower()
    if base in available:
        return base
    if FALLBACK_LANGUAGE in available:
        return FALLBACK_LANGUAGE
    return available[0] if available else FALLBACK_LANGUAGE


def available_languages(root: Path | None = None) -> list[str]:
    """Returns the languages that have at least one chapter, sorted."""
    base = root or DOCS_ROOT
    if not base.is_dir():
        return []
    return sorted(
        child.name
        for child in base.iterdir()
        if child.is_dir() and _LANGUAGE_FOLDER.match(child.name) and any(child.glob("*.md"))
    )


def page_files(language: str, root: Path | None = None) -> list[Path]:
    """Returns the chapter files of a language in reading order."""
    base = (root or DOCS_ROOT) / language
    if not base.is_dir():
        return []
    return sorted(base.glob("*.md"), key=lambda path: (page_position(path), path.name))


def plain_text(markdown_text: str) -> str:
    """Reduces Markdown to searchable prose (code blocks and markup removed)."""
    lines: list[str] = []
    in_fence = False
    for line in markdown_text.splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        lines.append(line)
    text = "\n".join(lines)
    text = _IMAGE.sub(r"\1", text)
    text = _LINK.sub(r"\1", text)
    text = _HTML_TAG.sub(" ", text)
    text = _LIST_MARKER.sub("", text)
    text = _BOLD.sub("", text)
    text = "\n".join(line.lstrip("#").strip() for line in text.splitlines())
    return _WHITESPACE.sub(" ", text).strip()


def split_sections(markdown_text: str) -> list[tuple[int, str, str]]:
    """Splits raw Markdown into `(level, title, plain body text)` per heading.

    A leading section with level 0 carries the text before the first heading.
    Headings inside fenced code blocks are ignored.
    """
    raw: list[list[str]] = [[]]  # body lines of the first (intro) section
    headings: list[tuple[int, str]] = [(0, "")]
    in_fence = False
    for line in markdown_text.splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
        elif not in_fence and (match := _HEADING.match(line)):
            headings.append((len(match.group("hashes")), match.group("title").strip()))
            raw.append([])
            continue
        raw[-1].append(line)
    sections: list[tuple[int, str, str]] = []
    for (level, title), body in zip(headings, raw, strict=True):
        text = plain_text("\n".join(body))
        if level == 0 and not text:
            continue
        sections.append((level, title, text))
    return sections


def _flatten_toc(tokens: list[dict], level: int = 0) -> list[tuple[int, str, str]]:
    """Flattens the `toc` token tree into `(level, title, anchor)` in document order."""
    flat: list[tuple[int, str, str]] = []
    for token in tokens:
        flat.append(
            (
                int(token.get("level", level)),
                str(token.get("name", "")),
                str(token.get("id", "")),
            )
        )
        flat.extend(_flatten_toc(token.get("children") or [], level))
    return flat


def _unique(base: str, used: dict[str, int]) -> str:
    """Mirrors the renderer's duplicate-heading scheme (`topic`, `topic_1`, ...)."""
    count = used.get(base, 0)
    used[base] = count + 1
    return base if count == 0 else f"{base}_{count}"


def render(markdown_text: str) -> tuple[str, list[dict]]:
    """Renders Markdown to HTML and returns the `toc` token tree alongside it."""
    md = markdown.Markdown(extensions=list(MARKDOWN_EXTENSIONS))
    html = md.convert(markdown_text)
    return html, list(getattr(md, "toc_tokens", []))


def sections_with_anchors(
    raw_sections: list[tuple[int, str, str]], toc_tokens: list[dict]
) -> tuple[Section, ...]:
    """Pairs the raw sections with the anchors the renderer produced.

    The renderer stays authoritative for the ids; the slug fallback only covers a
    document whose headings the raw scan sees differently (defensive, and asserted
    against the real handbook in the tests).
    """
    flat = _flatten_toc(toc_tokens)
    used: dict[str, int] = {}
    sections: list[Section] = []
    index = 0
    for level, title, body in raw_sections:
        anchor = ""
        if level and index < len(flat):
            flat_level, flat_title, flat_id = flat[index]
            if flat_level == level and flat_title == title:
                anchor = flat_id
                index += 1
        if level and not anchor:
            anchor = _unique(slugify(title, "-"), used)
        sections.append(Section(level=level, title=title, anchor=anchor, text=body))
    return tuple(sections)


def load_page(path: Path, language: str) -> Page:
    """Reads and renders one handbook chapter."""
    text = path.read_text(encoding="utf-8")
    html, toc_tokens = render(text)
    sections = sections_with_anchors(split_sections(text), toc_tokens)
    title = next(
        (section.title for section in sections if section.level == 1),
        page_id(path).replace("-", " ").title(),
    )
    return Page(
        id=page_id(path),
        language=language,
        title=title,
        position=page_position(path),
        sections=sections,
        html=html,
    )


def load_pages(language: str, root: Path | None = None) -> tuple[Page, ...]:
    """Returns the chapters of a language in reading order (uncached)."""
    return tuple(load_page(path, language) for path in page_files(language, root))


def catalog(root: Path | None = None) -> dict[str, tuple[Page, ...]]:
    """Returns every handbook language with its chapters (cached per root)."""
    return _catalog(str(root or DOCS_ROOT))


def clear_cache() -> None:
    """Drops the cached handbook (used by the tests and after a content change)."""
    _catalog.cache_clear()


@cache
def _catalog(root: str) -> dict[str, tuple[Page, ...]]:
    base = Path(root)
    return {language: load_pages(language, base) for language in available_languages(base)}


def terms_of(query: str) -> list[str]:
    """Splits a query into folded search terms (single characters are ignored)."""
    folded = _fold(query.lower())
    terms: list[str] = []
    for token in folded.split():
        cleaned = token.strip(".,;:!?()[]{}\"'`")
        if len(cleaned) >= 2 and cleaned not in terms:
            terms.append(cleaned)
    return terms


def _fold(text: str) -> str:
    return text.lower().translate(_FOLD)


def _snippet(text: str, terms: list[str], width: int = 110) -> str:
    """Returns a readable excerpt around the first matching term."""
    folded = _fold(text)
    position = -1
    for term in terms:
        found = folded.find(term)
        if found >= 0 and (position < 0 or found < position):
            position = found
    if position < 0:
        return text[:width] + ("…" if len(text) > width else "")
    start = max(0, position - width // 3)
    end = min(len(text), start + width)
    excerpt = text[start:end].strip()
    return ("…" if start > 0 else "") + excerpt + ("…" if end < len(text) else "")


def search(pages: tuple[Page, ...] | list[Page], query: str, limit: int = 20) -> list[Hit]:
    """Searches the chapters of one language and returns the best matching sections.

    Ranking: number of distinct query terms matched (descending), then the weighted
    score (chapter title > heading > body text), then reading order.
    """
    terms = terms_of(query)
    if not terms:
        return []
    ranked: list[tuple[int, int, int, int, Hit]] = []
    for page in pages:
        folded_title = _fold(page.title)
        for index, section in enumerate(page.sections):
            folded_heading = _fold(section.title)
            folded_text = _fold(section.text)
            matched = 0
            score = 0
            for term in terms:
                in_title = term in folded_title
                in_heading = term in folded_heading
                # Count the term once per position class, so one long paragraph
                # cannot outrank a section that names the term in its heading.
                occurrences = min(folded_text.count(term), 3)
                if in_title or in_heading or occurrences:
                    matched += 1
                if in_title:
                    score += 6
                if in_heading:
                    score += 4
                score += occurrences
            if not matched:
                continue
            hit = Hit(
                page_id=page.id,
                page_title=page.title,
                anchor=section.anchor,
                heading=section.title,
                snippet=_snippet(section.text, terms),
                score=score,
                matched=matched,
            )
            ranked.append((-matched, -score, page.position, index, hit))
    # Sort on the numeric ranks only (a `Hit` is not orderable by design).
    ranked.sort(key=lambda item: item[:4])
    return [item[-1] for item in ranked[:limit]]


def help_target(page_id_value: str, anchor: str = "") -> str:
    """Builds the in-app link of a chapter (and optionally one of its sections).

    The section is a **query parameter**, not a `#fragment`: NiceGUI builds the
    page client-side, so the browser cannot jump to an element id on its own
    (`help_page._scroll_to` does the scrolling once the DOM exists).
    """
    target = f"/help?page={quote(page_id_value)}"
    return f"{target}&anchor={quote(anchor)}" if anchor else target


def find_page(pages: tuple[Page, ...] | list[Page], page_id_value: str) -> Page | None:
    """Returns the chapter with that id, or `None`."""
    return next((page for page in pages if page.id == page_id_value), None)


def chapter_title(page_id_value: str, language: str, root: Path | None = None) -> str:
    """Returns the chapter title in that language ("" when the chapter is unknown).

    Used by the info buttons: the tooltip names the chapter it links to, so a
    wrong id is visible in the UI instead of failing silently.
    """
    page = find_page(catalog(root).get(language, ()), page_id_value)
    return page.title if page else ""
