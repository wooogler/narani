"""원문 단어 수 — 인용·마크다운 문법 빼기, 섹션 나누기·종류 정하기 (§wordcount)."""

from narani import wordcount as wcm
from narani.wordcount import ELEMENTS, KINDS, count_words

ALL_E = frozenset(ELEMENTS)
ALL_K = frozenset(KINDS)


def _n(md: str, **kw) -> int:
    return count_words(md).total(kinds=kw.get("kinds", ALL_K), elements=kw.get("elements", ALL_E))


def test_citations_are_not_words():
    linked = "Agents help [[3, America 2024, Erica](#ref-3); [9, Chilimbi et al. 2024](#ref-9)] people."
    assert _n(linked) == 3
    assert _n("Prior work [12] and [3–5] and [3, 7] shows it.") == 6
    assert _n("Memory matters (Smith et al., 2020; Lee & Kim 2019a, p. 5) a lot.") == 4
    assert _n("As Hall (1984) and Huber (1991, 1993) said.") == 5
    assert _n("Shown in [Xie et al. 2024] here.") == 3
    # 연도 없는 괄호는 인용이 아니다
    assert _n("The tool (a plugin) works.") == 5


def test_markdown_syntax_is_not_words():
    assert _n("**Bold** and *it* `code` ~~gone~~") == 5
    assert _n("See [the docs](https://example.com/a/b) now.") == 4
    assert _n("![Figure 1](images/figure-1.png)") == 0
    assert _n('<a id="ref-1"></a>**[1]** Ann Lee. A title. 2020') == 5
    assert _n("Our code is public. <sup class=\"md-fn\"><a href=\"#fn-1\">1</a></sup>") == 4
    assert _n("A dash — and arrow → and dot · stay out.") == 8
    assert _n("- item one\n1. item two\n> quoted line") == 6
    assert _n("well-known state-of-the-art") == 2


def test_math_inline_counts_one_display_none():
    md = "We had $N=33$ people and $\\alpha$ too.\n\n$$\nx = y + z\n$$\n\n$$a=b$$\n\nPaid $30 each."
    wc = count_words(md)
    assert wc.by_element(ALL_K)["math"] == 2
    assert wc.total(ALL_K, ALL_E - {"math"}) == 5 + 3  # 'We had people and too.' + 'Paid $30 each.'


def test_tables_captions_and_elements():
    md = ("# 1 Intro\n\nBody text here.\n\n"
          "![Figure 1](images/f.png)\n> **Figure 1:** A system overview.\n\n"
          "**Table 1:** *Two groups.*\n\n| Group | Round 1 |\n|---|---|\n| A | T1 · BT |\n")
    by = count_words(md).by_element(ALL_K)
    assert by["heading"] == 2 and by["text"] == 3  # 섹션 번호도 한 단어
    assert by["caption"] == 5 + 4  # 'Figure 1: A system overview.' + 'Table 1: Two groups.'
    assert by["table"] == 3 + 3     # 구분선은 0, '·'도 0


def test_sections_match_toc_order_and_kinds():
    md = ("# A Great Paper\n\n**Ann Lee**  \nSomewhere U\n\n# Abstract\n\nWe study X.\n\n*Keywords*\n\nx, y\n\n"
          "# 1 Introduction\n\nIntro words.\n\n```\n# not a heading\n```\n\n## 1.1 Sub\n\nMore.\n\n"
          "# Acknowledgments\n\nThanks all.\n\n# Funding\n\nGrant 1.\n\n# References\n\n"
          '<a id="ref-1"></a>**[1]** A. B. Title. 2020\n\n'
          "# A Appendix\n\n## A.1 Prompts\n\nPrompt text.\n\n## Background.\n\nBg.\n")
    wc = count_words(md)
    secs = [s for s in wc.sections if s.index is not None]
    assert [s.index for s in secs] == list(range(len(secs)))
    assert [(s.title, s.kind) for s in secs] == [
        ("A Great Paper", "front"), ("Abstract", "abstract"), ("1 Introduction", "body"), ("1.1 Sub", "body"),
        ("Acknowledgments", "ack"), ("Funding", "statements"), ("References", "references"),
        ("A Appendix", "appendix"), ("A.1 Prompts", "appendix"), ("Background.", "appendix")]
    kinds = wc.by_kind(ALL_E)
    assert kinds["meta"] == 3            # '*Keywords*' 줄부터 초록 섹션 끝까지
    assert kinds["abstract"] == 1 + 3    # 제목 + 'We study X.'
    assert wc.by_kind(ALL_E)["references"] == 1 + 4  # 참고문헌 번호는 빼고
    # 기본 범위 = 초록 + 본문 + 감사의 글 (코드 블록 빼고)
    assert wc.total() == 4 + (2 + 2 + 2 + 1) + (1 + 2)


def test_unknown_sections_after_references_or_appendix_divider_are_appendix():
    md = "# Intro\n\nx\n\n# APPENDIX\n\n# 8 Literature Review\n\n## 8.3 Keywords\n\ny\n"
    assert [s.kind for s in count_words(md).sections if s.index is not None] == [
        "front", "appendix", "appendix", "appendix"]
    md2 = "# T\n\n# Abstract\n\na\n\n# 1 Intro\n\nb\n\n# References\n\nr\n\n# Study Materials\n\nz\n"
    assert count_words(md2).sections[-1].kind == "appendix"


def test_front_without_abstract_keeps_long_paragraphs_as_body():
    para = " ".join(["word"] * 30)
    md = f"# Old Paper\n\n**JAMES WALSH**  \nDartmouth College\n\n{para}\n\n## Next Part\n\nMore text.\n"
    wc = count_words(md)
    assert wc.sections[0].kind == "front" and wc.sections[1].kind == "body"  # 빈 서문은 없다
    assert wc.by_kind(ALL_E)["body"] == 30 + 2 + 2
    assert wc.by_kind(ALL_E)["front"] == 2 + 2 + 2


def test_teaser_figure_caption_in_title_block_counts_as_body():
    md = "# Title\n\nAnn\n\n![Figure 1](f.png)\n> **Figure 1:** Teaser.\n\n# Abstract\n\nA b.\n"
    assert count_words(md).by_kind(ALL_E)["body"] == 3


def test_footnotes_move_to_the_section_that_cites_them():
    md = ('# Abstract\n\nA.\n\n# 1 Intro\n\nSee code.<sup class="md-fn"><a href="#fn-1">1</a></sup>\n\n'
          '# References\n\n# Footnotes\n\n<a id="fn-1"></a>**1.** https://github.com/x/y and more\n')
    wc = count_words(md)
    intro = next(s for s in wc.sections if s.title == "1 Intro")
    assert intro.counts[("body", "footnote")] == 3
    assert wc.total() == 1 + 1 + 2 + 2 + 3
    assert wc.total(elements=wcm.DEFAULT_ELEMENTS - {"footnote"}) == 1 + 1 + 2 + 2


def test_section_total_ignores_kind_filter_by_default():
    wc = count_words("# References\n\n<a id=\"ref-1\"></a>**[1]** Ann. T. 2020\n")
    refs = wc.sections[-1]
    assert refs.total() == 1 + 3 and refs.total(kinds=wcm.DEFAULT_KINDS) == 0


def test_sections_line_up_with_viewer_toc():
    """목차(§ui.app — _split_sections + _heading_of)와 같은 헤더를 같은 순서로."""
    from pathlib import Path

    from narani.ui.app import _heading_of, _split_sections

    for p in sorted((Path(__file__).parent / "corpus").glob("*.md")):
        md = p.read_text(encoding="utf-8") + "\n```\n# fenced\n```\n## Last\n\nx\n"
        toc = [h[1] for h in map(_heading_of, _split_sections(md)) if h]
        ours = [s.title for s in count_words(md).sections if s.index is not None]
        assert ours == toc, p.name
