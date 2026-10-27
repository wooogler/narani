"""폰 리더 렌더 — 논문 한 편의 원문·번역 행, 그림 한 번만, 앵커, 인용·각주 시트 데이터."""

from __future__ import annotations

import collections
import json
import re
from pathlib import Path

import pytest

pytest.importorskip("nicegui", reason="웹 UI 의존성(ui extra) 미설치")

from narani.ui import mobile, sentpair  # noqa: E402
from narani.workdir import WorkDir  # noqa: E402

EN = """# Bespoke Bots

We study how instructors customize chatbots [1](#ref-1). It matters.<sup class="md-fn"><a href="#fn-1">1</a></sup>

![Figure 1](images/figure-1.png)
> **Figure 1:** The interface.

## References

<a id="ref-1"></a>[1] Lee, M. 2022. CoAuthor. CHI.

<a id="fn-1"></a>**1.** Footnote text.
"""

KO = """# 맞춤형 봇

우리는 교수자가 챗봇을 어떻게 맞춤화하는지 연구한다 [1](#ref-1). 이는 중요하다.<sup class="md-fn"><a href="#fn-1">1</a></sup>

![Figure 1](images/figure-1.png)
> **Figure 1:** 인터페이스.

## References

<a id="ref-1"></a>[1] Lee, M. 2022. CoAuthor. CHI.

<a id="fn-1"></a>**1.** 각주 내용.
"""


def _wd(tmp_path, name: str, en: str = EN, ko: str | None = KO) -> WorkDir:
    from PIL import Image

    wd = WorkDir(tmp_path / f"{name}.md4")
    wd.ensure()
    wd.en_md.write_text(en, encoding="utf-8")
    if ko is not None:
        wd.ko_md.write_text(ko, encoding="utf-8")
    wd.out_images.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (40, 20), (200, 30, 30)).save(wd.out_images / "figure-1.png")
    wd.references_json.parent.mkdir(parents=True, exist_ok=True)
    wd.references_json.write_text(json.dumps({"accepted": [
        {"label": "1", "authors": ["Mina Lee"], "year": 2022, "title": "CoAuthor", "venue": "CHI",
         "doi": "10.1145/3491102.3502030"}]}), encoding="utf-8")
    wd.footnotes_json.write_text(json.dumps({"1": "Footnote text."}), encoding="utf-8")
    return wd


def _html(root) -> tuple[dict, str]:
    p = mobile._paper(0, root)
    return p, mobile._paper_html(p)


def test_paper_rows_images_and_anchors(tmp_path):
    p, html = _html(_wd(tmp_path, "a").root)
    assert html.startswith('<article class="paper has-ko" id="p0" data-key="a">')
    # 정렬 행: 번역 셀과 원문 셀이 짝으로
    assert '<div class="cell ko" lang="ko">' in html and '<div class="cell en" lang="en">' in html
    assert "교수자가 챗봇을" in html and "instructors customize" in html
    # 그림 줄은 원문·번역 공통 셀로 한 번만 — 크기도 적혀 자리가 미리 잡힌다
    assert html.count("<img ") == 1
    assert re.search(r'<img [^>]*src="data:image/png;base64,[^"]+" width="40" height="20"', html)
    # id가 언어별로 갈려 겹치지 않고, 내부 링크는 전부 있는 곳을 가리킨다
    ids = re.findall(r'\sid="([^"]+)"', html)
    assert not [k for k, v in collections.Counter(ids).items() if v > 1]
    assert {h for h in re.findall(r'href="#([^"]+)"', html)} - {"lib"} <= set(ids)
    # 번역 셀의 인용은 번역 쪽 참고문헌으로
    assert 'href="#p0-ko-ref-1"' in html and 'href="#p0-en-ref-1"' in html


def test_untranslated_paper_is_source_only(tmp_path):
    p, html = _html(_wd(tmp_path, "b", ko=None).root)
    assert 'class="paper noalign"' in html and not p["has_ko"]
    assert "번역이 없는 논문이라 원문만 보입니다." in html


def test_untranslated_references_reachable_from_both_sides(tmp_path):
    """참고문헌이 원문·번역에서 같으면 공통 셀 하나 — 양쪽 인용이 모두 닿도록 별칭 앵커를 둔다."""
    _, html = _html(_wd(tmp_path, "a").root)
    assert 'id="p0-ko-ref-1"' in html and 'id="p0-en-ref-1"' in html
    assert html.count("[1] Lee, M. 2022. CoAuthor. CHI.") == 1


def test_cite_and_footnote_data_for_tap_sheet(tmp_path):
    p, _ = _html(_wd(tmp_path, "a").root)
    assert p["cites"]["1"]["url"] == "https://doi.org/10.1145/3491102.3502030"
    assert "CoAuthor" in p["cites"]["1"]["text"]
    assert p["fns"] == {"1": "Footnote text."}
    assert p["title_ko"] == "맞춤형 봇"  # 번역 제목을 앞에


def test_reader_embeds_shared_sentence_pairing():
    """문장 짝 규칙은 뷰어와 같은 코드 — 두 화면이 갈라지지 않게 같은 문자열을 끼운다."""
    from narani.phone import web
    from narani.ui.app import _ANNO_HTML

    js = web.site_files(local=True)["app.js"][0].decode()
    assert sentpair.JS in js and mobile.CORE_JS in js
    assert sentpair.JS in _ANNO_HTML
    assert "function pairUp" in sentpair.JS and "function sentsOf" in sentpair.JS


def test_noalign_when_section_counts_differ(tmp_path):
    ko = KO.replace("## References", "## 참고문헌\n\n## 부록")
    _, html = _html(_wd(tmp_path, "a", ko=ko).root)
    assert 'class="paper has-ko noalign"' in html
    assert "문단 대조·문장 짝을 쓸 수 없습니다" in html


def test_split_shared_images():
    shared, en, ko = mobile._split_shared_images("![F](a.png)\n> **F:** cap", "![F](a.png)\n> **F:** 캡션")
    assert shared == "![F](a.png)" and en == "> **F:** cap" and ko == "> **F:** 캡션"
    assert mobile._split_shared_images("text", "글")[0] == ""


def test_large_image_is_shrunk(tmp_path):
    from PIL import Image

    p = tmp_path / "big.png"
    Image.effect_noise((2400, 1200), 80).convert("RGB").save(p)
    data, mime, size = mobile._image_bytes(p)
    assert size == (mobile.MAX_IMG_W, 600)
    assert len(data) < p.stat().st_size and mime in ("image/png", "image/webp", "image/jpeg")


def test_mobile_html_command_is_gone():
    """폰 리더로 옮겨 간 '모바일 HTML 한 장' 내보내기 — 명령이 남아 있지 않다."""
    from click.testing import CliRunner

    from narani.cli import cli

    result = CliRunner().invoke(cli, ["mobile"])
    assert result.exit_code != 0 and "No such command" in result.output


def test_math_is_wrapped_in_a_scroll_box():
    # iOS Safari는 <math>에 준 max-width·overflow를 따르지 않는다 — 긴 수식이 페이지를 가로로 늘리지 않게 상자로 싼다
    out = mobile._render("값은 $t(29)=5.71, p<10^{-4}$ 이다.\n\n$$x^2+y^2$$", "p0-ko-", mobile._Images(Path("/nonexistent")))
    assert out.count('<span class="mx"><math') == 2 and out.count("</math></span>") == 2
    assert ".cell .mx {" in mobile._CSS and "main { overflow-x: clip; }" in mobile._CSS
