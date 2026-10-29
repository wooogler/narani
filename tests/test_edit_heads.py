"""편집기에서 고친 헤더가 구조로 옮겨지는지 — 추출이 놓친 제목을 `#`로 세우면 섹션이 된다 (§structure/edit_heads)."""

import pytest

from narani import pipeline, relayout
from narani.review import manifest as manifest_io
from narani.structure import edit_heads
from narani.ui.controller import UIController
from narani.workdir import WorkDir

SOURCE = """# A Study of Things

## 1 Introduction

Intro body sentence.

Related Systems

Systems body paragraph about prior tools.

Design goals. We aim to keep the editor simple and the structure honest.

## 2 Method

Method body.

## 3 Results and Discussion

Results body.
"""


@pytest.fixture
def ctrl(tmp_path):
    src = tmp_path / "paper.md"
    src.write_text(SOURCE, encoding="utf-8")
    wd = WorkDir(tmp_path / "paper.md4")
    pipeline.convert(src, wd)
    return UIController(wd)


def _edit(ctrl, old: str, new: str) -> tuple[dict | None, str]:
    """편집기 저장과 같은 순서 — en.md를 쓰고, 저장 전 글로 헤더 변화를 구조에 옮긴다."""
    before = ctrl.en_markdown()
    assert old in before
    mine = before.replace(old, new, 1)
    ctrl.save_en_markdown(mine)
    return ctrl.follow_edit_headings(before), mine


def _sec(ctrl, text: str):
    return next((s for s in ctrl.manifest.sections if s.text == text), None)


def test_body_line_becomes_section(ctrl):
    assert _sec(ctrl, "Related Systems") is None
    r, mine = _edit(ctrl, "\nRelated Systems\n", "\n## Related Systems\n")
    assert r["added"] == 1 and r["orphans"] == [] and r["text"] is None  # 편집기 글을 바꿀 필요 없다
    sec = _sec(ctrl, "Related Systems")
    assert sec.added and sec.level == 2
    en = ctrl.en_markdown()
    assert en == mine and en.count("## Related Systems") == 1
    raw = ctrl.wd.raw_md.read_text(encoding="utf-8").splitlines()
    assert raw[sec.line] == "## Related Systems"
    # 섹션 맵(목차 짝·번역 단위)이 새 섹션의 줄을 가리킨다
    e = next(e for e in ctrl.section_map() if e["id"] == sec.id)
    assert en.splitlines()[e["out_line"]] == "## Related Systems"
    # 다시 열어도 그대로 — 줄 앵커는 blocks.json에서 id로 다시 읽는다
    again = UIController(ctrl.wd)
    s2 = next(s for s in again.manifest.sections if s.id == sec.id)
    assert s2.added and s2.line == sec.line
    # 원래 섹션의 순서 — 1 Introduction 다음, 2 Method 앞
    texts = [s.text for s in again.manifest.sections]
    assert texts.index("1 Introduction") < texts.index("Related Systems") < texts.index("2 Method")


def test_paragraph_prefix_splits_into_heading_and_body(ctrl):
    line = "Design goals. We aim to keep the editor simple and the structure honest."
    r, mine = _edit(ctrl, line, "### Design goals\n\nWe aim to keep the editor simple and the structure honest.")
    assert r["added"] == 1 and r["text"] is None
    sec = _sec(ctrl, "Design goals")
    raw = ctrl.wd.raw_md.read_text(encoding="utf-8").splitlines()
    assert raw[sec.line:sec.line + 3] == ["### Design goals", "",
                                          "We aim to keep the editor simple and the structure honest."]
    # 아래 섹션의 줄 앵커가 늘어난 두 줄만큼 밀렸다 — 안 밀면 '2 Method'가 엉뚱한 줄에 찍힌다
    method = _sec(ctrl, "2 Method")
    assert raw[method.line] == "## 2 Method"
    assert ctrl.en_markdown() == mine


def test_split_without_blank_line_keeps_users_layout(ctrl):
    line = "Design goals. We aim to keep the editor simple and the structure honest."
    r, mine = _edit(ctrl, line, "### Design goals\nWe aim to keep the editor simple and the structure honest.")
    assert r["added"] == 1 and ctrl.en_markdown() == mine


def test_level_change_then_back_to_body_then_italic(ctrl):
    r, _ = _edit(ctrl, "# 2 Method", "### 2 Method")  # 번호 규칙이 '2 Method'를 H1로 잡았다
    assert r["levels"] == 1 and _sec(ctrl, "2 Method").level == 3
    r, mine = _edit(ctrl, "### 2 Method", "2 Method")
    assert _sec(ctrl, "2 Method").level == "skip"
    assert "# 2 Method" not in ctrl.en_markdown() and ctrl.en_markdown() == mine
    r, _ = _edit(ctrl, "\n2 Method\n", "\n## 2 Method\n")  # 본문으로 둔 섹션을 다시 제목으로
    assert _sec(ctrl, "2 Method").level == 2
    r, _ = _edit(ctrl, "## 2 Method", "*2 Method*")
    assert _sec(ctrl, "2 Method").level == "italic"


def test_body_edit_in_same_save_survives(ctrl):
    before = ctrl.en_markdown()
    mine = before.replace("\nRelated Systems\n", "\n## Related Systems\n").replace(
        "Method body.", "Method body, edited by hand.")
    ctrl.save_en_markdown(mine)
    r = ctrl.follow_edit_headings(before)
    assert r["added"] == 1
    en = ctrl.en_markdown()
    assert "Method body, edited by hand." in en and en.count("## Related Systems") == 1
    assert ctrl.has_manual_edit()  # 손으로 고친 본문이 남았으니 표시도 남는다


def test_reassembly_after_edit_has_no_duplicate_heading(ctrl):
    _edit(ctrl, "\nRelated Systems\n", "\n## Related Systems\n")
    _edit(ctrl, "Method body.", "Method body, edited.")  # 본문만 — 헤더 모양 그대로라 구조는 손대지 않는다
    ctrl.set_level(_sec(ctrl, "2 Method").id, 3)  # 구조 탭에서 다른 섹션 레벨을 바꾼다
    r = ctrl.save_and_reassemble()
    en = ctrl.en_markdown()
    assert en.count("Related Systems") == 1 and "### 2 Method" in en and "Method body, edited." in en
    assert r["conflicts"] == 0
    # 편집을 버리고 원본에서 다시 만들어도 제목은 구조에 남는다(raw.md에 있다)
    ctrl.save_and_reassemble(keep_edits=False)
    assert ctrl.en_markdown().count("## Related Systems") == 1


def test_body_only_edit_skips_structure(ctrl):
    r, _ = _edit(ctrl, "Method body.", "Method body, changed.")
    assert r is None


def test_heading_without_source_line_is_left_as_edit(ctrl):
    sections = [s.text for s in ctrl.manifest.sections]
    r, mine = _edit(ctrl, "Method body.", "Method body.\n\n## Brand New Part\n\nTyped from scratch.")
    assert r["orphans"] == ["Brand New Part"] and r["added"] == 0
    assert [s.text for s in ctrl.manifest.sections] == sections
    assert ctrl.en_markdown() == mine  # 편집은 그대로


def test_added_heading_survives_reextraction(ctrl):
    """다시 변환은 raw.md를 새로 뽑는다 — added 표시로 같은 글의 줄을 다시 헤더로 세운다."""
    pristine = ctrl.wd.raw_md.read_text(encoding="utf-8")
    _edit(ctrl, "\nRelated Systems\n", "\n## Related Systems\n")
    _edit(ctrl, "## Related Systems", "#### Related Systems")
    old = manifest_io.load(ctrl.wd)
    ctrl.wd.raw_md.write_text(pristine, encoding="utf-8")  # 추출을 다시 돌린 것처럼
    new = relayout.rebuild(ctrl.wd, old)
    sec = next(s for s in new.sections if s.text == "Related Systems")
    assert sec.added and sec.level == 4
    assert ctrl.wd.en_md.read_text(encoding="utf-8").count("#### Related Systems") == 1


def test_promote_stays_between_neighbours():
    """짧은 제목이 엉뚱한 문단 첫머리에 붙지 않게 — 앞뒤 섹션 제목 사이에서만 찾는다."""
    from narani.ir import ManifestSection

    raw = "## 1 Intro\n\nResults are shown later.\n\n## 2 Body\n\nResults\n\nText.\n\n## 3 End\n"
    secs = [ManifestSection(id="a", text="1 Intro", line=0, level=2),
            ManifestSection(id="b", text="2 Body", line=4, level=2),
            ManifestSection(id="c", text="Results", line=6, level=3, added=True),
            ManifestSection(id="d", text="3 End", line=10, level=2)]
    new, n = edit_heads.promote(raw, secs)
    assert n == 1 and "Results are shown later." in new and "\n### Results\n" in new


def test_split_prefix():
    assert edit_heads.split_prefix("**3.2 Results.** We found it.", "3.2 Results") == "We found it."
    assert edit_heads.split_prefix("3.2 Results", "3.2 Results") == ""
    assert edit_heads.split_prefix("Resultsy text", "Results") is None
    assert edit_heads.split_prefix("Other text", "Results") is None


def test_renamed_heading_is_not_an_orphan(ctrl):
    """제목 글만 고쳤다 — 구조는 그대로, 경고 없이 3-way 합치기가 고친 글을 살린다."""
    raw = ctrl.wd.raw_md.read_text(encoding="utf-8")
    texts = [s.text for s in ctrl.manifest.sections]
    r, mine = _edit(ctrl, "# 2 Method", "# 2 Our Method")
    assert r["orphans"] == [] and r["added"] == 0 and r["levels"] == 0
    assert [s.text for s in ctrl.manifest.sections] == texts
    assert ctrl.wd.raw_md.read_text(encoding="utf-8") == raw and ctrl.en_markdown() == mine


def test_rename_with_level_change_applies_level(ctrl):
    r, _ = _edit(ctrl, "# 2 Method", "## 2 Our Method")
    assert r["orphans"] == [] and _sec(ctrl, "2 Method").level == 2
    assert "## 2 Our Method" in ctrl.en_markdown()


def test_shortened_heading_does_not_split_source(ctrl):
    """'3 Results and Discussion' → '3 Results' — 앞머리 일치지만 본문으로 이어지지 않으니 쪼개지 않는다."""
    raw = ctrl.wd.raw_md.read_text(encoding="utf-8")
    n = len(ctrl.manifest.sections)
    r, mine = _edit(ctrl, "# 3 Results and Discussion", "# 3 Results")
    assert r["orphans"] == [] and r["added"] == 0
    assert len(ctrl.manifest.sections) == n and ctrl.wd.raw_md.read_text(encoding="utf-8") == raw
    assert ctrl.en_markdown() == mine  # 'and Discussion' 문단이 생기지 않았다
