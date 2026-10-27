"""처음 켰을 때 뜨는 설정 마법사 — 언어 → AI 연결 → 저장 위치 → 첫 논문.

설치하고 켜면 설정 ⚙을 스스로 찾아 키를 넣어야 번역이 됐다. 비개발자에겐 그 길이 안 보이므로, 처음 한 번은
화면이 차례로 물어본다. 고른 값은 **설정 대화상자와 같은 config 키·함수**로 저장한다 — 마법사는 순서와 안내만
더한 다른 입구다. 어느 단계든 건너뛸 수 있고, 설정 ⚙ 머리의 '처음 설정 다시 하기'로 언제든 다시 연다.

**언제 뜨나**(`needs_setup`): 끝낸(또는 건너뛴) 표시 `[setup].done`이 없고, 예전부터 쓰던 흔적(키·언어·AI 서비스·
저장 위치)도 없을 때. 작업 폴더(`[output].workspace`)는 켤 때마다 `pin_workspace`가 적어 두므로 흔적으로 치지
않는다. 마법사 도중(`[setup].step`)엔 언어를 이미 적었어도 계속 뜬다 — 도중에 껐다 켜도 그 단계에서 이어진다.

**화면 언어**는 고르는 즉시 마법사를 그 언어로 다시 그린다. 뒤의 홈·헤더·JS 문구는 셸을 다시 불러야 바뀌므로
마법사를 닫을 때 언어가 바뀌었으면 페이지를 새로 부른다(Quasar 내장 문구만 앱을 다시 켜야 바뀐다).
"""

from __future__ import annotations

import locale
import os
from pathlib import Path
from typing import Awaitable, Callable

from narani import config, i18n, langs, library, projects
from narani.i18n import N_, t

STEPS = ("lang", "ai", "library", "done")
_STEP_NAME = {"lang": N_("언어"), "ai": N_("AI 연결"), "library": N_("저장 위치"), "done": N_("시작")}

# 키 받는 곳과 단계 — 링크는 각 회사의 키 관리 화면
KEY_GUIDE = {
    "openai": ("https://platform.openai.com/api-keys", (
        N_("OpenAI 플랫폼에 로그인합니다 (ChatGPT 계정으로 로그인할 수 있지만, 요금은 ChatGPT 구독과 따로입니다)."),
        N_("Billing에서 결제 수단을 등록하고 크레딧을 조금(예: 5달러) 충전합니다."),
        N_("'Create new secret key'를 눌러 키를 만들고 복사합니다 — 키는 이때 한 번만 보입니다."),
    )),
    "anthropic": ("https://console.anthropic.com/settings/keys", (
        N_("Anthropic 콘솔에 로그인합니다 (Claude 구독과는 별개의 계정·요금입니다)."),
        N_("Billing에서 결제 수단을 등록하고 크레딧을 조금 충전합니다."),
        N_("'Create Key'를 눌러 키를 만들고 복사합니다."),
    )),
    "gemini": ("https://aistudio.google.com/apikey", (
        N_("Google 계정으로 Google AI Studio에 로그인합니다."),
        N_("'Create API key'를 눌러 키를 만들고 복사합니다."),
        N_("무료 사용 한도가 있어 카드 없이 먼저 써 볼 수 있습니다 — 한도가 작아 논문이 많으면 결제를 켜야 합니다."),
    )),
}
_PROVIDER_BLURB = {
    "openai": N_("가장 널리 쓰입니다 · 기본 추천"),
    "anthropic": N_("자연스러운 번역 문장"),
    "gemini": N_("무료 한도로 먼저 써 보기 좋습니다"),
}

CSS = """
.md4-wiz { width: 600px; max-width: 94vw; padding: 0; gap: 0; }
.md4-wiz-head { padding: 14px 16px 10px 22px; border-bottom: 1px solid #e6e4e0; }
.md4-wiz-body { padding: 18px 22px 8px; gap: 12px; min-height: 360px; max-height: min(620px, 74vh); overflow-y: auto; }
.md4-wiz-foot { padding: 10px 16px 14px 22px; border-top: 1px solid #e6e4e0; }
.md4-wiz-dots { gap: 6px; }
.md4-wiz-dot { font-size: 12px; color: #9a968e; padding: 2px 8px; border-radius: 10px; }
.md4-wiz-dot.on { color: #2383e2; background: rgba(35,131,226,.13); font-weight: 600; }
.md4-wiz-dot.past { color: #6b6862; }
.md4-wiz-title { font-size: 18px; font-weight: 700; }
.md4-wiz-card { border: 1px solid #e6e4e0; border-radius: 8px; padding: 10px 12px; cursor: pointer; }
.md4-wiz-card.on { border-color: #2383e2; background: rgba(35,131,226,.08); }
.md4-wiz-steps { margin: 0; padding-left: 20px; font-size: 13.5px; line-height: 1.6; }
.md4-wiz-steps li { list-style: decimal; }
.md4-wiz-ok { color: #2e7d32; font-size: 13px; }
.md4-wiz-err { color: #c62828; font-size: 13px; }
.md4-wiz-up .q-uploader { width: 100%; box-shadow: none; border: 1px dashed #b9b6b0; }
@media (prefers-color-scheme: dark) {
  .md4-wiz-head, .md4-wiz-foot { border-color: #333; }
  .md4-wiz-card { border-color: #3a3a3a; }
  .md4-wiz-card.on { border-color: #5a96e6; background: rgba(90,150,230,.16); }
  .md4-wiz-ok { color: #81c784; }
  .md4-wiz-err { color: #ef9a9a; }
}
"""


# ------------------------------------------------------------------------------------
# 언제 뜨나 · 언어 추정 (화면 없이 시험할 수 있는 부분)
# ------------------------------------------------------------------------------------
def needs_setup(cfg: dict | None = None) -> bool:
    """처음 설정 마법사를 띄울지 (모듈 설명). cfg를 안 주면 config.toml을 읽는다."""
    cfg = config.load_config() if cfg is None else cfg
    setup = cfg.get("setup") or {}
    if setup.get("done"):
        return False
    if setup.get("step"):  # 마법사 도중 — 언어를 이미 적었어도 이어서 띄운다
        return True
    used = (cfg.get("keys") or cfg.get("default_provider") or cfg.get("library")
            or (cfg.get("ui") or {}).get("language") or (cfg.get("translate") or {}).get("target_lang"))
    return not used


def _tags(accept_language: str | None) -> list[str]:
    """Accept-Language 헤더('ko-KR,ko;q=0.9,en;q=0.8') → 순서대로 언어 태그. 없으면 OS 로캘."""
    tags = [p.split(";")[0].strip() for p in (accept_language or "").split(",")]
    tags = [x for x in tags if x and x != "*"]
    if not tags:
        for raw in (os.environ.get("LC_ALL"), os.environ.get("LANG"), _os_locale()):
            if raw and raw not in ("C", "POSIX"):
                tags.append(raw.split(".")[0].replace("_", "-"))
                break
    return tags


def _os_locale() -> str | None:
    try:
        return locale.getlocale()[0]
    except ValueError:
        return None


def guess_lang(accept_language: str | None = None) -> str:
    """사용자의 언어(BCP 47) 추정 — 중국어는 문자 체계로(zh-TW·zh-HK → zh-Hant, 나머지 → zh-Hans). 모르면 en."""
    for tag in _tags(accept_language):
        try:
            code = langs.normalize(tag)
        except ValueError:
            continue
        head = code.split("-")[0]
        if head == "zh":
            trad = any(p in code.split("-")[1:] for p in ("Hant", "TW", "HK", "MO"))
            return "zh-Hant" if trad else "zh-Hans"
        return head
    return "en"


def ui_lang_for(code: str) -> str:
    """추정한 언어 → 화면 언어 (카탈로그가 없으면 영어)."""
    return code if code in i18n.available() else "en"


def target_for(code: str) -> str:
    """추정한 언어 → 번역 언어. 영어 사용자이거나 모르는 언어면 지금 값(기본 한국어)을 둔다."""
    return code if code != "en" and code in langs.language_options() else config.resolve_target_lang()


def _set_ui_lang(code: str) -> None:
    config.set_section_value("ui", "language", code)
    i18n._ui_lang.cache_clear()  # 크기가 같은 저장을 같은 시각에 두 번 하면 mtime 캐시가 옛 값을 줄 수 있다


def _ui_lang_options() -> dict[str, str]:
    return {c: "English" if c == "en" else langs.get(c).native for c in i18n.available()}


def preselect_language(accept_language: str | None) -> bool:
    """첫 실행이면 사용자 언어(웹뷰·브라우저의 Accept-Language = OS 언어)로 화면·번역 언어를 미리 적는다.

    셸을 그리기 **전에** 불러야 헤더·홈·JS 문구까지 처음부터 그 언어로 나온다. 언어를 적으면 '예전 사용자'로
    보이지 않게 마법사 도중 표시(`[setup].step`)도 함께 적는다. 적었으면 True.
    """
    cfg = config.load_config()
    if not needs_setup(cfg) or (cfg.get("ui") or {}).get("language"):
        return False
    code = guess_lang(accept_language)
    target = target_for(code)
    if target != config.resolve_target_lang():
        config.set_section_value("translate", "target_lang", target)
    config.set_section_value("setup", "step", (cfg.get("setup") or {}).get("step") or "lang")
    _set_ui_lang(ui_lang_for(code))
    return True


def default_library_dir() -> Path:
    """저장 위치 제안 — 이미 정한 공통 폴더, 없으면 ~/Documents/Narani."""
    return library.dir_for("en") or Path.home() / "Documents" / "Narani"


def apply_library(path: str) -> Path:
    """공통 저장 폴더 하나 → en/번역/pdf 세 칸 (설정 '폴더 하나 고르기'와 같은 배치). 없으면 만든다."""
    base = Path(path.strip()).expanduser()
    base.mkdir(parents=True, exist_ok=True)
    for which, sub in projects.layout(base).items():
        config.set_library_dir(which, str(sub))
    projects.ensure_layout(base)
    return base


def save_ai(provider: str, key: str | None) -> None:
    """AI 서비스·모델(지금 값, 없으면 가장 저렴한 등급)·키를 설정 대화상자와 같은 자리에 저장."""
    config.set_default("default_provider", provider)
    config.set_section_value("models", provider, config.resolve_model(provider))
    if key:
        config.set_key(provider, key.strip())


def friendly_error(msg: str) -> str:
    """연결 테스트 실패(SDK 오류 원문) → 비개발자가 알아들을 한 줄. 모르는 오류면 빈 문자열(원문만 보인다)."""
    low = (msg or "").lower()
    if any(k in low for k in ("401", "invalid_api_key", "incorrect api key", "api key not valid",
                              "invalid x-api-key", "authentication")):
        return t("키가 맞지 않습니다 — 키를 처음부터 끝까지 다시 복사해 붙여넣어 보세요.")
    if any(k in low for k in ("insufficient_quota", "credit balance", "billing", "quota", "429")):
        return t("키는 맞지만 쓸 수 있는 크레딧이 없거나 한도를 넘었습니다 — 결제(Billing)를 확인하세요.")
    if any(k in low for k in ("connect", "timed out", "timeout", "network", "name resolution")):
        return t("AI 서비스에 닿지 못했습니다 — 인터넷 연결을 확인하세요.")
    return ""


def finish() -> None:
    """끝냈거나 건너뛴 표시 — 다음부터 뜨지 않는다."""
    config.set_section_value("setup", "done", True)
    config.set_section_value("setup", "step", None)


# ------------------------------------------------------------------------------------
# 화면
# ------------------------------------------------------------------------------------
def build_wizard(state: dict, on_upload: Callable[[object], Awaitable[None]],
                 on_done: Callable[[], None]) -> Callable[..., None]:
    """마법사 대화상자를 짓고 여는 함수를 돌려준다. `opener(first=True)`는 첫 실행(멈춘 단계에서 이어 열기).

    on_upload는 홈의 업로드 처리(마지막 단계의 '첫 논문 올리기'), on_done은 닫힐 때 홈을 다시 그리는 함수.
    """
    from nicegui import run, ui

    from narani.ui import desktop, settings_dialog
    from narani.ui.app import _PROVIDER_NAME, _short_provider

    w: dict = {"step": "lang", "provider": config.resolve_provider(), "key": "", "test": None,
               "lib": "", "lib_err": "", "lang_changed": False,
               "first": False, "next": None}

    def go(step: str) -> None:
        w["step"] = step
        if w["first"] and step != "done":
            config.set_section_value("setup", "step", step)  # 도중에 꺼도 이 단계에서 이어지게
        body.refresh()

    def close() -> None:
        dlg.close()
        if w["lang_changed"]:  # 헤더·홈·JS 문구까지 새 언어로
            ui.navigate.reload()
        else:
            on_done()

    def skip() -> None:
        finish()
        close()
        ui.notify(t("나중에 설정 ⚙에서 '처음 설정 다시 하기'로 언제든 열 수 있습니다."), type="info", timeout=4000)

    def done() -> None:
        finish()
        close()

    # ---- 단계 1: 언어 ----
    def step_lang() -> None:
        ui.label(t("Narani에 오신 것을 환영합니다")).classes("md4-wiz-title")
        ui.label(t("영어 논문 PDF를 읽기 좋은 문서로 바꾸고, 내 언어 번역과 나란히 보여 줍니다. "
                   "몇 가지만 정하면 바로 쓸 수 있어요 — 모두 나중에 설정 ⚙에서 바꿀 수 있습니다.")) \
            .classes("text-sm text-gray-600")

        def pick_ui(e) -> None:  # noqa: ANN001
            if not e.value or e.value == i18n.ui_lang():
                return
            _set_ui_lang(e.value)
            w["lang_changed"] = True
            body.refresh()  # 마법사를 그 자리에서 새 언어로

        def pick_target(e) -> None:  # noqa: ANN001
            if e.value and e.value != config.resolve_target_lang():
                config.set_section_value("translate", "target_lang", e.value)

        ui.select(_ui_lang_options(), value=i18n.ui_lang(), label=t("화면 언어"), on_change=pick_ui) \
            .props("outlined").classes("w-full")
        ui.label(t("메뉴·단추·안내문의 언어입니다. 고르면 바로 바뀝니다.")).classes("text-xs text-gray-400")
        cur = config.resolve_target_lang()
        ui.select(langs.language_options(cur), value=cur, label=t("번역 언어 (모국어)"), on_change=pick_target) \
            .props("outlined").classes("w-full")
        ui.label(t("영어 논문을 이 언어로 번역합니다.")).classes("text-xs text-gray-400")

    # ---- 단계 2: AI 연결 ----
    def step_ai() -> None:
        ui.label(t("AI 서비스 연결")).classes("md4-wiz-title")
        ui.label(t("번역·용어집·참고문헌 정리에 AI를 씁니다. 쓰려는 서비스의 API 키를 한 번 넣어 두면 됩니다. "
                   "요금은 쓴 만큼 그 서비스에 직접 냅니다 — 기본 모델로 논문 한 편 번역은 보통 1달러가 안 됩니다.")) \
            .classes("text-sm text-gray-600")

        def choose(p: str) -> None:
            if p != w["provider"]:
                w.update(provider=p, test=None, key="")
                body.refresh()

        with ui.row().classes("w-full no-wrap gap-2"):
            for p in config.PROVIDERS:
                card = ui.column().classes("md4-wiz-card gap-0 flex-1" + (" on" if p == w["provider"] else ""))
                card.on("click", lambda _, p=p: choose(p))
                with card:
                    ui.label(t(_PROVIDER_NAME[p])).classes("text-sm font-semibold")
                    ui.label(t(_PROVIDER_BLURB[p])).classes("text-xs text-gray-500")

        prov = w["provider"]
        url, steps = KEY_GUIDE[prov]
        with ui.element("ol").classes("md4-wiz-steps"):
            for s in steps:
                with ui.element("li"):
                    ui.label(t(s))
        ui.link(t("{v} 키 받는 곳 열기 ↗", v=_short_provider(prov)), url, new_tab=True) \
            .classes("text-sm font-semibold").style("color:#2383e2")

        saved = config.stored_key_preview(prov)
        if saved:
            ui.label(t("이 서비스의 키가 이미 저장되어 있습니다 ({masked}) — 그대로 두려면 비워 두세요.",
                       masked=saved["masked"])).classes("text-xs text-gray-500")
        key_in = ui.input(t("API 키 붙여넣기"), password=True, password_toggle_button=True, value=w["key"]) \
            .props("outlined").classes("w-full")

        def on_key(e) -> None:  # noqa: ANN001
            w["key"] = (e.value or "").strip()
            w["test"] = None
            result.refresh()

        key_in.on_value_change(on_key)

        @ui.refreshable
        def result() -> None:
            if w["test"] is None:
                return
            ok, msg = w["test"]
            if ok:
                ui.label("✅ " + t("연결됐습니다.")).classes("md4-wiz-ok")
                return
            plain = friendly_error(msg)
            ui.label("❌ " + (plain or t("연결하지 못했습니다."))).classes("md4-wiz-err")
            if not plain:
                ui.label(t("키를 다시 복사해 붙여넣어 보세요. 결제(크레딧)가 없으면 키가 맞아도 실패합니다.")) \
                    .classes("text-xs text-gray-500")
            ui.label(msg).classes("text-xs text-gray-400 break-all")  # 원문 — 물어볼 때 쓰라고 작게

        async def test() -> None:
            k = w["key"] or config.resolve_key(prov)
            if not k:
                ui.notify(t("먼저 키를 붙여넣으세요."), type="warning")
                return
            test_btn.props("loading")
            try:
                ok, msg = await run.io_bound(config.validate_provider_key, prov, config.resolve_model(prov), k)
            finally:
                test_btn.props(remove="loading")
            w["test"] = (ok, msg)
            result.refresh()

        with ui.row().classes("items-center gap-2"):
            test_btn = ui.button(t("연결 테스트"), icon="wifi_tethering", on_click=test).props("outline no-caps")
        result()
        ui.label(t("키는 이 컴퓨터에만(소유자만 읽는 파일로) 저장되고, 고른 AI 서비스로만 보냅니다.")) \
            .classes("text-xs text-gray-400")
        if not saved:
            ui.label(t("키가 없어도 PDF 변환과 읽기는 됩니다 — '나중에 할게요'를 누르면 번역만 꺼진 채로 시작하고, "
                       "설정 ⚙ → AI에서 언제든 넣을 수 있습니다.")).classes("text-xs text-gray-500")

    async def next_from_ai() -> None:
        prov = w["provider"]
        if w["key"]:
            if w["test"] is None:  # 저장 전에 한 번은 확인한다 — 틀린 키로 몇 분 번역하다 실패하지 않게
                w["next"].props("loading")
                try:
                    w["test"] = await run.io_bound(config.validate_provider_key, prov,
                                                   config.resolve_model(prov), w["key"])
                finally:
                    w["next"].props(remove="loading")
                if not w["test"][0]:
                    body.refresh()
                    return  # 실패를 보여 주고 멈춘다 — '그래도 저장'은 아래 단추로
            save_ai(prov, w["key"])
            w["key"] = ""
        elif config.resolve_key(prov):  # 저장된 키(또는 환경변수)로 서비스만 바꾼 경우
            save_ai(prov, None)
        go("library")

    def save_anyway() -> None:
        save_ai(w["provider"], w["key"])
        w["key"] = ""
        go("library")

    def later_ai() -> None:
        w["key"] = ""
        go("library")

    # ---- 단계 3: 저장 위치 ----
    def step_library() -> None:
        ui.label(t("저장 위치")).classes("md4-wiz-title")
        sub = projects.translation_subdir()
        ui.label(t("변환·번역한 문서를 모아 둘 폴더 하나를 고르세요. 영어 원문은 폴더 바로 아래, 번역은 {sub}/, "
                   "원본 PDF는 pdf/에 들어갑니다. Obsidian 볼트 안의 폴더도 좋습니다.", sub=sub)) \
            .classes("text-sm text-gray-600")
        if not w["lib"]:
            w["lib"] = str(default_library_dir())
        with ui.row().classes("items-center gap-2 w-full no-wrap"):
            inp = ui.input(t("공통 저장 폴더"), value=w["lib"]).props("outlined").classes("flex-grow min-w-0")
            inp.on_value_change(lambda e: w.update(lib=(e.value or "").strip(), lib_err=""))

            async def pick() -> None:
                picked = await desktop.choose_folder(t("공통 저장 폴더 선택"), w["lib"] or str(Path.home()))
                if picked:
                    w["lib"] = picked
                    inp.set_value(picked)

            btn = ui.button(t("고르기"), icon="folder_open", on_click=pick).props("outline no-caps")
            if not settings_dialog._can_pick():
                settings_dialog._no_dialog(btn)
        ui.label(t("폴더가 없으면 만듭니다. 컬렉션마다 다른 폴더를 쓰고 싶으면 나중에 컬렉션 설정에서 정하면 됩니다.")) \
            .classes("text-xs text-gray-400")
        if w["lib_err"]:
            ui.label("❌ " + w["lib_err"]).classes("md4-wiz-err break-all")

    def next_from_library() -> None:
        if not w["lib"]:
            go("done")
            return
        try:
            apply_library(w["lib"])
        except OSError as e:
            w["lib_err"] = t("이 폴더를 쓸 수 없습니다: {err}", err=e.strerror or str(e))
            body.refresh()
            return
        go("done")

    # ---- 단계 4: 시작 ----
    def step_done() -> None:
        ui.label(t("준비됐습니다!")).classes("md4-wiz-title")
        prov = config.resolve_provider()
        has_key = bool(config.resolve_key(prov))
        lib = library.dir_for("en")
        rows = (
            (t("화면 언어"), _ui_lang_options().get(i18n.ui_lang(), i18n.ui_lang())),
            (t("번역 언어"), langs.get(config.resolve_target_lang()).native),
            ("AI", _short_provider(prov) if has_key else t("나중에 — 변환·읽기는 되고 번역만 꺼져 있습니다")),
            (t("저장 위치"), str(lib) if lib else t("정하지 않음 — 작업 폴더에만 남습니다")),
        )
        with ui.column().classes("w-full gap-1"):
            for name, val in rows:
                with ui.row().classes("w-full no-wrap gap-3"):
                    ui.label(name).classes("text-sm text-gray-500 shrink-0").style("width:150px")
                    ui.label(val).classes("text-sm break-all")
        ui.label(t("첫 논문 PDF를 올려 보세요. 변환이 끝나면 목록에서 열어 원문과 번역을 나란히 볼 수 있습니다.")) \
            .classes("text-sm text-gray-600 q-mt-sm")

        async def uploaded(e) -> None:  # noqa: ANN001
            await on_upload(e)
            done()

        with ui.element("div").classes("md4-wiz-up w-full"):
            ui.upload(label=t("PDF 고르기 — 여기로 끌어다 놓아도 됩니다"), on_upload=uploaded, auto_upload=True) \
                .props('accept=".pdf" flat bordered').classes("w-full")

    @ui.refreshable
    def body() -> None:
        step = w["step"]
        idx = STEPS.index(step)
        with ui.row().classes("md4-wiz-head items-center w-full no-wrap"):
            with ui.row().classes("md4-wiz-dots items-center no-wrap"):
                for i, s in enumerate(STEPS):
                    cls = "on" if i == idx else ("past" if i < idx else "")
                    ui.label(f"{i + 1}. {t(_STEP_NAME[s])}").classes(f"md4-wiz-dot {cls}")
            ui.space()
            ui.button(t("건너뛰기"), on_click=skip).props("flat dense no-caps color=grey") \
                .tooltip(t("마법사를 닫고 기본값으로 시작합니다"))
        with ui.column().classes("md4-wiz-body w-full"):
            {"lang": step_lang, "ai": step_ai, "library": step_library, "done": step_done}[step]()
        with ui.row().classes("md4-wiz-foot items-center w-full no-wrap gap-2"):
            if idx > 0:
                ui.button(t("뒤로"), icon="arrow_back", on_click=lambda: go(STEPS[idx - 1])) \
                    .props("flat no-caps")
            ui.space()
            footer_buttons(step)

    def footer_buttons(step: str) -> None:
        """단계마다 오른쪽 아래 단추들 — '다음'은 저장까지 하고 넘어간다. 다음 단추는 w["next"]에(확인 중 loading)."""
        if step == "lang":
            w["next"] = ui.button(t("다음"), icon="arrow_forward", on_click=lambda: go("ai")) \
                .props("unelevated no-caps color=primary")
        elif step == "ai":
            if w["test"] is not None and not w["test"][0] and w["key"]:
                ui.button(t("그래도 저장"), on_click=save_anyway).props("flat no-caps color=grey")
            ui.button(t("나중에 할게요"), on_click=later_ai).props("flat no-caps")
            w["next"] = ui.button(t("다음"), icon="arrow_forward", on_click=next_from_ai) \
                .props("unelevated no-caps color=primary")
        elif step == "library":
            ui.button(t("저장 위치 없이"), on_click=lambda: (w.update(lib=""), go("done"))) \
                .props("flat no-caps").tooltip(t("작업 폴더에만 남기고, 나중에 설정 ⚙ → 저장 위치에서 정합니다"))
            w["next"] = ui.button(t("다음"), icon="arrow_forward", on_click=next_from_library) \
                .props("unelevated no-caps color=primary")
        else:
            w["next"] = ui.button(t("시작하기"), icon="check", on_click=done) \
                .props("unelevated no-caps color=primary")

    with ui.dialog().props("persistent") as dlg, ui.card().classes("md4-wiz"):
        body()

    def opener(*, first: bool = False) -> None:
        w.update(step="lang", provider=config.resolve_provider(), key="", test=None, lib="", lib_err="",
                 lang_changed=False, first=first)
        if first:  # 도중에 껐다 켰으면 그 단계부터
            step = (config.load_config().get("setup") or {}).get("step")
            w["step"] = step if step in STEPS else "lang"
            config.set_section_value("setup", "step", w["step"])
        body.refresh()
        dlg.open()

    return opener

