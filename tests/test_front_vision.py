"""첫 페이지 비전 구조화 — 텍스트 층 대조(환각 0), 비전 직접 조립, 폴백 사슬, 캐시, 서지 고정."""

import json

from narani.extract import front_matter as fm
from narani.extract import front_vision as fv
from narani.ir import AuthorEntry, FrontMatterLayout
from narani.llm import FakeProvider

# PDF 1쪽 텍스트 층 (pdfium 모양: \r\n 줄바꿈, 줄끝 하이픈은 ￾)
PAGE = (
    "DocVoyager: Anticipating User’s Information Needs and Guiding\r\n"
    "Document Reading through Question Answering\r\n"
    "Yoonjoo Lee∗\r\nSchool of Computing\r\nKAIST\r\nDaejeon, Republic of Korea\r\n"
    "yoonjoo.lee@kaist.ac.kr\r\n"
    "Nedim Lipka\r\nAdobe Research\r\nSan Jose, California, USA\r\nlipka@adobe.com\r\n"
    "Alexa Siu\r\nAdobe Research\r\nSan Jose, California, USA\r\nasiu@adobe.com\r\n"
    "Abstract\r\nPeople often approach complex documents (e.g., academic papers\r\n"
    "and professional reports) with diverse goals and varying levels of prior knowl￾edge.\r\n"
    "∗Work done during an internship at Adobe Research.\r\n"
    "CHI EA ’25, Yokohama, Japan\r\n"
)

# docling이 뭉갠 앞부분: 저자 그리드가 표가 되고 여백 낱글자(j p g)가 끼었다
RAW = (
    "## DocVoyager: Anticipating User's Information Needs and Guiding Document Reading through "
    "Question Answering\n\n"
    "| Yoonjoo Lee | Nedim Lipka | Alexa Siu |\n|---|---|---|\n"
    "| j School of Computing | p Adobe Research | g Adobe Research |\n"
    "| KAIST | San Jose, California, USA | San Jose, California, USA |\n\n"
    "lipka@adobe.com asiu@adobe.com\n\n"
    "![Image](img-01.png)\n\n"
    "Figure 1: DocVoyager suggests goal-based questions.\n\n"
    "## Abstract\n\n"
    "People often approach complex documents (e.g., academic papers and professional reports) with "
    "diverse goals and varying levels of prior knowledge.\n\n"
    "## Keywords\n\nDocument Reading, Large Language Models\n\n"
    "CHI EA '25, Yokohama, Japan\n\n"
    "## ACM Reference Format:\n\n"
    "Yoonjoo Lee, Nedim Lipka, and Alexa Siu. 2025. DocVoyager. In CHI EA '25. ACM, 6 pages.\n\n"
    '<a id="fn-author-1"></a>∗ Work done during an internship at Adobe Research.\n\n'
    "## 1 Introduction\n\n"
    "Complex documents are approached by readers with diverse goals.\n"
)


def _read(**over) -> fv.FrontPageRead:
    base = dict(
        is_first_page=True,
        title="DocVoyager: Anticipating User’s Information Needs and Guiding Document Reading "
              "through Question Answering",
        authors=[
            fv.VisionAuthor(name="Yoonjoo Lee", emails=["yoonjoo.lee@kaist.ac.kr"],
                            affiliation_lines=["School of Computing", "KAIST", "Daejeon, Republic of Korea"],
                            marks=["∗"]),
            fv.VisionAuthor(name="Nedim Lipka", emails=["lipka@adobe.com"],
                            affiliation_lines=["Adobe Research", "San Jose, California, USA"]),
            fv.VisionAuthor(name="Alexa Siu", emails=["asiu@adobe.com"],
                            affiliation_lines=["Adobe Research", "San Jose, California, USA"]),
        ],
        author_notes=[fv.VisionNote(mark="∗", text="Work done during an internship at Adobe Research.")],
        has_teaser_figure=True,
        abstract_start="People often approach complex documents (e.g., academic papers",
        furniture=["CHI EA ’25, Yokohama, Japan"],
    )
    base.update(over)
    return fv.FrontPageRead(**base)


def _verified(**over) -> fv.Verified:
    v, why = fv.verify(_read(**over), PAGE)
    assert v is not None, why
    return v


# --- 정규화·대조 -------------------------------------------------------------
def test_norm_ignores_spacing_hyphenation_ligatures_accents_case():
    assert fv.norm("knowl￾edge") == fv.norm("knowledge")
    assert fv.norm("ﬁnding") == fv.norm("finding")
    assert fv.norm("Zoë  Smith") == fv.norm("ZOE SMITH")
    # 한글 이름이 빈 문자열이 되어 대조를 공짜로 통과하지 않는다
    assert fv.norm("이상욱") and fv.norm("이상욱") in fv.norm("저자: 이상욱, 서울") and fv.norm("이상욱") not in fv.norm("김철수")


def test_verify_accepts_grounded_result():
    v = _verified()
    assert [a.name for a in v.authors] == ["Yoonjoo Lee", "Nedim Lipka", "Alexa Siu"]
    assert v.authors[0].affiliations == ["School of Computing", "KAIST", "Daejeon, Republic of Korea"]
    assert v.notes == ["∗ Work done during an internship at Adobe Research."]


def test_verify_rejects_any_invented_string():
    bad_email = _read()
    bad_email.authors[1].emails = ["nlipka@adobe.com"]  # 지어낸 주소
    assert fv.verify(bad_email, PAGE)[0] is None
    bad_affil = _read()
    bad_affil.authors[2].affiliation_lines = ["Adobe Inc."]  # 교정·추정
    v, why = fv.verify(bad_affil, PAGE)
    assert v is None and "affiliation" in why
    assert fv.verify(_read(title="DocVoyager: Guiding Document Reading"), PAGE)[0] is None
    bad_name = _read()
    bad_name.authors.append(fv.VisionAuthor(name="Tong Sun"))
    assert fv.verify(bad_name, PAGE)[0] is None
    bad_note = _read(author_notes=[fv.VisionNote(mark="†", text="Equal contribution.")])
    assert fv.verify(bad_note, PAGE)[0] is None
    assert fv.verify(_read(is_first_page=False), PAGE)[0] is None


def test_verify_grouped_email_and_affiliation_numbers():
    page = PAGE + "{alice, bob}@x.edu\r\n1Department of Computer Science\r\n"
    r = _read()
    r.authors[0].emails = ["alice@x.edu"]  # 묶음 표기를 펼친 주소
    r.authors[0].affiliation_lines = ["1Department of Computer Science"]
    v, why = fv.verify(r, page)
    assert v is not None, why
    assert v.authors[0].emails == ["alice@x.edu"]
    assert v.authors[0].affiliations == ["Department of Computer Science"]  # 소속 번호는 뗀다
    r.authors[0].emails = ["carol@x.edu"]
    assert fv.verify(r, page)[0] is None


def test_verify_drops_ungrounded_locators_without_rejecting():
    v = _verified(abstract_start="Something not on the page at all here", furniture=["Not on the page at all"])
    assert v.abstract_start == "" and v.furniture == []


# --- 비전 직접 조립 -----------------------------------------------------------
def test_build_vision_rebuilds_front_and_keeps_rest():
    ops: dict = {}
    out, entries = fm._build_vision(_verified(), RAW, None, ops)
    assert ops["vision_direct"] == "ok"
    front, body = out.split("## Abstract")
    assert front.startswith("## DocVoyager: Anticipating User’s")
    assert "**Yoonjoo Lee**  \nyoonjoo.lee@kaist.ac.kr  \nSchool of Computing  \nKAIST  \n" \
           "Daejeon, Republic of Korea" in front
    assert "| j School" not in out and "\np Adobe" not in out  # 표·낱글자 잡음 제거
    assert "∗ Work done during an internship" in front and out.count("Work done during") == 1
    assert "![Image](img-01.png)\n\nFigure 1:" in front  # teaser는 저자·주석 뒤에 그대로
    assert "ACM Reference Format" not in out and "CHI EA" not in out  # 상용구·venue 줄
    assert "## Keywords\n\nDocument Reading" in body
    assert body.endswith("## 1 Introduction\n\nComplex documents are approached by readers with diverse goals.\n")
    assert fm._render_authors_detail(entries, None) in out  # authors.json과 바이트 단위로 맞는다


def test_build_vision_is_idempotent():
    v = _verified()
    once, _ = fm._build_vision(v, RAW, None)
    twice, _ = fm._build_vision(v, once, None)
    assert twice == once


def test_build_vision_bails_on_uncovered_front_block():
    """비전 문자열로 설명되지 않는 앞부분 블록(머리 없는 초록 등)은 지우지 않고 포기한다."""
    raw = RAW.replace("![Image](img-01.png)", "An unheaded paragraph of real content that nobody listed "
                                               "anywhere in the vision result, long enough to matter.")
    ops: dict = {}
    raw = raw.replace("People often approach", "Readers often approach")  # 초록 첫머리도 못 찾게
    assert fm._build_vision(_verified(), raw, None, ops) is None
    assert ops["vision_direct"].startswith("uncovered front block")


def test_build_vision_drops_stray_author_cell_after_body():
    raw = RAW.replace("Complex documents are approached by readers with diverse goals.\n",
                      "Complex documents are approached by readers with diverse goals.\n\n"
                      "Alexa Siu Adobe Research San Jose, California, USA asiu@adobe.com\n\n"
                      "Second body paragraph.\n")
    out, _ = fm._build_vision(_verified(), raw, None)
    body = out.split("## 1 Introduction")[1]
    assert "asiu@adobe.com" not in body and "Second body paragraph." in body


# --- 폴백 사슬 -----------------------------------------------------------------
def _label_provider(layout):
    calls = []

    def parse(system, user, schema):
        calls.append(schema)
        return layout

    return FakeProvider(parse_fn=parse), calls


def test_vision_direct_needs_no_label_call():
    prov, calls = _label_provider(FrontMatterLayout(title=0, authors=[1], body_start=9))
    ops: dict = {}
    out, entries = fm.normalize_authors(prov, RAW, None, ops=ops, vision=_verified())
    assert ops["path"] == "vision" and calls == []
    assert len(entries) == 3


def test_label_path_uses_vision_strings_when_direct_assembly_bails():
    raw = ("Contents lists available at SomeJournalSite\n\n" + RAW)  # 설명 안 되는 머리말 → 직접 조립 포기
    blocks = fm._split_blocks(raw)
    body = next(i for i, b in enumerate(blocks) if b.startswith("## 1 Introduction"))
    abstract = next(i for i, b in enumerate(blocks) if b.startswith("## Abstract"))
    layout = FrontMatterLayout(title=1, authors=[2, 3], sections=[abstract, abstract + 1], body_start=body)
    prov, calls = _label_provider(layout)
    ops: dict = {}
    v = _verified(furniture=[])
    out, entries = fm.normalize_authors(prov, raw, None, ops=ops, vision=v)
    assert ops["path"] == "label+vision" and len(calls) == 1
    assert ops["vision_direct"].startswith("uncovered front block 0")
    assert out.startswith("## DocVoyager: Anticipating User’s")  # 제목 글자는 비전 것
    assert [e.name for e in entries] == ["Yoonjoo Lee", "Nedim Lipka", "Alexa Siu"]
    assert "Daejeon, Republic of Korea" in out and "| j School" not in out
    assert out.count("Work done during") == 1


def test_no_vision_keeps_previous_paths():
    prov, calls = _label_provider(FrontMatterLayout(title=0, authors=[1, 2], body_start=11))
    ops: dict = {}
    fm.normalize_authors(prov, RAW, None, ops=ops)
    assert ops["path"] == "label" and len(calls) == 1
    ops = {}
    fm.normalize_authors(None, RAW, None, ops=ops)
    assert ops["path"] == "heuristic"


# --- 호출·캐시 -----------------------------------------------------------------
def test_read_cached_calls_once_and_reuses(tmp_path):
    calls = []

    def parse(system, user, schema):
        calls.append(schema)
        assert schema is fv.FrontPageRead and "PDF TEXT LAYER" in user
        return _read()

    prov = FakeProvider(parse_fn=parse)
    page = fv.FrontPage(png=b"png", text=PAGE)
    cache = tmp_path / "front_vision.json"
    v1, info1 = fv.read_cached(prov, page, cache)
    v2, info2 = fv.read_cached(prov, page, cache)
    assert v1 is not None and v2 is not None and len(calls) == 1
    assert info1["status"] == info2["status"] == "verified" and info2.get("cached") is True
    assert json.loads(cache.read_text())["result"]["title"].startswith("DocVoyager")
    # 모델이 바뀌면 다시 묻는다
    fv.read_cached(FakeProvider(parse_fn=parse, model="other"), page, cache)
    assert len(calls) == 2


def test_read_cached_keeps_rejected_answer_and_skips_imageless_provider(tmp_path):
    calls = []

    def parse(system, user, schema):
        calls.append(1)
        return _read(title="Totally Different Invented Title Here")

    prov = FakeProvider(parse_fn=parse)
    page = fv.FrontPage(png=b"png", text=PAGE)
    cache = tmp_path / "fv.json"
    assert fv.read_cached(prov, page, cache)[1]["status"] == "rejected"
    assert fv.read_cached(prov, page, cache)[1]["status"] == "rejected"
    assert len(calls) == 1  # 거부된 답도 캐시 — 같은 실패에 다시 돈을 쓰지 않는다

    class NoImage(FakeProvider):
        def parse_image(self, *a, **k):  # noqa: ANN002, ANN003
            raise NotImplementedError

    v, info = fv.read_cached(NoImage(parse_fn=parse), page, tmp_path / "other.json")
    assert v is None and info["status"] == "skipped"


# --- 서지 고정 -----------------------------------------------------------------
BIB_TITLE = ("Supporting Business Document Workflows via Collection-Centric Information Foraging "
             "with Large Language Models")
FOK_PAGE = ("Supporting Business Document Workflows via Collection-Centric\r\n"
            "Information Foraging with Large Language Models\r\nRaymond Fok\r\nAlexa Siu\r\nTong Sun\r\n")


def test_pin_title_replaces_truncated_title_only_when_grounded():
    md = "## with Large Language Models\n\n**Raymond Fok**\n\n## 1 Introduction\n\nBody.\n"
    out, changed = fm.pin_title(md, BIB_TITLE, FOK_PAGE)
    assert changed and out.startswith(f"## {BIB_TITLE}\n\n**Raymond Fok**")
    # 서지 제목이 텍스트 층에 없으면(서지가 틀렸을 수 있다) 손대지 않는다
    assert fm.pin_title(md, BIB_TITLE, "Some other page\r\nwith Large Language Models") == (md, False)
    assert fm.pin_title(md, BIB_TITLE, None) == (md, False)


def test_pin_title_keeps_equivalent_or_longer_title():
    page = "DocVoyager: Anticipating User’s Information Needs\r\n"
    md = "## DocVoyager: Anticipating User's Information Needs\n\nBody.\n"
    assert fm.pin_title(md, "DocVoyager: Anticipating User’s Information Needs", page) == (md, False)
    md2 = "## Foraging Documents: A Study of Many Things Together\n\nBody.\n"
    page2 = "Foraging Documents: A Study of Many Things Together\r\n"
    assert fm.pin_title(md2, "Foraging Documents", page2) == (md2, False)


def test_pin_authors_reorders_and_adds_grounded_missing():
    entries = [AuthorEntry(name="Alexa Siu", emails=["asiu@adobe.com"]), AuthorEntry(name="Raymond Fok")]
    new, did = fm.pin_authors(entries, ["Raymond Fok", "Tong Sun", "Alexa Siu"], FOK_PAGE)
    assert [e.name for e in new] == ["Raymond Fok", "Tong Sun", "Alexa Siu"]
    assert did == "reordered+added" and new[2].emails == ["asiu@adobe.com"]
    # 쪽에 없는 서지 저자는 넣지 않는다
    new, did = fm.pin_authors(entries, ["Raymond Fok", "Ghost Author", "Alexa Siu"], FOK_PAGE)
    assert [e.name for e in new] == ["Raymond Fok", "Alexa Siu"] and did == "reordered"


def test_pin_authors_leaves_alone_when_bib_does_not_cover_extraction():
    entries = [AuthorEntry(name="Raymond Fok"), AuthorEntry(name="Someone Else")]
    assert fm.pin_authors(entries, ["Alexa Siu", "Raymond Fok"], FOK_PAGE) == (entries, "")
    same = [AuthorEntry(name="Garreth W. Tigwell"), AuthorEntry(name="Alexa Siu")]
    assert fm.pin_authors(same, ["Garreth Tigwell", "Alexa Siu"], "x") == (same, "")


def test_pin_to_bib_rerenders_author_block_consistently():
    entries = [AuthorEntry(name="Alexa Siu", emails=["asiu@adobe.com"]), AuthorEntry(name="Raymond Fok")]
    md = (f"## {BIB_TITLE}\n\n" + fm._render_authors_detail(entries, None)
          + "\n\n## 1 Introduction\n\nBody.\n")
    ops: dict = {}
    out, new = fm.pin_to_bib(md, entries, {"title": BIB_TITLE, "authors": ["Raymond Fok", "Alexa Siu"]},
                             FOK_PAGE, None, ops)
    assert [e.name for e in new] == ["Raymond Fok", "Alexa Siu"] and ops["bib_authors"] == "reordered"
    assert fm._render_authors_detail(new, None) in out and ops["bib_title"] is False


# --- 파이프라인 -----------------------------------------------------------------
def test_run_frontmatter_vision_path_records_and_caches(tmp_path, monkeypatch):
    from narani import pipeline
    from narani.workdir import WorkDir

    wd = WorkDir(tmp_path / "p.md4")
    wd.extract.mkdir(parents=True)
    wd.raw_md.write_text(RAW, encoding="utf-8")
    monkeypatch.setattr(pipeline, "_front_page", lambda _wd, **_k: fv.FrontPage(png=b"png", text=PAGE))
    calls = []

    def parse(system, user, schema):
        calls.append(schema)
        return _read()

    r = pipeline.run_frontmatter(wd, provider=FakeProvider(parse_fn=parse))
    assert r["path"] == "vision" and r["vision"]["status"] == "verified" and calls == [fv.FrontPageRead]
    st = wd.load_status()["frontmatter"]
    assert st["path"] == "vision"
    authors = json.loads(wd.authors_json.read_text(encoding="utf-8"))
    assert [a["name"] for a in authors] == ["Yoonjoo Lee", "Nedim Lipka", "Alexa Siu"]
    first = wd.raw_md.read_text(encoding="utf-8")
    # 다시 변환(raw.md를 새로 뽑음)해도 비전은 캐시 — 재호출 없음, 같은 결과
    wd.raw_md.write_text(RAW, encoding="utf-8")
    pipeline.run_frontmatter(wd, provider=FakeProvider(parse_fn=parse))
    assert len(calls) == 1 and wd.raw_md.read_text(encoding="utf-8") == first
