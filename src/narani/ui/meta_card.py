"""서지 카드 — 홈 미리보기와 논문 뷰어의 ⓘ 팝오버가 **같은 칸·같은 편집 동작**을 쓴다.

한 논문의 제목·저자·연도·venue·DOI·약칭(눌러서 그 자리에서 고치기), 이름 규칙 힌트(폴더 이름 바꾸기),
서지 ✓, 컬렉션 태그(체크 메뉴로 넣고 빼기), 초록 EN/번역(눌러서 고치기), '온라인에서 다시 찾기'·
'AI로 다시 추출'. 저장 경로는 둘 다 같다 — 서지는 `paper_meta.update_fields`, 초록은
`preview.set_abstract`, 소속은 `library.set_memberships`(저장 위치 사본도 따라간다), 이름은
`paper_meta.rename_to_naming`. 그래서 어느 쪽에서 고쳐도 홈 목록·파일 이름 규칙·references.bib가
같은 규칙으로 따라온다.

화면마다 다른 것(홈: 들고 있는 목록 행·카드 글자·캐시, 뷰어: 헤더 탭 제목·주소)은 `MetaCard`의
훅으로 넘긴다. 이 모듈은 그 상태를 모른다.

`META_LOCK`·`BUSY`는 프로세스에 하나다 — 홈 창과 뷰어 창에서 같은 논문을 함께 고쳐도
paper_meta.json 읽고-쓰기가 겹쳐 앞의 값을 잃지 않게, '다시 찾기'가 두 번 돌지 않게.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from pathlib import Path

from narani.i18n import t
from narani.workdir import WorkDir

FAIL = object()  # commit이 저장하지 못했을 때 — 칸은 옛 값으로 돌아간다
META_LOCK = asyncio.Lock()  # 칸을 연달아 고칠 때 paper_meta.json 읽고-쓰기가 겹치지 않게 (홈·뷰어 공통)
BUSY: set[str] = set()  # '다시 찾기'·'다시 추출'이 도는 논문 root — 두 번 누르지 않게 (홈·뷰어 공통)

# 홈 미리보기에서 옮겨 온 스타일 — 뷰어 팝오버도 같은 칸을 그리므로 둘 다 싣는다(spa.add_css가 한 번만 넣는다).
CSS = """
/* 소속 프로젝트 태그 — 여러 개. 누르면 체크 메뉴로 넣고 뺀다. */
.md4-ptags { display: inline-flex; flex-wrap: wrap; gap: 4px; cursor: pointer; border-radius: 10px; }
.md4-ptag { display: inline-flex; align-items: center; gap: 3px; height: 19px; padding: 0 7px; border-radius: 10px;
  font-size: 11.5px; line-height: 1; color: #2383e2; background: rgba(35,131,226,.1); white-space: nowrap;
  max-width: 180px; overflow: hidden; text-overflow: ellipsis; }
.md4-ptag.none { color: #9a968e; background: transparent; box-shadow: inset 0 0 0 1px #dcdad5; }
/* 상위 컬렉션 보기에서 하위 컬렉션으로만 들어온 논문 — 그 하위 태그 (채우지 않고 테두리만) */
.md4-ptag.via { background: transparent; box-shadow: inset 0 0 0 1px rgba(35,131,226,.45); }
.md4-ptags:hover .md4-ptag { background: rgba(35,131,226,.18); }
.md4-ptags:hover .md4-ptag.none { color: #6b6862; background: rgba(0,0,0,.04); }
.md4-ptag-add { opacity: 0; color: #9a968e; }
.md4-ptags:hover .md4-ptag-add, .md4-pcard:hover .md4-ptag-add { opacity: 1; }
.md4-pv-title { font-size: 16px; font-weight: 700; line-height: 1.35; color: #37352f; overflow-wrap: anywhere; }
.md4-pv-auth { font-size: 12.5px; line-height: 1.45; color: #6b6862; }
.md4-pv-bib { font-size: 12.5px; color: #9a968e; }
.md4-pv-abs { font-size: 13.5px; line-height: 1.55; color: #37352f; white-space: pre-line; overflow-wrap: anywhere; }
.md4-pv-abs.none { color: #9a968e; }
.md4-pv-ok { font-size: 11.5px; color: #21ba45; }
/* 고칠 수 있는 서지·초록 칸 — 누르면 그 자리에서 입력칸(Enter·칸 밖 클릭 = 저장, Esc = 취소). 보일 때는 글자
   그대로이고 hover 때만 옅은 바탕 + 오른쪽 끝 연필. 편집 중에는 메모 탭 이름 바꾸기(.md4-notetab.editing)처럼
   흰 바탕에 1.5px 파란 테두리. 입력칸 글꼴은 칸의 것(제목 16px 굵게 · 본문 13.5px …)을 그대로 물려받는다. */
.md4-pv-ed { position: relative; min-width: 0; margin: 0 -5px; padding: 1px 20px 1px 5px; border-radius: 5px;
  cursor: text; transition: background .12s ease; }
.md4-pv-ed:hover { background: rgba(55,53,47,.05); }
.md4-pv-ed-v { overflow-wrap: anywhere; }
.md4-pv-ed-v.ph { color: #b4b0a8; font-weight: 400; font-size: 12.5px; }
.md4-pv-pen { position: absolute; right: 4px; top: 4px; color: #9a968e; opacity: 0; transition: opacity .12s ease;
  pointer-events: none; }
.md4-pv-ed:hover .md4-pv-pen { opacity: .75; }
.md4-pv-ed.editing, .md4-pv-ed.editing:hover { background: #fff; box-shadow: 0 0 0 1.5px #2383e2; padding-right: 5px; }
.md4-pv-ed.editing .md4-pv-pen { display: none; }
.md4-pv-ed .q-field__control, .md4-pv-ed .q-field__control-container, .md4-pv-ed .q-field__marginal {
  height: auto !important; min-height: 0 !important; padding: 0 !important; }
.md4-pv-ed .q-field__control { color: inherit !important; }  /* Quasar는 포커스 칸 글자를 파랗게 칠한다 */
.md4-pv-ed .q-field__native { min-height: 0 !important; padding: 0 !important; font: inherit; color: inherit;
  line-height: inherit; letter-spacing: inherit; }
.md4-pv-ed input.q-field__native { height: 1.45em; }
.md4-pv-ed textarea.q-field__native { resize: none; }
/* 연도 · venue · DOI · 약칭 — 이름표 | 값 두 칸 (Zotero 정보 칸처럼) */
.md4-pv-kv { display: grid; grid-template-columns: max-content minmax(0, 1fr); column-gap: 12px; row-gap: 1px;
  align-items: start; }
.md4-pv-k { font-size: 11.5px; line-height: 1.45; color: #9a968e; padding-top: 2px; white-space: nowrap; }
.md4-pv-v { font-size: 12.5px; line-height: 1.45; color: #6b6862; }
/* 이름 규칙 힌트 — 서지를 고쳐 기준명이 폴더 이름과 달라지면 한 줄 */
.md4-pv-hint { display: flex; flex-wrap: wrap; align-items: center; gap: 2px 6px; font-size: 12px; color: #9a968e;
  line-height: 1.5; }
.md4-pv-hint-n { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 11.5px; color: #6b6862;
  background: rgba(55,53,47,.06); border-radius: 4px; padding: 0 5px; overflow-wrap: anywhere; }
.md4-pv-hint .q-btn { min-height: 0; padding: 0 4px; font-size: 12px; }
.md4-pv-redo .q-btn { font-size: 12px; }
/* EN/KO — 작은 세그먼트 */
.md4-seg { display: inline-flex; gap: 2px; padding: 2px; border-radius: 6px; background: rgba(55,53,47,.06); }
.md4-seg span { font-size: 11px; font-weight: 600; line-height: 1; padding: 3px 8px; border-radius: 4px; cursor: pointer;
  color: #9a968e; user-select: none; }
.md4-seg span.on { background: #fff; color: #37352f; box-shadow: 0 1px 2px rgba(0,0,0,.1); cursor: default; }
.md4-menucap { font-size: 11px; font-weight: 700; letter-spacing: .05em; color: #9a968e; padding: 8px 16px 2px; }
@media (prefers-color-scheme: dark) {
  /* 고칠 수 있는 칸은 밝은 바탕(홈 미리보기 열·뷰어 팝오버)을 따른다 — 편집 중 칸은 흰 바탕 그대로, 테두리만 다크 쪽 파랑 */
  .md4-pv-ed.editing, .md4-pv-ed.editing:hover { background: #fff; box-shadow: 0 0 0 1.5px #3b8eea; }
}
"""


# ----- 칸 글자 ↔ 저장 값 -----

def field_text(key: str, value) -> tuple[str, str]:  # noqa: ANN001
    """저장된 값 → (보이는 글자, 입력칸 글자). 저자는 보일 때 쉼표로, 고칠 때는 한 줄에 한 명."""
    if key == "authors":
        names = [str(a) for a in (value or []) if str(a).strip()]
        return ", ".join(names), "\n".join(names)
    s = "" if value is None else str(value)
    return s, s


def parse_field(key: str, raw) -> object:  # noqa: ANN001
    """입력칸 글자 → 저장할 값. 연도가 숫자가 아니면 ValueError (범위 검사는 update_fields가 한다).

    제목은 줄바꿈·겹친 공백을 한 칸으로(Shift+Enter로 넣은 줄바꿈이 제목에 남지 않게), 저자는 줄마다
    한 명(빈 줄은 버린다), 초록은 문단 줄바꿈을 그대로 둔다.
    """
    s = "" if raw is None else str(raw)
    if key == "authors":
        return [a.strip() for a in s.replace("\r", "").split("\n") if a.strip()]
    if key == "year":
        s = s.strip()
        if not s:
            return None
        if not s.isdigit():
            raise ValueError(t("연도는 숫자로 입력하세요 (예: 2024)."))
        return int(s)
    if key == "title":
        return " ".join(s.split())
    return s.strip()


def same_base(stem: str, base: str) -> bool:
    """폴더 이름이 이미 규칙대로인지 — 같은 이름의 다른 논문이 있어 `X (2)`처럼 유일화된 것도 규칙대로다."""
    return stem == base or re.fullmatch(re.escape(base) + r" \(\d+\)", stem) is not None


def via_subs(pids: list[str], scope: str, sub: dict[str, set[str]]) -> list[str]:
    """상위 컬렉션 `scope`를 볼 때 이 논문이 **하위 컬렉션을 통해서만** 보이면 그 하위 컬렉션들(소속 순서).

    직접 든 논문이거나, 컬렉션 보기가 아니거나(전체·미분류 — `sub`에 없다), 그 하위에도 없으면 []."""
    if scope in pids:
        return []
    under = sub.get(scope) or set()
    return [p for p in pids if p in under and p != scope]


# ----- 디스크에서 읽기 -----

def values(wd: WorkDir) -> dict:
    """카드에 그릴 서지 — 홈 목록 행(`recent_workdirs`)과 같은 출처: 제목은 paper_title, 나머지는 paper_meta."""
    from narani import paper_meta
    from narani.workdir import paper_title

    pm = paper_meta.load(wd) or {}
    return {"title": paper_title(wd.root), "authors": list(pm.get("authors") or []), "year": pm.get("year"),
            "venue": pm.get("venue") or "", "short_title": pm.get("short_title"),
            "doi": effective_doi(wd, pm)}


def effective_doi(wd: WorkDir, meta: dict | None = None) -> str:
    """BibTeX에 실제로 나가는 DOI — 손으로 넣은 값 > 온라인 기록."""
    from narani import bibsource, paper_meta

    meta = paper_meta.load(wd) if meta is None else meta
    return (meta or {}).get("doi") or (bibsource.load(wd) or {}).get("doi") or ""


def collections() -> dict:
    """컬렉션 목록을 한 번 읽어 둔다 — 태그·체크 메뉴가 행마다 파일을 다시 읽지 않게.

    projs · names(id → 이름) · tree(사이드바 순서, (프로젝트, 깊이)) · up(조상) · sub(자신 + 하위 전체)."""
    from narani import projects

    projs = projects.all_projects()
    return {"projs": projs, "names": {p["id"]: p["name"] for p in projs}, "tree": projects.tree(projs),
            "up": {p["id"]: projects.ancestors(p["id"], projs) for p in projs},
            "sub": {p["id"]: {p["id"], *projects.descendants(p["id"], projs)} for p in projs}}


def coll_name(coll: dict, pid: str) -> str:
    return coll["names"].get(pid, t("미분류"))


def coll_path(coll: dict, pid: str) -> str:
    """'SCALE / 리뷰' — 태그 툴팁·메뉴용."""
    chain = [pid, *coll["up"].get(pid, [])]
    return " / ".join(coll_name(coll, p) for p in reversed(chain))


def ai_ready() -> bool:
    """AI 키가 있는지 — config.build_provider()가 RuntimeError를 내는 바로 그 조건 (클라이언트는 만들지 않는다)."""
    from narani import config

    try:
        return bool(config.resolve_key(config.resolve_provider()))
    except Exception:  # noqa: BLE001 — 알 수 없는 제공자 이름 등
        return False


def export_bib_bg(wd: WorkDir) -> None:
    """서지가 바뀌었으면 저장 위치의 references.bib 항목도 — 사본이 놓인 곳마다, 백그라운드로."""
    from nicegui import background_tasks, run

    from narani import library
    from narani.ui import settings_dialog

    if not settings_dialog.any_library_dirs():
        return

    def go() -> None:
        for tgt in library.export_targets(wd):
            try:
                library.export_bib(wd, tgt)
            except Exception:  # noqa: BLE001 — bib 한 곳이 실패해도 서지 저장은 이미 끝났다
                pass

    background_tasks.create(run.io_bound(go), name="md4-export-bib")


async def set_members(wd: WorkDir, pids: list[str], valid) -> int | None:  # noqa: ANN001 — Container[str]
    """논문 한 편의 소속을 pids로 — 저장 위치의 사본도 새 소속에 맞춰 옮기고 정리한다.

    반환: 새 자리에 쓴 사본 수(실패면 None — 알림은 여기서 띄운다). 없는 컬렉션 id는 버린다."""
    from nicegui import run, ui

    from narani import library

    new = list(dict.fromkeys(p for p in pids if p in valid))
    try:
        return len(await run.io_bound(library.set_memberships, wd, new))
    except Exception as ex:  # noqa: BLE001
        ui.notify(t("소속을 바꾸지 못했습니다: {stem} — {ex}", stem=wd.root.stem, ex=ex), type="negative")
        return None


# ----- 그리기 조각 -----

# Enter = 저장(칸을 떠나게 해 blur 한 길로 모은다), Esc = 취소. 한글 조합 중 Enter(isComposing·229)는
# 글자를 확정할 뿐이라 건드리지 않는다. 여러 줄 칸은 Shift+Enter가 줄바꿈.
# Esc는 stopPropagation — 칸이 팝오버(QMenu) 안에 있어도 칸만 취소하고 팝오버는 닫지 않는다
# (Quasar는 window의 keydown에서 Esc를 보고 메뉴를 닫는다).
KEYS_JS = ("(e) => { if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); emit(); return; }"
           " if (e.key === 'Enter' && !e.isComposing && e.keyCode !== 229 && !(%s && e.shiftKey))"
           " { e.preventDefault(); e.target.blur(); } }")


def edit_field(cls: str, key: str, value, placeholder: str,  # noqa: ANN001
               commit: Callable[[str, object], Awaitable[object]], *, multiline: bool = False,
               track: dict | None = None) -> None:
    """누르면 그 자리에서 입력칸이 되는 칸. commit(key, 새 값) → 저장된 값(또는 FAIL).

    값이 같으면 저장하지 않는다. Enter로 저장하면 칸이 사라지며 blur가 한 번 더 올 수 있어
    편집 한 번에 한 번만 끝내도록 done으로 막는다. track을 주면 고치는 동안 그 칸의 '저장하고 끝내기'를
    거기 올려 둔다 — 팝오버가 바깥 클릭으로 닫힐 때 blur가 서버에 오지 않으므로(QMenu 포커스 가두기가
    칸에 포커스를 돌려놓는다) 닫히기 직전에 호출측이 저장한다(MetaCard.flush).
    """
    from nicegui import ui

    box = ui.element("div").classes(f"md4-pv-ed {cls}")
    st = {"value": value, "editing": False}

    def show() -> None:
        st["editing"] = False
        if track is not None:
            track.pop(box.id, None)
        box.classes(remove="editing")
        box.clear()
        text, _ = field_text(key, st["value"])
        with box:
            ui.label(text or placeholder).classes("md4-pv-ed-v" + ("" if text else " ph"))
            ui.icon("edit", size="13px").classes("md4-pv-pen")

    def edit() -> None:
        if st["editing"]:
            return  # 입력칸 안을 누른 클릭이 칸까지 올라온 것
        st["editing"] = True
        done = {"v": False}
        _, cur = field_text(key, st["value"])
        box.classes(add="editing")
        box.clear()
        with box:
            w = ui.textarea(value=cur).props("autogrow") if multiline else ui.input(value=cur)
            w.props("dense borderless autofocus" + (" inputmode=numeric maxlength=4" if key == "year" else "")) \
                .classes("w-full")

        async def finish(save: bool) -> None:
            if done["v"]:
                return
            done["v"] = True
            if save:
                try:
                    new, old = parse_field(key, w.value), parse_field(key, cur)
                except ValueError as ex:
                    ui.notify(str(ex), type="warning")
                    new = old = None
                if new != old:
                    saved = await commit(key, new)
                    if saved is not FAIL:
                        st["value"] = saved
            show()

        if track is not None:
            track[box.id] = lambda: finish(True)
        if not multiline:  # 한 줄 칸(연도·DOI…)은 통째로 바꾸는 일이 많아 전체 선택으로 연다
            w.on("focus", js_handler="(e) => e.target.select()")
        w.on("blur", lambda: finish(True))
        w.on("keydown", lambda: finish(False), js_handler=KEYS_JS % ("true" if multiline else "false"))

    box.on("click", edit)
    show()


def draw_ok(box, doi: str) -> None:  # noqa: ANN001
    from nicegui import ui

    box.clear()
    if doi:
        with box:
            ui.label(t("서지 ✓")).classes("md4-pv-ok").tooltip(t("서지 정보 보강됨 — DOI {doi}", doi=doi))


def paint_tags(box, pids: list[str], coll: dict, *, via=(), scope_name: str = "") -> None:  # noqa: ANN001
    """소속 컬렉션 태그 — 없으면 '미분류'. via(상위 컬렉션 보기에서 하위로만 보이는 소속)는 '↳'로 갈라
    왜 여기 보이는지 알린다(새 요소 없이 글자·class만 — 홈은 카드마다 그리므로 가볍게)."""
    from nicegui import ui

    box.clear()
    via = set(via)
    with box:
        if pids:
            for p in pids:
                if p in via:
                    ui.label("↳ " + coll_name(coll, p)).classes("md4-ptag via") \
                        .tooltip(t("'{v}'에 직접 들지 않고 하위 컬렉션 '{sub}'에서 와서 보입니다",
                                   v=scope_name, sub=coll_path(coll, p)))
                    continue
                tag = ui.label(coll_name(coll, p)).classes("md4-ptag")
                if coll["up"].get(p):  # 하위 컬렉션은 어디 밑인지
                    tag.tooltip(coll_path(coll, p))
        else:
            ui.label(t("미분류")).classes("md4-ptag none")
        ui.icon("add", size="14px").classes("md4-ptag-add")


def open_member_menu(menu, anchor, coll: dict, pids: Callable[[], list[str]],  # noqa: ANN001 — ui.menu, ui.element
                     flip: Callable[[str], Awaitable[bool]], on_new: Callable[[], None],
                     note: Callable[[], str] | None = None) -> None:
    """태그를 누르면 뜨는 체크 메뉴 — 컬렉션마다 넣기/빼기 + 새 컬렉션 만들기. menu를 비우고 채워 anchor에 연다.

    한 칸을 눌러도 메뉴는 닫지 않고 그 칸만 고친다(여러 개를 연달아 고를 수 있게). flip(pid) → 지금
    소속인지. note()는 칸 위 안내 한 줄(홈: 상위 컬렉션 보기에서 하위로만 들어온 논문) — 고칠 때마다 다시 읽는다.
    """
    from nicegui import ui

    async def flip_one(pid: str, icon) -> None:  # noqa: ANN001 — ui.icon
        now_in = await flip(pid)
        icon.name = "check_box" if now_in else "check_box_outline_blank"
        icon.classes(replace="text-primary" if now_in else "text-grey-6")
        paint_note()

    def paint_note() -> None:
        text = note() if note is not None else ""
        note_lbl.text = text
        note_lbl.set_visibility(bool(text))

    menu.clear()
    cur = pids()
    with menu:
        ui.label(t("컬렉션에 넣기")).classes("md4-menucap")
        note_lbl = ui.label().classes("text-xs text-gray-500 q-px-md q-pb-xs").style("max-width:280px")
        paint_note()
        for p, d in coll["tree"]:  # 트리 순서, 하위는 들여 써서
            on = p["id"] in cur
            with ui.menu_item(auto_close=False).props("dense").style(f"padding-left:{16 + 14 * d}px") as item:
                with ui.row().classes("items-center gap-2 no-wrap"):
                    ic = ui.icon("check_box" if on else "check_box_outline_blank", size="18px") \
                        .classes("text-primary" if on else "text-grey-6")
                    ui.label(p["name"])
            item.on("click", lambda _, pid=p["id"], i=ic: flip_one(pid, i))
        if not coll["projs"]:
            ui.label(t("아직 컬렉션이 없습니다")).classes("text-xs text-gray-400 q-px-md q-py-xs")
        ui.separator()
        ui.menu_item(t("새 컬렉션 만들기…"), on_click=on_new).props("dense")
    menu.props(f'target="#c{anchor.id}"')
    menu.open()


def lang_seg(cur: str, native_short: str, on_pick: Callable[[str], None]) -> dict:
    """초록 EN/번역 세그먼트 — 반환: 언어 → span (고른 쪽 표시를 옮기는 데 쓴다)."""
    from nicegui import ui

    spans: dict = {}
    with ui.element("div").classes("md4-seg"):
        for k in ("en", "ko"):
            sp = ui.element("span").classes("on" if k == cur else "")
            with sp:
                ui.label("EN" if k == "en" else native_short)
            sp.on("click", lambda _, k=k: on_pick(k))
            spans[k] = sp
    return spans


def mark_seg(spans: dict, lang: str) -> None:
    for k, span in spans.items():
        span.classes(add="on") if k == lang else span.classes(remove="on")


def set_busy(btn, on: bool) -> None:  # noqa: ANN001 — ui.button
    try:
        btn.props("loading") if on else btn.props(remove="loading")
    except Exception:  # noqa: BLE001 — 그 사이 카드가 다시 그려져 단추가 없어졌다
        pass


class MetaCard:
    """한 논문의 서지 카드 조각들과 그 저장 동작 — 그리는 곳(홈 미리보기·뷰어 팝오버)마다 하나.

    훅 (모두 선택):
    - on_meta(meta): 서지가 저장된 뒤 — 그 화면이 들고 있는 것(목록 행·카드 글자·탭 제목)을 고친다.
      references.bib 내보내기와 이 카드의 이름 힌트·서지 ✓는 여기서 알아서 한다.
    - on_redraw(): '다시 찾기'·'다시 추출'로 여러 칸이 한꺼번에 바뀌었을 때 — 카드를 다시 그린다.
    - before_rename(): 폴더를 옮기기 직전 — 뷰어는 기다리던 메모·편집을 바로 쓴다(옛 경로에 쓰지 않게).
    - on_renamed(old, new): 폴더가 옮겨진 뒤 (tabstore.rename_tab은 이미 했다).
    - on_abs(lang, text): 초록이 저장된 뒤 (홈은 캐시를 고친다).
    host: 대화상자를 지을 자리 — 다시 그려도 지워지지 않는 곳(팝오버·목록 밖). workspace(): 작업 폴더.
    """

    def __init__(self, wd: WorkDir, *, host, workspace: Callable[[], Path],  # noqa: ANN001 — ui.element
                 on_meta: Callable[[dict], None] | None = None, on_redraw: Callable[[], None] | None = None,
                 before_rename: Callable[[], None] | None = None,
                 on_renamed: Callable[[Path, Path], None] | None = None,
                 on_abs: Callable[[str, str], None] | None = None) -> None:
        self.wd = wd
        self.host = host
        self.workspace = workspace
        self.on_meta = on_meta
        self.on_redraw = on_redraw
        self.before_rename = before_rename
        self.on_renamed = on_renamed
        self.on_abs = on_abs
        self.hint_box = None
        self.ok_box = None
        self.editing: dict = {}  # 고치는 중인 칸 → 저장하고 끝내기 (edit_field track)

    async def flush(self) -> None:
        """고치는 중인 칸을 모두 저장하고 끝낸다 — 칸 밖 클릭과 같은 저장(값이 같으면 쓰지 않는다)."""
        for done in list(self.editing.values()):
            await done()

    # ----- 저장 -----

    def changed(self, meta: dict) -> None:
        """서지가 바뀐 뒤 — 화면 훅 · 이름 힌트 · 서지 ✓ · references.bib(백그라운드)."""
        if self.on_meta is not None:
            self.on_meta(meta)
        self.paint_hint()
        if self.ok_box is not None:
            try:
                draw_ok(self.ok_box, effective_doi(self.wd, meta))
            except Exception:  # noqa: BLE001 — 그 사이 카드가 다시 그려졌다
                pass
        export_bib_bg(self.wd)

    async def commit(self, key: str, value):  # noqa: ANN001, ANN201 — 저장된 값 | FAIL
        from nicegui import run, ui

        from narani import paper_meta

        wd = self.wd
        try:
            async with META_LOCK:
                meta = await run.io_bound(paper_meta.update_fields, wd, {key: value})
        except ValueError as ex:  # 빈 제목·범위 밖 연도 등 — 칸은 옛 값으로
            ui.notify(t("저장하지 않았습니다 — {ex}", ex=ex), type="warning")
            return FAIL
        except Exception as ex:  # noqa: BLE001
            ui.notify(t("저장하지 못했습니다: {ex}", ex=ex), type="negative")
            return FAIL
        self.changed(meta)
        ui.notify(t("저장됨"), type="positive", timeout=1200)
        if key == "doi":
            return meta.get("doi") or effective_doi(wd, meta)
        return meta.get(key)

    async def commit_abs(self, lang: str, text: str):  # noqa: ANN201 — 저장된 값 | FAIL
        from nicegui import run, ui

        from narani import preview

        try:
            await run.io_bound(preview.set_abstract, self.wd, lang, text)
        except Exception as ex:  # noqa: BLE001
            ui.notify(t("초록을 저장하지 못했습니다: {ex}", ex=ex), type="negative")
            return FAIL
        if self.on_abs is not None:
            self.on_abs(lang, text)
        ui.notify(t("초록 저장됨"), type="positive", timeout=1200)
        return text

    # ----- 그리기 -----

    def fields(self, vals: dict) -> None:
        """제목·저자(여러 줄 칸, 저자는 한 줄에 한 명) + 연도·venue·DOI·약칭(이름표 | 값 두 칸).

        약칭은 폴더 이름 규칙의 {title}."""
        from nicegui import ui

        with ui.column().classes("gap-1 w-full"):
            edit_field("md4-pv-title w-full", "title", vals.get("title"), t("제목 없음 — 눌러서 입력"), self.commit,
                       multiline=True, track=self.editing)
            edit_field("md4-pv-auth w-full", "authors", vals.get("authors") or [],
                       t("저자 없음 — 눌러서 입력 (한 줄에 한 명)"), self.commit, multiline=True, track=self.editing)
        with ui.element("div").classes("md4-pv-kv w-full"):
            for key, label, tip in (
                    ("year", t("연도"), ""),
                    ("venue", "venue", t("학회·저널 이름")),
                    ("doi", "DOI", t("BibTeX·온라인 조회에 쓰는 DOI — https://doi.org/ 접두는 떼어 저장합니다")),
                    ("short_title", t("약칭"),
                     t("파일·폴더 이름에 쓰는 제목 약칭(이름 규칙의 {title}) — 비우면 제목에서 만듭니다"))):
                k = ui.label(label).classes("md4-pv-k")
                if tip:
                    k.tooltip(tip)
                edit_field("md4-pv-v", key, vals.get(key), t("비어 있음 — 눌러서 입력"), self.commit, track=self.editing)

    def hint(self) -> None:
        """이름 규칙 힌트 자리 — 서지·소속(주 컬렉션의 이름 규칙)이 바뀌면 paint_hint로 다시."""
        from nicegui import ui

        self.hint_box = ui.element("div").classes("md4-pv-hint w-full")
        self.paint_hint()

    def ok(self, doi: str) -> None:
        from nicegui import ui

        self.ok_box = ui.element("div")
        draw_ok(self.ok_box, doi)

    def paint_hint(self) -> None:
        """이름 규칙대로면 폴더 이름이 달라질 때만 한 줄 — 서지를 고친 뒤 폴더도 맞출 수 있게."""
        from nicegui import ui

        from narani import paper_meta

        box = self.hint_box
        if box is None:
            return
        try:
            box.clear()
        except Exception:  # noqa: BLE001 — 그 사이 카드가 다시 그려졌다
            return
        base = paper_meta.naming_base_for(self.wd)
        if not base or same_base(self.wd.root.stem, base):
            box.set_visibility(False)
            return
        box.set_visibility(True)
        with box:
            ui.label(t("이름 규칙대로면"))
            ui.label(base).classes("md4-pv-hint-n")
            ui.button(t("폴더 이름 바꾸기"), on_click=lambda: self.confirm_rename(base)) \
                .props("flat dense no-caps color=primary")

    def abstract(self, box, text, lang: str) -> None:  # noqa: ANN001
        """초록 칸 — 눌러서 고치면 preview.json에 사용자 값으로 고정한다(md는 건드리지 않는다)."""
        box.clear()
        with box:
            edit_field("md4-pv-abs w-full", "abstract", text or "", t("초록을 찾지 못했습니다 — 눌러서 입력"),
                       lambda _k, v: self.commit_abs(lang, v), multiline=True, track=self.editing)

    def redo_buttons(self) -> None:
        """서지 다시 받기 — 손으로 고친 칸은 어느 쪽도 덮지 않는다(user_fields)."""
        from nicegui import ui

        online = ui.button(t("온라인에서 다시 찾기"), icon="travel_explore", on_click=lambda: self.redo_online(online)) \
            .props("flat dense no-caps size=sm color=grey-8") \
            .tooltip(t("OpenAlex·Crossref 등에서 출판 기록(BibTeX용)을 새로 받습니다 — 손으로 고친 칸은 그대로"))
        ok = ai_ready()
        # 툴팁은 감싼 span에 — 비활성 <button>은 마우스 이벤트를 받지 않아 '왜 꺼졌는지'가 안 뜬다
        with ui.element("span").tooltip(
                t("본문 앞부분에서 제목·저자·연도·venue를 AI로 다시 뽑습니다 — 손으로 고친 칸은 그대로")
                if ok else t("AI 키가 없어 쓸 수 없습니다 — ⚙ 설정 → AI에서 키를 넣으세요")):
            ai = ui.button(t("AI로 다시 추출"), icon="auto_awesome", on_click=lambda: self.redo_ai(ai)) \
                .props("flat dense no-caps size=sm color=grey-8")
        if not ok:
            ai.disable()

    # ----- 다시 받기 -----

    async def redo_online(self, btn) -> None:  # noqa: ANN001
        """'온라인에서 다시 찾기' — 출판 기록을 새로 받는다(손으로 고친 칸은 기록 위에 다시 덮인다)."""
        from nicegui import run, ui

        from narani import bibsource, config, paper_meta

        root, wd = str(self.wd.root), self.wd
        if root in BUSY:
            ui.notify(t("이 논문의 서지를 이미 다시 받는 중입니다."), type="info", timeout=1500)
            return
        BUSY.add(root)
        set_busy(btn, True)
        try:
            rec = await run.io_bound(bibsource.refetch, wd, mailto=config.resolve_enrich_mailto() or None)
        except Exception as ex:  # noqa: BLE001 — 오프라인·레이트리밋
            ui.notify(t("온라인 조회에 실패했습니다: {ex}", ex=ex), type="negative")
            return
        finally:
            BUSY.discard(root)
            set_busy(btn, False)
        if not rec:
            ui.notify(t("출판 기록을 찾지 못했습니다 — 제목·저자·연도를 고친 뒤 다시 찾아 보세요."), type="info")
            return
        bits = [str(x) for x in (rec.get("venue"), rec.get("year")) if x]
        if rec.get("doi"):
            bits.append(f"DOI {rec['doi']}")
        ui.notify(t("출판 기록을 받았습니다") + (" — " + " · ".join(bits) if bits else ""), type="positive")
        self.changed(paper_meta.load(wd) or {})
        if self.on_redraw is not None:
            self.on_redraw()  # DOI 칸·서지 ✓

    async def redo_ai(self, btn) -> None:  # noqa: ANN001
        """'AI로 다시 추출' — 앞부분에서 서지를 다시 뽑는다(손으로 고친 칸은 그대로)."""
        from nicegui import run, ui

        from narani import config, paper_meta

        root, wd = str(self.wd.root), self.wd
        if root in BUSY:
            ui.notify(t("이 논문의 서지를 이미 다시 받는 중입니다."), type="info", timeout=1500)
            return
        try:
            provider = config.build_provider()
        except RuntimeError as ex:
            ui.notify(str(ex), type="warning")
            return
        BUSY.add(root)
        set_busy(btn, True)
        try:
            async with META_LOCK:
                meta = await run.io_bound(paper_meta.reextract, wd, provider)
        except Exception as ex:  # noqa: BLE001
            ui.notify(t("서지를 다시 뽑지 못했습니다: {ex}", ex=ex), type="negative")
            return
        finally:
            BUSY.discard(root)
            set_busy(btn, False)
        if not meta:
            ui.notify(t("본문 앞부분이 비어 있어 서지를 뽑지 못했습니다."), type="warning")
            return
        self.changed(meta)
        ui.notify(t("AI로 서지를 다시 뽑았습니다 — 손으로 고친 칸은 그대로 두었습니다."), type="positive")
        if self.on_redraw is not None:
            self.on_redraw()

    # ----- 폴더 이름 -----

    def confirm_rename(self, base: str) -> None:
        from nicegui import run, ui

        from narani import paper_meta
        from narani.ui import jobs, tabstore

        old = Path(self.wd.root)
        running = {str(Path(j.root).resolve()) for j in jobs.running()}
        if str(old.resolve()) in running:  # 번역 등이 이 폴더에 쓰는 중 — 옮기면 그 작업이 길을 잃는다
            ui.notify(t("이 논문에서 작업(번역 등)이 도는 중입니다 — 끝난 뒤에 바꾸세요."), type="warning")
            return

        async def do_rename() -> None:
            dlg.close()
            if self.before_rename is not None:
                self.before_rename()
            try:
                new_wd = await run.io_bound(paper_meta.rename_to_naming, WorkDir(old), self.workspace())
            except Exception as ex:  # noqa: BLE001
                ui.notify(t("폴더 이름을 바꾸지 못했습니다: {ex}", ex=ex), type="negative")
                return
            new = Path(new_wd.root)
            if new == old:
                ui.notify(t("이미 이름 규칙대로입니다."), type="info")
                if self.on_redraw is not None:
                    self.on_redraw()
                return
            tabstore.rename_tab(old, new)  # rename_workdir도 부르지만, 두 번이어도 무해(옛 키가 없으면 그냥 끝)
            self.wd = WorkDir(new)
            if self.on_renamed is not None:
                self.on_renamed(old, new)
            ui.notify(t("폴더 이름을 바꿨습니다 → {stem}", stem=new.stem), type="positive")

        with self.host, ui.dialog() as dlg, ui.card().classes("gap-2").style("max-width:440px"):
            ui.label(t("폴더 이름을 이름 규칙대로 바꿀까요?")).classes("font-bold")
            with ui.column().classes("gap-0 w-full"):
                ui.label(old.stem).classes("text-sm text-gray-500 break-all")
                ui.label("→ " + base).classes("text-sm break-all")
            ui.label(t("폴더·PDF·저장 위치 사본·열린 탭이 함께 옮겨집니다.")).classes("text-xs text-gray-500")
            with ui.row().classes("justify-end w-full items-center gap-2"):
                ui.button(t("취소"), on_click=dlg.close).props("flat")
                ui.button(t("이름 바꾸기"), icon="drive_file_rename_outline", on_click=do_rename) \
                    .props("unelevated no-caps color=primary")
        dlg.open()
