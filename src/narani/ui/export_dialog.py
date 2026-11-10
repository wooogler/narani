"""내보내기 대화상자 — 논문 화면의 '내보내기'와 홈의 여러 편 일괄 내보내기가 같이 쓴다 (§export_bundle).

체크박스로 묶을 것을 고른다: 영어·번역 마크다운(+이미지), 원본 PDF, 하이라이트·메모, BibTeX, 듣기 음성.
그 논문(들)에 없는 항목은 흐리게. 고른 것은 전역 설정 `[ui].export_items`에 기억한다 — 흐린 항목의 체크도
지우지 않고 두어, 다음 논문에서 그 항목이 있으면 다시 켜진 채로 보인다. 형식(범용·Notion·Obsidian)은
전역 `[output].export_target` 그대로(마크다운에만 적용).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

from narani import config, export_bundle, langs
from narani.i18n import N_, t
from narani.workdir import WorkDir

PREF_KEY = "export_items"
_OPEN_ATTR = "_md4_export_open"
_LABELS = {
    "en": N_("영어 마크다운"), "ko": N_("번역 마크다운"), "pdf": N_("원본 PDF"),
    "notes": N_("하이라이트·메모"), "bib": "BibTeX", "audio": N_("듣기 음성"),
}
_HINTS = {
    "en": N_("이미지 포함"), "ko": N_("이미지 포함"), "pdf": "",
    "notes": N_("자유 메모 + 하이라이트, 마크다운 한 장"), "bib": N_("서지 항목 (.bib)"),
    "audio": N_("웹 리더용으로 만든 음성 (.m4a)"),
}


def prefs() -> list[str]:
    """지난번에 고른 항목 — 설정이 없거나 깨졌으면 기본(영어·번역·PDF)."""
    got = config.load_config().get("ui", {}).get(PREF_KEY)
    if isinstance(got, list):
        return export_bundle.normalize(str(x) for x in got)
    return list(export_bundle.DEFAULT_ITEMS)


def _ko_label(roots: list[Path]) -> str:
    """번역 항목 이름 — 고른 논문들의 번역 언어가 하나면 그 언어 이름을 붙인다."""
    codes = {WorkDir(r).lang for r in roots if WorkDir(r).ko_md.exists()}
    if len(codes) == 1:
        return t("번역 마크다운 · {native}", native=langs.get(codes.pop()).native)
    return t(_LABELS["ko"])


async def open_dialog(roots: list[Path | str], before: Callable[[], Awaitable[None]] | None = None) -> None:
    """체크박스 내보내기 대화상자를 연다. `before`는 묶기 직전에 기다릴 일(뷰어의 메모 되저장 등).

    한 화면에 하나만 — 단추를 두 번 눌러 대화상자가 겹치지 않게."""
    from nicegui import run, ui

    from narani.ui import desktop
    from narani.ui.app import _EXPORT_TARGET

    roots = [Path(r) for r in roots]  # 열 때의 선택을 붙잡아 둔다 — 홈의 선택이 바뀌어도 이 묶음은 그대로
    if not roots:
        ui.notify(t("선택된 논문이 없습니다."), type="warning")
        return
    client = ui.context.client
    if getattr(client, _OPEN_ATTR, False):
        return
    setattr(client, _OPEN_ATTR, True)
    try:  # 논문마다 파일을 훑는다(bib 항목 조립 포함) — 수백 편이면 0.5초쯤이라 화면이 멈추지 않게 따로
        have = await run.io_bound(export_bundle.counts, roots)
        ko_label = await run.io_bound(_ko_label, roots)
    except Exception:
        setattr(client, _OPEN_ATTR, False)
        raise
    total = len(roots)
    chosen = set(prefs())
    state = {"busy": False}

    def effective() -> list[str]:
        return [k for k in export_bundle.ITEMS if k in chosen and have[k]]

    def toggle(key: str, on: bool) -> None:
        chosen.add(key) if on else chosen.discard(key)
        config.set_section_value("ui", PREF_KEY, export_bundle.normalize(chosen))
        footer.refresh()

    def pick_target(e) -> None:  # noqa: ANN001
        config.set_section_value("output", "export_target", e.value)

    async def go() -> None:
        items = effective()
        if not items or state["busy"]:
            return
        # 이 손잡이를 부른 단추는 footer를 다시 그리면 지워진다 — 그 뒤의 UI 호출은 카드(대화상자가 열린 동안 그대로)의 자리에서
        with card:
            state["busy"] = True
            dlg.props("persistent")  # 묶는 동안은 Esc·바깥 클릭으로 닫히지 않게 — 닫히면 전달할 자리가 없다
            footer.refresh()
            try:
                if before is not None:
                    await before()
                name, data, media = await run.io_bound(
                    export_bundle.bundle, roots, items, config.resolve_export_target())
            except (OSError, ValueError) as ex:
                ui.notify(str(ex), type="warning")
                state["busy"] = False
                dlg.props(remove="persistent")
                footer.refresh()
                return
            await desktop.deliver(name, data, media_type=media)
        dlg.close()

    # 화면 바탕(layout)에 짓는다 — 누른 단추가 다시 그려지는 영역(홈의 일괄 패널) 안이면 대화상자도 함께 지워진다
    with ui.context.client.layout, ui.dialog() as dlg, ui.card().classes("gap-2 q-pa-md").style("width:440px;max-width:94vw") as card:
        with ui.row().classes("items-baseline gap-2 w-full no-wrap"):
            ui.label(t("내보내기")).classes("text-base font-semibold")
            if total > 1:
                ui.label(t("{n}편", n=total)).classes("text-sm text-gray-500")
        ui.label(t("묶을 것을 고르세요 — zip 하나로 받습니다.")).classes("text-xs text-gray-500")
        with ui.column().classes("gap-0 w-full"):
            for k in export_bundle.ITEMS:
                n = have[k]
                label = ko_label if k == "ko" else t(_LABELS[k])
                with ui.row().classes("items-center gap-2 no-wrap w-full"):
                    cb = ui.checkbox(label, value=k in chosen,
                                     on_change=lambda e, kk=k: toggle(kk, bool(e.value))) \
                        .props("dense").classes("text-sm")
                    if not n:
                        cb.disable()
                    if total > 1:
                        note = t("{n}/{total}편", n=n, total=total)
                    else:
                        note = t(_HINTS[k]) if (n and _HINTS[k]) else ("" if n else t("없음"))
                    if note:
                        ui.label(note).classes("text-xs text-gray-500")
        with ui.row().classes("items-center gap-2 no-wrap q-mt-xs"):
            ui.label(t("형식")).classes("text-xs text-gray-500")
            ui.select({k: t(v) for k, v in _EXPORT_TARGET.items()}, value=config.resolve_export_target(),
                      on_change=pick_target).props("dense outlined").classes("w-32") \
                .tooltip(t("마크다운에 적용하는 형식 (전역 설정)"))

        @ui.refreshable
        def footer() -> None:
            items = effective()
            if total > 1 and items:
                ui.label(t("논문마다 자기 폴더로 — 마크다운은 <이름>-en/ · <이름>-ko/, 나머지는 <이름>/")) \
                    .classes("text-xs text-gray-500")
            with ui.row().classes("w-full justify-end gap-1"):
                ui.button(t("취소"), on_click=dlg.close).props("flat no-caps")
                b = ui.button(t("내보내기"), icon="download", on_click=go).props("unelevated no-caps color=primary")
                if not items or state["busy"]:
                    b.disable()
                if state["busy"]:
                    b.props("loading")

        footer()
    def _gone() -> None:
        setattr(client, _OPEN_ATTR, False)
        dlg.delete()

    dlg.on("hide", _gone)
    dlg.open()
