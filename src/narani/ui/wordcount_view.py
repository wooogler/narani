"""원문 단어 수 — 목차 머리의 작은 숫자와, 누르면 뜨는 범위별·섹션별 표 (§wordcount가 센다).

무엇을 셀지(범위 = 섹션 종류, 요소 = 캡션·표·각주 …)는 전역 설정 `[ui].word_count_kinds` /
`[ui].word_count_elements`에 기억한다. 기본은 학회 제출에 흔한 기준 — 초록+본문+감사의 글, 제목·저자·참고문헌·
부록은 빼고, 캡션·표·각주·섹션 제목은 넣는다. 세기는 en.md 글자가 바뀔 때만 다시 한다(캐시).
"""

from __future__ import annotations

from collections.abc import Callable

from narani import config
from narani.i18n import N_, t
from narani.wordcount import DEFAULT_ELEMENTS, DEFAULT_KINDS, ELEMENTS, KINDS, WordCount, count_words

KIND_LABELS = {
    "front": N_("제목·저자"), "abstract": N_("초록"), "meta": N_("CCS·키워드"), "body": N_("본문"),
    "ack": N_("감사의 글"), "statements": N_("선언(연구비·이해 상충·저자 기여)"), "references": N_("참고문헌"),
    "appendix": N_("부록"), "footnotes": N_("각주 모음"),
}
ELEMENT_LABELS = {
    "heading": N_("섹션 제목"), "text": N_("본문 글"), "caption": N_("그림·표 캡션"), "table": N_("표 내용"),
    "footnote": N_("각주"), "math": N_("인라인 수식(하나 = 한 단어)"), "code": N_("코드 블록"),
}
# 설정에서 고를 범위 — '각주 모음'은 제목 한 줄뿐(각주 글은 부른 섹션으로 간다)이라 고르지 않는다
_PICK_KINDS = [k for k in KINDS if k != "footnotes"]
# 본문 글은 늘 센다 — 이걸 빼면 단어 수가 아니다
_PICK_ELEMENTS = [e for e in ELEMENTS if e != "text"]

_memo: dict = {}


def counted(md: str) -> WordCount:
    key = hash(md)
    if _memo.get("key") != key:
        _memo.update(key=key, v=count_words(md))
    return _memo["v"]


def prefs() -> tuple[frozenset[str], frozenset[str], bool]:
    """(범위, 요소, 목차에 섹션별 수) — 설정이 없거나 깨졌으면 기본값."""
    ui_cfg = config.load_config().get("ui", {})
    kinds = ui_cfg.get("word_count_kinds")
    elems = ui_cfg.get("word_count_elements")
    kinds = frozenset(k for k in kinds if k in KINDS) if isinstance(kinds, list) else DEFAULT_KINDS
    elems = frozenset(e for e in elems if e in ELEMENTS) | {"text"} if isinstance(elems, list) else DEFAULT_ELEMENTS
    return kinds, elems, bool(ui_cfg.get("word_count_toc", False))


def criteria(kinds: frozenset[str], elems: frozenset[str]) -> str:
    """'초록 · 본문 · 감사의 글 — 캡션·표 포함' 꼴의 한 줄 — 목차 숫자의 툴팁과 표 머리에."""
    ks = " · ".join(t(KIND_LABELS[k]) for k in _PICK_KINDS if k in kinds) or t("없음")
    es = ", ".join(t(ELEMENT_LABELS[e]) for e in _PICK_ELEMENTS if e in elems)
    return t("범위: {kinds}", kinds=ks) + "\n" + (t("함께 셈: {elems}", elems=es) if es else t("본문 글만 셈"))


def open_dialog(md: str, on_change: Callable[[], None]) -> None:
    """범위별·섹션별 표 + 무엇을 셀지 고르기. 지금 `with` 문맥(refreshable 밖의 자리) 안에 짓는다."""
    from nicegui import ui

    wc = counted(md)

    def save(field: str, value) -> None:  # noqa: ANN001
        config.set_section_value("ui", field, value)
        body.refresh()
        on_change()

    def toggle(field: str, cur: frozenset[str], key: str, on: bool) -> None:
        save(field, sorted((cur | {key}) if on else (cur - {key})))

    @ui.refreshable
    def body() -> None:
        kinds, elems, toc_on = prefs()
        with ui.row().classes("items-baseline gap-2 w-full no-wrap"):
            ui.label(t("원문 단어 수")).classes("text-base font-semibold")
            ui.label(f"{wc.total(kinds, elems):,}").classes("text-2xl font-bold text-primary")
            ui.label(t("단어")).classes("text-sm text-gray-500")
        ui.label(criteria(kinds, elems)).classes("text-xs text-gray-500").style("white-space:pre-line")

        with ui.row().classes("w-full gap-6 no-wrap items-start"):
            with ui.column().classes("gap-0"):
                ui.label(t("범위")).classes("md4-panel-title")
                by_kind = wc.by_kind(elems)
                for k in _PICK_KINDS:
                    with ui.row().classes("items-center gap-1 no-wrap"):
                        ui.checkbox(t(KIND_LABELS[k]), value=k in kinds,
                                    on_change=lambda e, kk=k, cur=kinds: toggle("word_count_kinds", cur, kk, e.value)) \
                            .props("dense size=xs").classes("text-sm")
                        ui.label(f"{by_kind[k]:,}").classes("text-xs text-gray-500")
            with ui.column().classes("gap-0"):
                ui.label(t("함께 셀 것")).classes("md4-panel-title")
                by_elem = wc.by_element(kinds)
                for e in _PICK_ELEMENTS:
                    with ui.row().classes("items-center gap-1 no-wrap"):
                        ui.checkbox(t(ELEMENT_LABELS[e]), value=e in elems,
                                    on_change=lambda ev, ee=e, cur=elems: toggle("word_count_elements", cur, ee, ev.value)) \
                            .props("dense size=xs").classes("text-sm")
                        ui.label(f"{by_elem[e]:,}").classes("text-xs text-gray-500")
                ui.separator().classes("my-1")
                ui.checkbox(t("목차에 섹션별 수 보이기"), value=toc_on,
                            on_change=lambda e: save("word_count_toc", bool(e.value))) \
                    .props("dense size=xs").classes("text-sm")
                ui.button(t("기본값으로"), on_click=lambda: (
                    config.set_section_value("ui", "word_count_kinds", None),
                    save("word_count_elements", None))).props("flat dense no-caps size=sm color=grey-7")

        ui.label(t("섹션별")).classes("md4-panel-title q-mt-sm")
        with ui.element("div").classes("md4-wc-secs"):
            for s in wc.sections:
                n = s.total(elems, kinds)
                raw = s.total(elems)
                if s.index is None and not raw:
                    continue
                with ui.element("div").classes("md4-wc-row" + ("" if n else " md4-wc-off")):
                    ui.label(s.title or t("(첫 제목 앞)")).classes("md4-wc-title").style(
                        f"padding-left:{(max(s.level, 1) - 1) * 12}px")
                    ui.label(t(KIND_LABELS[s.kind])).classes("md4-wc-kind")
                    # 범위 밖 섹션은 흐리게, 그 섹션을 넣으면 몇 단어인지(괄호)
                    ui.label(f"{n:,}" if n or not raw else f"({raw:,})").classes("md4-wc-n")
        ui.label(t("세지 않는 것: 인용 표기([12], (Smith et al., 2020), 링크된 인용 묶음), 본문의 각주 번호, 링크 주소"
                   "(링크 글자는 셈), 이미지, HTML 태그, 디스플레이 수식, 표 구분선, 참고문헌 번호. 각주 글은 그 각주를 "
                   "부른 섹션에 셉니다. 글자나 숫자가 든 낱말만 한 단어 — 하이픈 낱말은 하나, '—'·'→'는 0.")) \
            .classes("text-xs text-gray-500").style("line-height:1.5")

    with ui.dialog() as dlg, ui.card().classes("gap-2 q-pa-md").style("width:640px;max-width:94vw"):
        body()
        with ui.row().classes("w-full justify-end"):
            ui.button(t("닫기"), on_click=dlg.close).props("flat no-caps")
    dlg.on("hide", lambda: dlg.delete())
    dlg.open()


CSS = """
.md4-wcbadge { font-size: 10.5px; color: #9a968e; cursor: pointer; padding: 0 4px; border-radius: 4px; white-space: nowrap;
  min-width: 0; overflow: hidden; text-overflow: ellipsis; }
.md4-wcbadge:hover { color: #2383e2; background: rgba(35,131,226,.08); }
.vtoc-item.vtoc-hasn { position: relative; padding-right: 40px; }
.vtoc-n { position: absolute; right: 8px; top: 4px; font-size: 10.5px; color: #b5b1a9; font-variant-numeric: tabular-nums; }
.md4-wc-secs { max-height: 300px; overflow-y: auto; width: 100%; border-top: 1px solid rgba(128,128,128,.2); }
.md4-wc-row { display: flex; gap: 8px; align-items: baseline; padding: 2px 4px; font-size: 12.5px;
  border-bottom: 1px solid rgba(128,128,128,.12); }
.md4-wc-title { flex: 1 1 auto; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.md4-wc-kind { flex: none; font-size: 11px; color: #9a968e; }
.md4-wc-n { flex: none; width: 56px; text-align: right; font-variant-numeric: tabular-nums; }
.md4-wc-off { color: #a8a49c; }
"""
