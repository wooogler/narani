"""번역본에 원문 표기(인용·참고문헌 링크·캡션·저자)를 재번역 없이 따라 입히기 (§translate/display_sync)."""

from narani.ir import AuthorEntry
from narani.translate import display_sync as ds

def test_relabel_citations_copies_what_the_original_shows():
    ko = "자기주의는 [[1, Vaswani et al. 2017, Transformer](#ref-1); [2, Xie 2024](#ref-2)]에서 왔다. ([Smith 2020](#ref-9))"
    en = "Self-attention [[1](#ref-1), [2](#ref-2)] came from ([Smith 2020](#ref-9))."
    out, n = ds.relabel_citations(ko, en, ["number"])
    assert n == 1 and out.startswith("자기주의는 [[1](#ref-1), [2](#ref-2)]에서")
    assert "([Smith 2020](#ref-9))" in out  # 저자-연도형 링크(묶음 아님)는 그대로
    assert ds.relabel_citations(out, en, ["number"]) == (out, 0)
    en2 = "A [[1, Transformer](#ref-1)] and B [[2, Xie 2024](#ref-2)]."
    back, _ = ds.relabel_citations(out, en2, ["number", "short"])
    assert "[[1, Transformer](#ref-1); [2, Xie 2024](#ref-2)]" in back
    assert ds.relabel_citations(ko, "no groups", ["number"]) == (ko, 0)  # 원문에 없는 번호 → 그대로


def test_restyle_captions_only_labels_known_from_original():
    en = "![Figure 1](images/figure-1.png)\n*Figure 1: A system.*\n"
    ko = ("![Figure 1](images/figure-1.png)\n> **Figure 1:** 시스템 개요.\n\n"
          "> **Figure 9:** 원문에 없는 라벨은 그대로.\n")
    out, n = ds.restyle_captions(ko, en, "italic")
    assert n == 1 and "*Figure 1: 시스템 개요.*" in out and "> **Figure 9:**" in out
    bold, _ = ds.restyle_captions(out, en, "bold-italic")
    assert "**Figure 1:** *시스템 개요.*" in bold
    assert ds.restyle_captions(bold, en, "bold-italic") == (bold, 0)


def test_sync_reference_entries_keeps_translated_entries():
    en = ('<a id="ref-1"></a>**[1]** Vaswani. Attention Is All You Need. 2017\n\n'
          '<a id="ref-2"></a>**[2]** Xie. OSWorld. 2024')
    ko = ('<a id="ref-1"></a>**[1]** Vaswani. [Attention Is All You Need](https://doi.org/x). 2017\n\n'
          '<a id="ref-2"></a>**[2]** 시에. 오에스월드. 2024')  # 2번은 번역됨 → 건드리지 않는다
    out, n = ds.sync_reference_entries(ko, en)
    assert n == 1 and "](https://" not in out.split("\n")[0] and "시에. 오에스월드" in out


def test_sync_author_blocks_keeps_translated_affiliation():
    entries = [AuthorEntry(name="Helena V", emails=["h@x.edu"], affiliations=["Harvard University"])]
    ko = "# 제목\n\n**Helena V**  \nh@x.edu  \n하버드대학교\n\n본문."
    kept: dict = {}
    off, n = ds.sync_author_blocks(ko, entries, ["email"], kept)
    assert n == 1 and "하버드" not in off and kept == {"Helena V": ["하버드대학교"]}
    on, _ = ds.sync_author_blocks(off, entries, ["email", "affiliation"], kept)
    assert on == ko  # 다시 켜면 번역된 소속이 돌아온다
    assert ds.sync_author_blocks(on, entries, ["email", "affiliation"], kept) == (on, 0)
