"""서재 Drive 동기화 화면 조각 — 설정의 켜기/끄기 칸, 처음 한 번 묻는 창, 상태 한 줄 (§libsync.service)."""

from __future__ import annotations

from collections.abc import Callable

from narani.i18n import t
from narani.libsync import service as library_sync


def status_line(i: dict) -> str:
    """'서재 동기화 — 12편 · 340MB · 2분 전 맞춤' — 일하는 중이면 진행. 홈의 구름 툴팁·진행 줄과 설정이 같이 쓴다."""
    from narani.ui.app import _relative_time

    if i["running"] and i["phase"] == "import":
        return t("다른 컴퓨터의 논문을 가져오는 중… {done}/{total} {label}", done=i["done"] + 1, total=i["total"],
                 label=i["label"])
    if i["running"] and i["phase"] == "reader" and i["total"]:
        return t("휴대폰에서 읽을 수 있게 준비하는 중… {done}/{total} {label}", done=i["done"], total=i["total"],
                 label=i["label"])
    if i["running"] and i["total"]:
        return t("서재를 맞추는 중… {done}/{total} {label}", done=i["done"], total=i["total"], label=i["label"])
    if i["running"]:
        return t("서재 목록을 받는 중…")
    bits = [t("서재 동기화 — {n}편 · {mb:.0f}MB", n=i["papers"], mb=i["bytes"] / 1e6)]
    if i["drive_only"]:
        bits.append(t("가져올 논문 {n}편", n=i["drive_only"]))
    when = i["last_ok"] or i["listed"]
    if when:
        bits.append(t("{when} 맞춤", when=_relative_time(when)))
    return " · ".join(bits)


def settings_block(ui, on_change: Callable[[], None] | None = None) -> None:  # noqa: ANN001
    """설정 → 저장 위치 탭의 'Drive 서재 동기화' 칸."""
    from nicegui import run

    from narani.ui import audio_dialog

    @ui.refreshable
    def block() -> None:
        i = library_sync.info()
        if not i["available"]:
            ui.label(t("Google Drive를 연결하면 서재(논문·번역·메모·컬렉션)를 Drive에 올려 다른 컴퓨터의 Narani에서도 "
                       "같은 서재를 쓸 수 있습니다.")).classes("text-xs text-gray-500")

            async def connect() -> None:
                if await audio_dialog.ensure_drive():
                    block.refresh()
            ui.button(t("Google Drive 연결"), icon="link", on_click=connect).props("dense outline no-caps")
            return

        def flip(e) -> None:  # noqa: ANN001
            library_sync.set_enabled(bool(e.value))
            block.refresh()
            if on_change:
                on_change()
        ui.switch(t("서재를 Drive에 동기화"), value=i["enabled"], on_change=flip).props("dense") \
            .tooltip(t("논문을 추가·번역·편집하거나 메모·용어집·컬렉션을 바꾸면 바뀐 논문만 뒤에서 Drive에 올리고, "
                       "다른 컴퓨터에서 바꾼 것은 받아 옵니다"))
        if i["enabled"]:
            ui.label(status_line(i)).classes("text-xs text-gray-500")
            if i["error"]:
                ui.label(t("⚠ 마지막 동기화 문제: {e}", e=i["error"][:200])).classes("text-xs text-orange-8")

            async def now() -> None:
                n = ui.notification(t("Drive 서재와 맞추는 중…"), spinner=True, timeout=None)
                try:
                    out = await run.io_bound(library_sync.sync_now)
                    ui.notify(t("Drive 서재: 올림 {up} · 받음 {down} · 병합 {merged}", up=out["up"], down=out["down"],
                                merged=out["merged"]), type="positive")
                except Exception as e:  # noqa: BLE001
                    ui.notify(t("Drive 서재와 맞추지 못했습니다: {e}", e=e), type="negative")
                finally:
                    n.dismiss()
                block.refresh()
                if on_change:
                    on_change()
            ui.button(t("지금 동기화"), icon="sync", on_click=now).props("dense outline no-caps")
            gone = library_sync.declined()
            if gone:   # 이 컴퓨터에서만 지운 논문 — 저절로 다시 가져오지 않는다
                ui.label(t("이 컴퓨터에서만 지운 논문 {n}편 — Drive 서재에는 남아 있고, 저절로 다시 가져오지 않습니다.",
                           n=len(gone))).classes("text-xs text-gray-500 q-mt-xs")
                for d in gone[:5]:
                    ui.label("· " + (d.get("title") or d.get("name") or d["pid"])).classes("text-xs text-gray-500 truncate")
                if len(gone) > 5:
                    ui.label(t("외 {v}편", v=len(gone) - 5)).classes("text-xs text-gray-500")

                def take_back() -> None:
                    library_sync.take_back([d["pid"] for d in gone])
                    ui.notify(t("다시 가져옵니다 — 끝나면 홈 목록에 나타납니다."), type="info")
                    block.refresh()
                ui.button(t("모두 다시 가져오기"), icon="download", on_click=take_back).props("dense flat no-caps")
        ui.label(t("올리는 것: 원본 PDF, 마크다운·그림, 번역(용어집·번역 캐시), 인용·서지, 하이라이트·메모, 만든 듣기 음성, "
                   "컬렉션 목록. 올리지 않는 것: API 키·설정, 음성 섹션 조각, 미리보기 캐시, 컬렉션 폴더 위치, 고정 표시.")) \
            .classes("text-xs text-gray-400")
        if i["local"]:
            ui.label(t("시험용 폴더를 Drive 대신 쓰는 중: {p}", p=str(library_sync.local_store_dir()))) \
                .classes("text-xs text-gray-400 break-all")

    block()


_ASKED = {"session": False}


async def ask(on_change: Callable[[], object]) -> None:
    """Drive를 연결한 사용자에게 한 번 — 서재 동기화를 켤지. Drive에 이미 서재가 있으면(다른 컴퓨터) 편수를 알려 준다.
    '나중에'면 이번 실행 동안은 다시 묻지 않는다."""
    from nicegui import run, ui

    if _ASKED["session"]:
        return
    _ASKED["session"] = True
    try:
        n = await run.io_bound(library_sync.remote_count)
    except Exception:  # noqa: BLE001 — 오프라인이면 다음에 묻는다
        return
    with ui.dialog().props("persistent") as dlg, ui.card().classes("gap-2").style("max-width:480px"):
        ui.label(t("서재를 Google Drive에 동기화할까요?")).classes("font-bold")
        if n:
            ui.label(t("Drive에 다른 컴퓨터에서 올린 서재가 {n}편 있습니다. 켜면 그 논문들을 이 컴퓨터로 저절로 가져오고, "
                       "이 컴퓨터의 논문도 올립니다.", n=n)).classes("text-sm")
        else:
            ui.label(t("켜면 논문을 추가·번역·편집할 때마다 바뀐 논문만 뒤에서 Drive에 올립니다. 다른 컴퓨터의 Narani에서 "
                       "같은 Google 계정으로 연결하면 같은 서재를 씁니다.")).classes("text-sm")
        ui.label(t("처음에는 지금 서재 전체(원본 PDF·그림 포함)를 올립니다. API 키와 설정은 올리지 않습니다. 진행은 홈 "
                   "왼쪽 아래 구름 아이콘에 보이고, 나중에 설정 → 저장 위치에서 끌 수 있습니다.")).classes("text-xs text-gray-500")

        def pick(on: bool) -> None:
            library_sync.set_enabled(on)
            dlg.close()
            res = on_change()
            if hasattr(res, "__await__"):
                import asyncio

                asyncio.ensure_future(res)
        with ui.row().classes("justify-end w-full gap-2"):
            ui.button(t("나중에"), on_click=dlg.close).props("flat no-caps")
            ui.button(t("끄기"), on_click=lambda: pick(False)).props("flat no-caps")
            ui.button(t("켜기"), icon="cloud_sync", on_click=lambda: pick(True)).props("unelevated no-caps color=primary")
    dlg.open()
