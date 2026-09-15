"""Tests for `meadow.frontend.help_content` (the in-app user handbook).

Two groups:

- **Pure rules** — page ids, ordering, language resolution, search ranking and
  snippets, checked on synthetic data so the assertions do not depend on the
  wording of the handbook.
- **Content invariants** — checked against the real `docs/user/` files: the
  anchors the search produces must exist in the rendered HTML, both languages
  must expose the same page ids (a `/help?page=…` link is language-independent)
  and every cross-reference inside the handbook must point at a real chapter.
"""

from __future__ import annotations

from pathlib import Path

from meadow.frontend.help_content import (
    Page,
    Section,
    available_languages,
    catalog,
    clear_cache,
    help_target,
    page_id,
    page_position,
    plain_text,
    resolve_language,
    search,
    split_sections,
)


def _page(page_id_value: str, title: str, position: int, sections: list[Section]) -> Page:
    return Page(
        id=page_id_value,
        language="de",
        title=title,
        position=position,
        sections=tuple(sections),
        html="",
    )


def test_page_id_and_position_come_from_the_filename() -> None:
    assert page_id(Path("03-product-form.md")) == "product-form"
    assert page_position(Path("03-product-form.md")) == 3
    # A file without a numeric prefix is still usable, it just sorts last.
    assert page_id(Path("Anhang.md")) == "anhang"
    assert page_position(Path("Anhang.md")) == 999


def test_the_language_falls_back_when_the_locale_has_no_handbook() -> None:
    assert resolve_language("de", ["de", "en"]) == "de"
    assert resolve_language("de-AT", ["de", "en"]) == "de"
    assert resolve_language("fr", ["de", "en"]) == "en"
    assert resolve_language(None, ["de", "en"]) == "en"
    # No available language at all must not raise.
    assert resolve_language("de", []) == "en"


def test_plain_text_drops_code_blocks_and_markup() -> None:
    text = plain_text(
        "# Titel\n\nEin **wichtiges** Merkmal aus `rel_category_member`.\n\n"
        "```python\nrun(obj, ctx, result)\n```\n\n- Punkt mit [Link](/help?page=import)\n"
    )
    assert "wichtiges Merkmal" in text
    assert "rel_category_member" in text
    assert "run(obj" not in text  # inside a fence -> not indexed
    assert "[" not in text and "#" not in text


def test_split_sections_ignores_headings_inside_code_blocks() -> None:
    sections = split_sections(
        "# Titel\n\nText.\n\n```\n# kein Titel\n```\n\n## Abschnitt\n\nMehr.\n"
    )
    assert [(level, title) for level, title, _ in sections] == [
        (1, "Titel"),
        (2, "Abschnitt"),
    ]


def test_search_ranks_a_heading_match_above_a_body_match() -> None:
    pages = [
        _page(
            "a",
            "Kapitel A",
            1,
            [Section(2, "Allgemein", "allgemein", "Die Vererbung wird hier nur erwähnt.")],
        ),
        _page("b", "Kapitel B", 2, [Section(2, "Vererbung", "vererbung", "Kurzer Text.")]),
    ]
    hits = search(pages, "vererbung")
    assert [hit.page_id for hit in hits] == ["b", "a"]
    assert hits[0].anchor == "vererbung"
    assert hits[0].heading == "Vererbung"


def test_search_ranks_by_the_number_of_matching_terms_and_folds_umlauts() -> None:
    pages = [
        _page("a", "Kapitel", 1, [Section(2, "Übersicht", "ubersicht", "Ein Überblick.")]),
        _page("b", "Kapitel", 2, [Section(2, "Anderes", "anderes", "Ein Überblick und Details.")]),
    ]
    # "uber" must find "Über" (folding); both chapters match, the first in reading order wins.
    assert [hit.page_id for hit in search(pages, "uber")] == ["a", "b"]
    # A second term lifts the chapter that carries both; partial matches stay listed.
    hits = search(pages, "uber details")
    assert [hit.page_id for hit in hits] == ["b", "a"]
    assert [hit.matched for hit in hits] == [2, 1]
    # A single character is not a search term.
    assert search(pages, "a") == []


def test_a_hit_carries_a_readable_snippet() -> None:
    text = "Viel Text davor. " * 8 + "Hier steht Vererbung im Satz. " + "Viel Text danach. " * 8
    pages = [_page("a", "Kapitel", 1, [Section(2, "Abschnitt", "abschnitt", text)])]
    hit = search(pages, "vererbung")[0]
    assert "Vererbung" in hit.snippet
    assert hit.snippet.startswith("…")
    assert len(hit.snippet) < 130


def test_help_target_builds_an_in_app_link() -> None:
    assert help_target("product-form") == "/help?page=product-form"
    # The section is a query parameter: the browser cannot jump to an id in a
    # client-side rendered page on its own.
    assert help_target("product-form", "vererbung") == "/help?page=product-form&anchor=vererbung"


# --- invariants of the real handbook ------------------------------------------


def test_the_handbook_ships_german_and_english() -> None:
    languages = available_languages()
    assert "de" in languages
    assert "en" in languages


def test_both_languages_expose_the_same_chapters_in_the_same_order() -> None:
    """Otherwise a `/help?page=…` link from a tooltip would break per language."""
    clear_cache()
    pages = catalog()
    ids = {language: [page.id for page in chapters] for language, chapters in pages.items()}
    reference = ids["de"]
    assert reference, "the German handbook must not be empty"
    for language, page_ids in ids.items():
        assert page_ids == reference, f"{language} has different chapters: {page_ids}"


def test_every_chapter_has_a_title_and_linkable_sections() -> None:
    clear_cache()
    for language, chapters in catalog().items():
        for page in chapters:
            assert page.title, f"{language}/{page.id} has no title"
            assert any(section.level == 2 for section in page.sections), (
                f"{language}/{page.id} has no sections"
            )


def test_every_search_anchor_exists_in_the_rendered_html() -> None:
    """The search index and the renderer must agree on the heading ids."""
    clear_cache()
    for language, chapters in catalog().items():
        for page in chapters:
            for section in page.sections:
                if not section.is_linkable:
                    continue
                marker = f'id="{section.anchor}"'
                assert page.html.count(marker) == 1, (
                    f"{language}/{page.id}: anchor {section.anchor!r} appears "
                    f"{page.html.count(marker)}x in the HTML"
                )


def test_every_cross_reference_points_at_a_real_chapter() -> None:
    clear_cache()
    pages = catalog()
    known = {page.id for page in pages["de"]}
    for language, chapters in pages.items():
        for page in chapters:
            for target in _help_targets(page.html):
                assert target in known, f"{language}/{page.id} links to unknown chapter {target!r}"


def test_the_handbook_is_searchable_per_language() -> None:
    clear_cache()
    pages = catalog()
    hits = search(pages["de"], "Massenaktionen")
    assert hits and hits[0].page_id == "product-grid"
    hits_en = search(pages["en"], "bulk actions")
    assert hits_en and hits_en[0].page_id == "product-grid"
    assert search(pages["de"], "") == []


def _help_targets(html: str) -> list[str]:
    """Returns the page ids referenced by `/help?page=…` links in rendered HTML."""
    import re

    return re.findall(r"/help\?page=([A-Za-z0-9._-]+)", html)
