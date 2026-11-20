"""macOS 메뉴 막대 — 앱 창 모드에서 Narani · 파일 · 편집 · 보기 · 이동 · 도움말.

세 층으로 나뉜다.

1. **메뉴 정의**(`menus()`) — 항목·단축키·명령 이름만 담은 데이터. 테스트가 이것을 본다.
2. **웹뷰 프로세스**(`webview_started` → `_install_menu`) — 앱 창은 NiceGUI가 띄운 별도 프로세스의
   pywebview(WKWebView)라서 메뉴도 그 프로세스에서 PyObjC로 NSMenu를 짓는다. pywebview 6의 `menu=`는
   단축키를 붙일 수 없어(MenuAction에 keyEquivalent가 없다) 쓰지 않는다. `menu=`를 넘기지 않으면
   pywebview는 창이 key가 될 때 메뉴를 다시 깔지 않으므로(`cocoa.py` windowDidBecomeKey_) 우리 메뉴가 남는다.
   본 창은 `ui.run(native=True)`의 `start_args["func"]`(§ui/desktop.py configure), 형제 창은
   `python -m narani.ui.window`가 `webview.start(func=...)`로 부른다 — 프로세스마다 메뉴가 하나씩이다.
3. **페이지**(`DISPATCH_JS`의 `window.md4Menu(cmd, attempt)`) — 메뉴를 누르면 key 창의 WKWebView에
   `evaluateJavaScript`로 이 함수를 부른다. 화면 안에서 끝나는 일(탭 옮기기·찾기·에이전트 창·두 뷰어)은
   기존 단추·JS API를 그대로 쓰고, 서버가 할 일(내보내기·새 창·Finder 등)은 `emitEvent('md4-menu')`로
   보낸다. 서버는 셸 페이지가 한 번 단 `ui.on` 하나로 받고(`install_page`), 할 일은 지금 화면(홈·뷰어)이
   그릴 때마다 통째로 갈아 끼우는 등록부(`register`)에서 찾는다 — 화면마다 `ui.on`을 달면 탭을 오갈수록
   핸들러가 쌓여 한 번 눌러 여러 번 돈다.

키 처리는 한 곳에서만: ⌘+/−/0은 §ui/page_zoom.py의 NSEvent 모니터가 메뉴보다 먼저 먹으므로 메뉴의 확대
항목은 단축키를 **보여 주기만** 하고, 눌렀을 때는 같은 `page_zoom.zoom`을 부른다. 메뉴에 단축키가 있는
키는 **메뉴가 먼저** 먹고 페이지 keydown까지 가지 않는다(실제 창에서 확인) — 그래서 ⌘F는 찾기 바의 keydown 대신
메뉴 → `__md4Find.open()`으로 한 번만 돈다. 메뉴에 없는 키(⌘G·⌥←·편집기의 ⌘B 등)는 예전처럼 페이지가 받는다.
그래서 ⌘Z·⇧⌘Z는 메뉴에 키를 달지 않는다(편집기들이 자기 되돌리기 기록으로 받아야 한다 — menus()의 주석).

메뉴 글자는 앱을 켤 때의 화면 언어다(t()). 설정에서 언어를 바꾸면 메뉴는 다시 켜야 바뀐다.
macOS가 아니거나 브라우저 모드면 아무것도 하지 않는다.
"""

from __future__ import annotations

import inspect
import json
import sys
from dataclasses import dataclass

from narani.i18n import N_, t

EVENT = "md4-menu"          # 페이지 → 서버 이벤트 이름
RETRIES = 12                # 홈으로 옮긴 뒤 다시 부르는 횟수(0.35초 간격) — 'again'
RETRY_DELAY = 0.35
_REG = "_md4_menu"          # 클라이언트에 붙이는 지금 화면의 등록부


@dataclass(frozen=True)
class Item:
    """메뉴 항목 하나.

    kind: page = 페이지 디스패처(`md4Menu`)로 · zoom = page_zoom을 직접 · sel = AppKit 기본 동작
    (cmd가 셀렉터, 대상은 응답 체인) · services = 서비스 하위 메뉴 · sep = 구분선.
    key는 keyEquivalent 한 글자 — Shift가 붙는 글자 단축키는 Cocoa 관례대로 **대문자**로 쓴다('H' = ⇧⌘H;
    'h'에 shift 마스크를 주면 실제 키 입력이 ⌘H 항목과 엉킨다). mods는 'cmd'·'alt'·'ctrl'(·'shift')를 +로 이은 것.
    """

    cmd: str
    title: str = ""
    key: str = ""
    mods: str = "cmd"
    kind: str = "page"
    n: int = 0              # 제목 자리표시 {n} (탭 번호)


SEP = Item("", kind="sep")

APP, FILE, EDIT, VIEW, GO, HELP = (N_("Narani"), N_("파일"), N_("편집"), N_("보기"), N_("이동"), N_("도움말"))

# 페이지 디스패처가 안 받는 명령 — 웹뷰 프로세스가 직접 page_zoom을 부른다
ZOOM = {"zoom_in": "+", "zoom_out": "-", "zoom_reset": "0"}


def menus() -> list[tuple[str, list[Item]]]:
    """메뉴 막대 전체 — (메뉴 제목 키, 항목들). 제목·항목 글자는 카탈로그 키(한국어 원문)다."""
    tabs = [Item(f"tab_{i}", N_("탭 {n}"), str(i), n=i) for i in range(1, 9)]
    return [
        (APP, [
            Item("about", N_("Narani에 관하여")),
            SEP,
            Item("settings", N_("설정…"), ","),
            Item("check_update", N_("업데이트 확인…")),
            SEP,
            Item("", N_("서비스"), kind="services"),
            SEP,
            Item("hide:", N_("Narani 가리기"), "h", kind="sel"),
            Item("hideOtherApplications:", N_("기타 가리기"), "h", "cmd+alt", kind="sel"),
            Item("unhideAllApplications:", N_("모두 보기"), kind="sel"),
            SEP,
            Item("terminate:", N_("Narani 종료"), "q", kind="sel"),
        ]),
        (FILE, [
            Item("add_pdf", N_("PDF 추가…"), "o"),
            Item("new_window", N_("새 창"), "n"),
            Item("close_tab", N_("탭 닫기"), "w"),
            Item("close_other_tabs", N_("다른 탭 모두 닫기"), "w", "cmd+alt"),
            SEP,
            Item("export", N_("내보내기…")),
            Item("reveal", N_("Finder에서 보기")),
        ]),
        (EDIT, [
            # ⌘Z·⇧⌘Z는 메뉴에 달지 않는다 — 달면 WebKit 되돌리기 목록이 비어 있어도(항목이 흐려도) 메뉴가 키를 먹어
            # CodeMirror·Crepe·에이전트 입력칸이 자기 기록으로 되돌리지 못한다(⌘B 뒤 ⌘Z가 먹통, 실제 창에서 확인).
            # 항목은 남겨 마우스로 WebKit 되돌리기를 쓸 수 있게 한다.
            Item("undo:", N_("실행 취소"), kind="sel"),
            Item("redo:", N_("다시 실행"), kind="sel"),
            SEP,
            Item("cut:", N_("오려두기"), "x", kind="sel"),
            Item("copy:", N_("복사하기"), "c", kind="sel"),
            Item("paste:", N_("붙여넣기"), "v", kind="sel"),
            Item("selectAll:", N_("전체 선택"), "a", kind="sel"),
            SEP,
            Item("find", N_("찾기…"), "f"),
        ]),
        (VIEW, [
            Item("zoom_in", N_("확대"), "+", kind="zoom"),
            Item("zoom_out", N_("축소"), "-", kind="zoom"),
            Item("zoom_reset", N_("실제 크기"), "0", kind="zoom"),
            SEP,
            Item("agent", N_("에이전트 창 열기/닫기")),
            Item("split", N_("위아래 두 뷰어")),
            Item("swap_lang", N_("원문/번역 전환")),
            SEP,
            Item("toggleFullScreen:", N_("전체 화면"), "f", "cmd+ctrl", kind="sel"),
        ]),
        (GO, [
            Item("home", N_("홈"), "H"),  # ⇧⌘H — 대문자 키가 Shift다('h'+shift 마스크면 ⌘H 가리기와 엉킨다)
            Item("jump_back", N_("읽던 자리로")),
            SEP,
            *tabs,
            Item("tab_9", N_("마지막 탭"), "9"),
        ]),
        (HELP, [
            Item("shortcuts", N_("단축키 목록"), "/"),
        ]),
    ]


def items() -> list[Item]:
    """구분선을 뺀 모든 항목 (메뉴 순서대로)."""
    return [it for _, its in menus() for it in its if it.kind != "sep"]


def page_commands() -> set[str]:
    """페이지 디스패처로 가는 명령 이름들."""
    return {it.cmd for it in items() if it.kind == "page"}


def title_of(it: Item) -> str:
    return t(it.title, n=it.n) if it.n else t(it.title)


_MOD_FLAGS = {"ctrl": 1 << 18, "alt": 1 << 19, "shift": 1 << 17, "cmd": 1 << 20}  # NSEventModifierFlag*
_MOD_GLYPH = (("ctrl", "⌃"), ("alt", "⌥"), ("shift", "⇧"), ("cmd", "⌘"))       # macOS 표기 순서


def mod_mask(mods: str) -> int:
    """'cmd+shift' → NSEventModifierFlags 비트."""
    out = 0
    for m in filter(None, mods.split("+")):
        out |= _MOD_FLAGS[m]
    return out


def shortcut_label(it: Item) -> str:
    """메뉴에 보이는 단축키 표기 — '⇧⌘H'. 단축키가 없으면 ''."""
    if not it.key:
        return ""
    on = set(it.mods.split("+"))
    if it.key.isalpha() and it.key.isupper():
        on.add("shift")  # 대문자 키 = Shift
    return "".join(g for m, g in _MOD_GLYPH if m in on) + it.key.upper()


def call_js(cmd: str, attempt: int = 0) -> str:
    """웹뷰 프로세스가 key 창에 보내는 JS — 디스패처가 아직 없으면(페이지가 뜨는 중) 'none'."""
    return f"window.md4Menu ? window.md4Menu({json.dumps(cmd)}, {int(attempt)}) : 'none'"


# ---------------------------------------------------------------- 페이지 디스패처 (셸·에이전트 단독 창 head)
# 돌려주는 값: 'ok' 처리함 · 'server' 서버로 보냄 · 'again' 홈으로 옮겼으니 잠시 뒤 다시 불러 달라
# (홈 화면을 그린 뒤에 그 단추를 누른다 — 서버를 거쳐 누르면 사용자 제스처가 끊겨 파일 고르기 창이 막힌다) ·
# 'close-window' 이 창을 닫아 달라(에이전트 단독 창의 ⌘W).
DISPATCH_JS = """
<script>
window.md4Menu = function(cmd, attempt){
  attempt = attempt || 0;
  var SA = !!window.__md4AgentStandalone;
  function q(s){ return document.querySelector(s); }
  function srv(c, extra){
    var a = {cmd: c, path: location.pathname + location.search};
    for (var k in (extra || {})) a[k] = extra[k];
    if (window.emitEvent) emitEvent('%(event)s', a);
    return 'server';
  }
  function na(){ return srv('na', {what: cmd}); }
  function goHome(){                          // 셸의 홈 탭 — 없으면(단독 창) false
    var h = q('.md4-tab-home');
    if (!h) return false;
    if (!h.classList.contains('on')) h.click();
    return true;
  }
  function later(){ return attempt >= %(retries)d ? na() : (goHome() ? 'again' : na()); }
  var m = /^tab_(\\d)$/.exec(cmd);
  if (m){                                     // 헤더의 논문 탭 순서 — 9는 마지막 탭(브라우저 관례)
    var ts = Array.prototype.slice.call(document.querySelectorAll('.md4-tabstrip .md4-tab:not(.md4-tab-home)'));
    var n = +m[1], el = n === 9 ? ts[ts.length - 1] : ts[n - 1];
    if (!el) return na();
    if (!el.classList.contains('on')) el.click();
    return 'ok';
  }
  switch (cmd){
    case 'home': return goHome() ? 'ok' : na();
    case 'close_tab':
      var x = q('.md4-tab.on .md4-tab-x');
      if (x){ x.click(); return 'ok'; }
      return SA ? 'close-window' : na();
    case 'close_other_tabs':                  // 탭 줄이 그려 둔 숨은 자리 — 다른 논문 탭이 있을 때만 있다
      var o = q('.md4-tab-others');
      if (o){ o.click(); return 'ok'; }
      return na();
    case 'find':
      if (window.__md4Find){ window.__md4Find.open(); return 'ok'; }
      var s = q('[data-md4menu="search"] input') || q('input[data-md4menu="search"]');
      if (s){ s.focus(); s.select(); return 'ok'; }
      return na();
    case 'agent':
      if (!SA && window.__md4Agent){ window.__md4Agent.toggle(); return 'ok'; }
      return na();
    case 'split':
      if (window.__md4Split && q('.md4-split-btn')){ window.__md4Split.toggle(); return 'ok'; }
      return na();
    case 'jump_back':
      var J = window.__mdJumpBack;
      if (J && J.depth && J.depth()){ J.back(); return 'ok'; }
      return na();
    case 'settings':
      var b = q('[data-md4menu="settings"]');
      if (b){ b.click(); return 'ok'; }
      return later();
    case 'add_pdf':
      var f = q('.md4-upload input[type=file]');
      if (f){ f.click(); return 'ok'; }
      return later();
    default: return srv(cmd);
  }
};
</script>
""" % {"event": EVENT, "retries": RETRIES}


# ---------------------------------------------------------------- 서버 (NiceGUI)
# 해당 없는 화면에서 고른 항목 — 짧은 안내. 여기 없는 명령은 '<항목> — 이 화면에서는 쓸 수 없습니다'.
_NA = {
    "jump_back": N_("돌아갈 읽던 자리가 없습니다."),
    "close_tab": N_("닫을 논문 탭이 없습니다."),
    "split": N_("위아래 두 뷰어는 논문 본문을 읽을 때 쓸 수 있습니다."),
    "swap_lang": N_("원문/번역 전환은 논문을 볼 때 쓸 수 있습니다."),
    "find": N_("찾기는 논문 화면에서 쓸 수 있습니다."),
}


def enabled() -> bool:
    """메뉴 막대를 쓰는 환경인지 — macOS 앱 창 모드."""
    from narani.ui import desktop

    return sys.platform == "darwin" and desktop.active()


def register(**handlers) -> None:  # noqa: ANN003 — 명령 이름 → 함수(동기·비동기)
    """지금 화면(홈·뷰어)이 그릴 때 부른다 — 이 창의 메뉴 명령이 부를 함수들을 **통째로** 갈아 끼운다."""
    from nicegui import context

    setattr(context.client, _REG, handlers)


def na_message(what: str) -> str:
    """해당 없는 화면에서 고른 항목의 안내 문구."""
    if what.startswith("tab_"):
        return t("그 번호의 탭이 없습니다.")
    if what in _NA:
        return t(_NA[what])
    it = next((i for i in items() if i.cmd == what), None)
    return t("{name} — 이 화면에서는 쓸 수 없습니다.", name=title_of(it) if it else what)


def install_page(state: dict) -> None:
    """셸 페이지·에이전트 단독 창이 **한 번** 부른다 — 디스패처 JS와 서버 쪽 이벤트 하나."""
    from nicegui import context, ui

    if not enabled():
        return
    ui.add_head_html(DISPATCH_JS)
    client = context.client

    async def on_menu(e) -> None:  # noqa: ANN001 — GenericEventArguments
        args = e.args if isinstance(e.args, dict) else {}
        await handle(client, state, str(args.get("cmd") or ""), str(args.get("what") or ""))

    ui.on(EVENT, on_menu)


async def handle(client, state: dict, cmd: str, what: str = "") -> None:  # noqa: ANN001 — nicegui Client
    """페이지가 보낸 명령 하나 — 지금 화면의 등록부 > 앱 공통 > 안내."""
    from nicegui import ui

    if cmd == "na":
        ui.notify(na_message(what), type="info", timeout=2200)
        return
    fn = (getattr(client, _REG, None) or {}).get(cmd)
    if fn is None:
        common = {"about": lambda: about_dialog(), "check_update": lambda: check_update(state),
                  "shortcuts": lambda: shortcuts_dialog()}
        fn = common.get(cmd)
    if fn is None:
        ui.notify(na_message(cmd), type="info", timeout=2200)
        return
    r = fn()
    if inspect.isawaitable(r):
        await r


def _dialog():  # noqa: ANN202 — 닫으면 지워지는 대화상자
    from nicegui import ui

    dlg = ui.dialog()
    dlg.on("hide", lambda: dlg.delete())
    return dlg


def about_dialog() -> None:
    from nicegui import ui

    from narani import updater
    from narani.ui import desktop

    with _dialog() as dlg, ui.card().classes("items-center q-pa-lg").style("min-width:320px"):
        if desktop.ICON.is_file():
            ui.image(str(desktop.ICON)).style("width:72px;height:72px")
        ui.label("Narani").classes("text-h6")
        ui.label(updater.version_label()).classes("text-xs text-grey-7")
        ui.label(t("영어 논문 PDF를 마크다운으로 바꾸고 모국어 번역을 나란히 보여 줍니다.")) \
            .classes("text-sm text-center").style("max-width:300px")
        ui.link("github.com/wooogler/narani", "https://github.com/wooogler/narani", new_tab=True).classes("text-sm")
        ui.button(t("닫기"), on_click=dlg.close).props("flat no-caps")
    dlg.open()


async def check_update(state: dict) -> None:
    """'업데이트 확인…' — 지금 GitHub을 보고, 새 버전이면 업데이트 대화상자를 띄운다."""
    from nicegui import run, ui

    from narani import updater
    from narani.ui import update_banner

    src = state["update_src"] if "update_src" in state else updater.installed_source()
    if src is None:
        ui.notify(t("이 설치본은 업데이트를 확인하지 않습니다 (개발본·pip 설치)."), type="info")
        return
    state["update_src"] = src
    ui.notify(t("업데이트 확인 중…"), type="info", timeout=1500)
    try:
        upd = await run.io_bound(updater.check, src) or False
    except Exception:  # noqa: BLE001 — 오프라인·API 한도
        ui.notify(t("업데이트 확인 실패 (오프라인?)"), type="warning")
        return
    state["update"], state["update_error"] = upd, False
    if not upd:
        ui.notify(t("최신 버전입니다."), type="positive")
        return
    with _dialog() as dlg, ui.card().style("min-width:340px;max-width:440px"):
        ui.label(t("새 버전이 있습니다 ({n}개 변경)", n=upd.ahead) if upd.ahead else t("새 버전이 있습니다")) \
            .classes("font-bold")
        if upd.messages:
            with ui.element("ul").classes("text-xs text-grey-7").style("margin:4px 0 0 16px;max-height:160px;overflow:auto"):
                for m in upd.messages[:8]:
                    with ui.element("li"):
                        ui.label(m)
        with ui.row().classes("justify-end gap-1 w-full"):
            ui.button(t("나중에"), on_click=dlg.close).props("flat no-caps")
            # 대화상자를 닫지 않는다 — apply가 이 슬롯에 앱 종료 타이머를 다는데, 닫으면(hide → delete) 타이머도 지워진다
            ui.button(t("업데이트하고 다시 시작"), icon="system_update",
                      on_click=lambda: update_banner.apply(state)) \
                .props("unelevated no-caps color=indigo")
    dlg.open()


def shortcut_rows() -> list[tuple[str, list[tuple[str, str]]]]:
    """단축키 목록 — 메뉴의 단축키 + 화면 안에서만 듣는 키. [(묶음 제목, [(키, 설명)])]."""
    out = []
    for title, its in menus():
        rows = [(shortcut_label(it), title_of(it)) for it in its if it.key and it.kind != "sep"]
        if rows:
            out.append((t(title), rows))
    out.append((t("본문"), [
        ("⌘G / ⇧⌘G", t("다음·이전 찾기")),
        ("⌥←", t("읽던 자리로")),
        ("⌥Space", t("듣기 재생·멈춤")),
        ("⌥[ / ⌥]", t("듣기 — 이전·다음 섹션")),
        ("Esc", t("찾기·그림 확대·팝업 닫기")),
    ]))
    return out


def shortcuts_dialog() -> None:
    from nicegui import ui

    from narani.ui import md_keys

    with _dialog() as dlg, ui.card().style("min-width:380px;max-width:520px"):
        ui.label(t("단축키 목록")).classes("text-h6")
        with ui.element("div").classes("md4-scroll").style("max-height:64vh;overflow:auto;width:100%"):
            for title, rows in shortcut_rows():
                ui.label(title).classes("text-xs text-grey-7 q-mt-sm")
                for key, label in rows:
                    with ui.row().classes("items-center no-wrap gap-3 w-full").style("padding:1px 0"):
                        ui.label(key).classes("text-sm").style("min-width:96px;font-family:-apple-system,system-ui")
                        ui.label(label).classes("text-sm")
            ui.label(t("편집기")).classes("text-xs text-grey-7 q-mt-sm")
            for line in md_keys.shortcut_help(mac=True).splitlines():
                key, _, label = line.partition("  ")
                with ui.row().classes("items-center no-wrap gap-3 w-full").style("padding:1px 0"):
                    ui.label(key).classes("text-sm").style("min-width:96px")
                    ui.label(label).classes("text-sm")
        ui.label(t("메뉴 글자는 앱을 다시 켜면 바뀐 화면 언어로 나옵니다.")).classes("text-xs text-grey-6 q-mt-sm")
        with ui.row().classes("justify-end w-full"):
            ui.button(t("닫기"), on_click=dlg.close).props("flat no-caps")
    dlg.open()


# ---------------------------------------------------------------- 웹뷰 프로세스 (AppKit)
_NATIVE: dict = {}


def webview_started() -> None:
    """`webview.start(func=...)`로 웹뷰 프로세스에서 불린다(별도 스레드) — 확대 키와 메뉴 막대를 설치한다.

    pywebview의 start는 func을 하나만 받으므로 page_zoom도 여기서 부른다.
    """
    from narani.ui import page_zoom

    page_zoom.webview_started()
    if sys.platform != "darwin":
        return
    try:
        from PyObjCTools import AppHelper
    except ImportError:
        return
    # 메인 스레드의 런 루프가 돌기 시작한 뒤에 돈다 — pywebview가 first_show에서 기본 메뉴를 깐 다음이다
    AppHelper.callAfter(_install_menu)


def _target():  # noqa: ANN202 — 우리 항목들의 action을 받는 NSObject (프로세스에 하나)
    if "target" not in _NATIVE:
        import objc
        from Foundation import NSObject

        class NaraniMenuTarget(NSObject):
            @objc.IBAction
            def menuAction_(self, sender):  # noqa: ANN001, ANN202, N802
                run(str(sender.representedObject() or ""))

        _NATIVE["target"] = NaraniMenuTarget.alloc().init()
    return _NATIVE["target"]


def _ns_item(it: Item):  # noqa: ANN202
    import AppKit

    if it.kind == "sep":
        return AppKit.NSMenuItem.separatorItem()
    title = title_of(it)
    if it.kind == "services":
        mi = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, None, "")
        services = AppKit.NSMenu.alloc().initWithTitle_(title)
        mi.setSubmenu_(services)
        AppKit.NSApp.setServicesMenu_(services)
        return mi
    if it.kind == "sel":  # 응답 체인으로 (대상 없음) — WKWebView·NSApp이 받는다
        mi = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, it.cmd, it.key)
    else:
        mi = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, "menuAction:", it.key)
        mi.setTarget_(_target())
        mi.setRepresentedObject_(it.cmd)
    if it.key:
        mi.setKeyEquivalentModifierMask_(mod_mask(it.mods))
    return mi


def build_ns_menu():  # noqa: ANN201 — NSMenu
    """menus()로 NSMenu(메인 메뉴)를 짓는다."""
    import AppKit

    main = AppKit.NSMenu.alloc().initWithTitle_("MainMenu")
    for title, its in menus():
        top = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(t(title), None, "")
        sub = AppKit.NSMenu.alloc().initWithTitle_(t(title))
        top.setSubmenu_(sub)
        main.addItem_(top)
        for it in its:
            sub.addItem_(_ns_item(it))
        if title == HELP:
            AppKit.NSApp.setHelpMenu_(sub)
    return main


def _install_menu() -> None:
    import AppKit

    try:
        menu = build_ns_menu()
    except Exception as e:  # noqa: BLE001 — 메뉴는 부가 기능: 실패해도 창은 떠 있어야 한다
        print(f"메뉴 막대를 만들지 못했습니다: {e}", file=sys.stderr)
        return
    _NATIVE["menu"] = menu
    AppKit.NSApp.setMainMenu_(menu)

    # 혹시 pywebview가 다시 깔았으면(첫 창이 늦게 떠 first_show가 뒤에 온 경우) 한 번 더
    from PyObjCTools import AppHelper

    def again() -> None:
        if AppKit.NSApp.mainMenu() is not _NATIVE.get("menu"):
            AppKit.NSApp.setMainMenu_(_NATIVE["menu"])

    AppHelper.callLater(1.5, again)


def _key_window():  # noqa: ANN202 — 웹뷰가 든 창: key > main > 아무거나 (패널·시트가 key일 수 있다)
    from AppKit import NSApp

    from narani.ui import page_zoom

    for w in (NSApp.keyWindow(), NSApp.mainWindow(), *(NSApp.windows() or [])):
        if w is not None and page_zoom._webviews(w):  # noqa: SLF001
            return w
    return None


def run(cmd: str, attempt: int = 0) -> None:
    """메뉴 항목 하나를 실행한다(메인 스레드) — 확대는 직접, 나머지는 key 창의 페이지로."""
    from narani.ui import page_zoom

    if cmd in ZOOM:
        page_zoom.zoom(ZOOM[cmd])
        return
    win = _key_window()
    views = page_zoom._webviews(win)  # noqa: SLF001 — 같은 패키지의 창 → 웹뷰 탐색
    if not views:
        return

    def done(result, error) -> None:  # noqa: ANN001
        if result == "again" and attempt < RETRIES:
            from PyObjCTools import AppHelper

            AppHelper.callLater(RETRY_DELAY, run, cmd, attempt + 1)
        elif result == "close-window" and win is not None:
            win.performClose_(None)

    views[0].evaluateJavaScript_completionHandler_(call_js(cmd, attempt), done)
