"""듣기 음성 만들기 대화상자 — 홈(여러 편·웹 리더 보기)과 뷰어(듣기 막대 ⋯, 이 논문 하나)가 같이 쓴다.

음성은 이 컴퓨터에 만든다(§phone.service.make_local — Drive 없이). 웹 리더에 이미 있는 논문은 만든 뒤 저절로
올리고, 그 밖의 논문은 '웹 리더로도 보내기'(기본 꺼짐)를 켰을 때만 보낸다. 홈은 웹 리더 보기에서 부르므로 늘 보낸다.

오래 걸리는 일이라 백그라운드 작업(§ui.jobs, 종류 `mobile_audio`)으로 돈다 — 다른 논문·홈으로 가도 끝까지 돌고,
한 번에 한 편씩(GATE), 같은 논문은 둘째 시작이 거절된다.
"""

from __future__ import annotations

import re
from pathlib import Path

from narani.i18n import t
from narani.phone import gdrive, pages
from narani.phone import service as phone_service
from narani.ui import jobs

KIND = "mobile_audio"
_OPEN_ATTR = "_md4_audio_dialog_open"
GATE = jobs.Gate(1)  # 서버 수명 동안 하나, 한 번에 한 편씩 (합성이 이미 여러 스레드를 쓴다)


def audio_label(a: dict) -> str:
    """만든 음성 한 줄 — 무엇으로(엔진·목소리) · 길이 · 언제. 예: 'Mac · Yuna · 68분 · 3시간 전'."""
    from narani.ui.app import _relative_time

    v = str(a.get("voice") or "")
    if v.startswith("gemini:"):
        _, model, name = (v.split(":") + ["", ""])[:3]
        who = f"Gemini {'flash-lite' if 'lite' in model else 'flash'} · {name}"
    elif v.startswith("qwen:"):
        who = "Qwen3 · " + (v.split(":") + ["", ""])[2]
    elif v.startswith("mac:"):
        who = "Mac · " + re.sub(r"\s+\([^()]*\([^()]*\)\)$", "", v[4:].split("@")[0])
    elif v.startswith("win:"):
        who = "Windows · " + re.sub(r"^Microsoft\s+|\s+Desktop$", "", v[4:].split("@")[0])
    else:
        who = v or "?"
    bits = [who, t("{m}분", m=round(float(a.get("duration") or 0) / 60))]
    if a.get("built"):
        bits.append(_relative_time(float(a["built"])))
    return " · ".join(bits)


# ---- Google Drive 연결 · 부르기 (홈과 뷰어가 같이) ----
async def connect_drive() -> bool:
    """웹 리더용 Google Drive 연결 — 브라우저에서 로그인·허용을 마치면 True."""
    from nicegui import run, ui

    n = ui.notification(t("브라우저에서 Google 로그인을 마치세요 — 웹 리더가 쓸 Narani 폴더를 Drive에 만듭니다."),
                        spinner=True, timeout=None)
    try:
        who = await run.io_bound(phone_service.connect)
    except gdrive.DriveError as e:
        ui.notify(t("Google Drive에 연결하지 못했습니다: {e}", e=e), type="negative")
        return False
    finally:
        n.dismiss()
    ui.notify(t("Google Drive에 연결했습니다: {who}", who=who or "?"), type="positive")
    return True


async def ensure_drive(on_connect=None) -> bool:  # noqa: ANN001
    """보내기 전에 — 설정이 없으면 알리고 False, 연결 전이면 연결부터. on_connect는 새로 연결했을 때."""
    from nicegui import ui

    if not phone_service.configured():
        ui.notify(t("웹 리더용 Google 클라이언트 설정이 없습니다 — README의 '웹 리더에서 읽기'를 보세요."),
                  type="warning")
        return False
    if phone_service.connected():
        return True
    if not await connect_drive():
        return False
    if on_connect:
        on_connect()
    return True


async def drive_call(fn, roots: list[Path], busy: str, progress: dict | None = None, on_connect=None):  # noqa: ANN001, ANN202
    """연결을 확인하고(없으면 연결부터) fn(roots)를 돌린다. 승인이 만료됐으면 한 번 다시 연결한다.

    progress가 있으면 fn이 그 안의 'msg'를 고쳐 쓰는 동안 알림 문구를 따라 바꾼다(음성 만들기처럼 긴 일)."""
    from nicegui import run, ui

    if not await ensure_drive(on_connect):
        return None
    for attempt in (0, 1):
        n = ui.notification(busy, spinner=True, timeout=None)
        tick = ui.timer(0.5, lambda n=n: setattr(n, "message", progress.get("msg") or busy)) if progress else None
        try:
            return await run.io_bound(fn, roots)
        except gdrive.NotConnected:
            gdrive.token_path().unlink(missing_ok=True)
            if attempt or not await connect_drive():
                return None
            if on_connect:
                on_connect()
        except Exception as e:  # noqa: BLE001
            ui.notify(t("웹 리더와 주고받지 못했습니다: {e}", e=e), type="negative")
            return None
        finally:
            if tick:
                tick.cancel()
            n.dismiss()
    return None


async def ask(host, roots: list[Path], send: bool | None = None) -> dict | None:  # noqa: ANN001, C901
    """음성 만들기 창 — 엔진·목소리·(한 편이면) 섹션·발음 용어집·캡션·수식을 고른다. 취소하면 None.

    send=True면 늘 웹 리더로도 보낸다(홈의 웹 리더 보기), None이면 웹 리더에 없는 논문이 있을 때 '웹 리더로도
    보내기'(기본 꺼짐)를 묻는다. 반환: {engine, voice, model, style, captions, math, include, send}.
    무료 내장 목소리(Mac·Windows), 무료 로컬 Qwen3(Apple Silicon), Gemini(유료, 내 키) 가운데 이 컴퓨터에서 되는
    엔진만 고를 수 있다. Gemini는 길이로 어림한 비용을 먼저 보여 준다."""
    from nicegui import run, ui

    from narani.phone import tts

    roots = [Path(r) for r in roots]
    if not roots:
        ui.notify(t("음성을 만들 논문이 없습니다."), type="warning")
        return
    if not phone_service.audio_available():
        ui.notify(t("음성 파일을 만들 인코더(PyAV)가 없습니다 — 앱을 업데이트(다시 설치)하세요."), type="warning")
        return
    langs = sorted({await run.io_bound(phone_service.paper_lang, r) for r in roots})
    kind = await run.io_bound(phone_service.system_kind)      # 무료 내장 목소리: mac · win · 없음
    names = await run.io_bound(phone_service.voices, langs[0], kind) if (kind and len(langs) == 1) else []
    gem_ok = await run.io_bound(phone_service.gemini_ready)
    qs = await run.io_bound(phone_service.qwen_status)
    if not kind and not gem_ok and not qs["supported"]:
        ui.notify(t("이 컴퓨터에서 쓸 수 있는 목소리가 없습니다 — 설정 → AI에서 Gemini 키를 넣으면 Gemini 목소리로 만들 수 있습니다."),
                  type="warning")
        return
    if kind and len(langs) == 1 and not names and not gem_ok and not qs["ready"]:
        ui.notify(t("이 Mac에 '{lang}' 목소리가 없습니다 — 시스템 설정 → 손쉬운 사용 → 읽기 및 말하기에서 받으세요.",
                    lang=langs[0]) if kind == "mac" else
                  t("이 PC에 '{lang}' 목소리가 없습니다 — Windows 설정 → 시간 및 언어 → 음성에서 그 언어의 음성을 "
                    "추가하세요(추가한 뒤 앱을 다시 켜야 보일 수 있습니다).", lang=langs[0]), type="warning")
        return
    per = [await run.io_bound(phone_service.sections, r) for r in roots]
    there = phone_service.on_mobile()
    on_web = [r for r in roots if r.stem in there]
    off_web = [r for r in roots if r.stem not in there]
    secs = per[0] if len(roots) == 1 else []
    gc = phone_service.gemini_choice()
    st = await run.io_bound(phone_service.speech_terms, roots[0]) if len(roots) == 1 else {"terms": {}, "found": []}
    term_inputs: dict = {}

    def cur_terms() -> dict:
        return {k: (inp.value or "").strip() for k, inp in term_inputs.items() if (inp.value or "").strip()}
    picked = {"go": False}

    def go() -> None:
        picked["go"] = True
        dlg.close()

    # 들어 보기는 첫 논문의 첫 본문 문장으로 — 보통 초록의 첫 문장 (창에 그 문장을 보여 준다)
    sample = next((sec["first"] for sec in (per[0] if per else []) if sec.get("first")), "") or "Narani"

    async def preview() -> None:
        import shutil
        import subprocess
        import sys
        import tempfile

        spoken = tts.speak_text(sample, cur_terms())
        if eng.value == "mac":
            subprocess.Popen(["say", "-v", voice.value, "--", spoken])
            return
        if eng.value == "win" and not voice.value:
            return
        if eng.value == "qwen" and not qs["ready"]:
            ui.notify(t("먼저 Qwen3를 설치하세요."), type="warning")
            return
        n = ui.notification(t("목소리를 준비하는 중…"), spinner=True, timeout=None) if eng.value == "qwen" else None
        try:
            if eng.value == "qwen":
                wav = await run.io_bound(phone_service.voice_preview, "qwen", qvoice.value, langs[0], spoken,
                                         None, qstyle.value or "")
            elif eng.value == "win":
                wav = await run.io_bound(phone_service.voice_preview, "win", voice.value, langs[0], spoken)
            else:
                wav = await run.io_bound(phone_service.gemini_preview, gvoice.value, gmodel.value, spoken)
        except Exception as e:  # noqa: BLE001
            if n:
                n.dismiss()
            ui.notify(t("들어 보기 실패: {e}", e=e), type="negative")
            return
        if n:
            n.dismiss()
        f = Path(tempfile.gettempdir()) / "narani-voice-preview.wav"
        f.write_bytes(wav)
        if sys.platform == "win32":
            import winsound

            winsound.PlaySound(str(f), winsound.SND_FILENAME | winsound.SND_ASYNC)
        elif shutil.which("afplay"):
            subprocess.Popen(["afplay", str(f)])
        elif shutil.which("aplay"):
            subprocess.Popen(["aplay", "-q", str(f)])

    with host, ui.dialog() as dlg, ui.card().classes("gap-2").style("width:540px;max-height:92vh;overflow-y:auto"):
        ui.label(t("듣기 음성 만들기")).classes("font-bold")
        ui.label(t("번역 본문을 읽은 음성을 이 컴퓨터에 만듭니다 — 뷰어의 듣기에서 바로 듣고, 웹 리더에 있는 논문은 "
                   "Drive에도 올립니다(시간당 약 15MB). 그림·표·인용 표기·참고문헌은 건너뛰고, 섹션 제목은 알립니다. "
                   "캡션·수식은 아래에서 고릅니다.")).classes("text-xs text-gray-500")
        opts = {kind: t("무료 — Mac 목소리") if kind == "mac" else t("무료 — Windows 목소리")} if kind else {}
        if qs["supported"]:
            opts["qwen"] = t("Qwen3 — 무료·로컬, 자연스러움")
        if gem_ok:
            opts["gemini"] = t("Gemini — 더 자연스러움 (유료)")
        first = "gemini" if gem_ok and (not kind or (len(langs) == 1 and not names)) else (kind or next(iter(opts)))
        eng = ui.toggle(opts, value=first) \
            .props("dense no-caps")
        if not gem_ok:
            ui.label(t("설정 → AI에서 Gemini 키를 넣으면 더 자연스러운 Gemini 목소리도 고를 수 있습니다.")) \
                .classes("text-xs text-gray-500")
        with ui.row().classes("items-center gap-2 w-full no-wrap") as mac_row:
            # 'Yuna (Korean (South Korea))' → 'Yuna' 로 보이게 (값은 say에 줄 전체 이름)
            # Windows: 'Microsoft Heami Desktop' → 'Heami'
            shown = {n: re.sub(r"^Microsoft\s+|\s+Desktop$", "", re.sub(r"\s+\([^()]*\([^()]*\)\)$", "", n)) for n in names}
            voice = ui.select(shown, value=names[0] if names else None, label=t("목소리")) \
                .props("dense outlined").classes("grow")
            if not names:
                voice.set_visibility(False)
                ui.label(t("논문 언어마다 지난번 목소리(처음이면 기본 목소리)로 만듭니다.")).classes("text-xs")
        with ui.column().classes("gap-1 w-full") as qwen_col:
            async def install_qwen() -> None:
                ib.disable()
                n = ui.notification(t("Qwen3 설치하는 중… (수백 MB, 1~2분)"), spinner=True, timeout=None)
                try:
                    await run.io_bound(phone_service.qwen_install)
                except Exception as e:  # noqa: BLE001
                    ui.notify(t("Qwen3를 설치하지 못했습니다: {e}", e=e), type="negative", timeout=10000)
                    ib.enable()
                    return
                finally:
                    n.dismiss()
                qs["ready"] = True
                ui.notify(t("Qwen3를 설치했습니다 — 처음 만들 때 모델(약 2.5GB)을 한 번 받습니다."), type="positive")
                total()
            with ui.row().classes("items-center gap-2 w-full no-wrap") as qwen_inst:
                ui.label(t("이 Mac에 Qwen3 로컬 목소리를 설치합니다 — 앱과 따로 두는 작은 환경(수백 MB)이고, 처음 만들 때 "
                           "모델(약 2.5GB)을 한 번 받습니다. 인터넷 없이·무료로 돌지만 Mac 목소리보다 느립니다(한 시간 분량에 약 50분).")) \
                    .classes("text-xs grow")
                ib = ui.button(t("설치하기"), icon="download", on_click=install_qwen).props("unelevated dense no-caps")
            with ui.row().classes("items-center gap-2 w-full no-wrap") as qwen_row:
                qvoice = ui.select(list(tts.QWEN_SPEAKERS), value=phone_service.qwen_choice(langs[0]), label=t("목소리")) \
                    .props("dense outlined").classes("grow")
            # 말투 지시 — 없으면 졸린 기본 말투라 '팟캐스트 진행자처럼'이 기본. 영어로 쓰는 게 가장 잘 듣는다
            qstyle = ui.input(t("말투 지시 (영어가 잘 들어요 · 비우면 기본 말투)"), value=phone_service.qwen_style()) \
                .props("dense outlined clearable").classes("w-full text-xs")
            qstyle.bind_visibility_from(qwen_row, "visible")
        with ui.row().classes("items-center gap-2 w-full no-wrap") as gem_row:
            gmodel = ui.select({m: (t("flash-lite — 더 쌈") if "lite" in m else t("flash — 더 자연스러움"))
                                for m in tts.GEMINI_MODELS}, value=gc["model"], label=t("모델")) \
                .props("dense outlined").classes("grow")
            gvoice = ui.select(list(tts.GEMINI_VOICES), value=gc["voice"], label=t("목소리")) \
                .props("dense outlined").classes("grow")
        with ui.row().classes("items-center gap-1 w-full no-wrap"):
            ui.button(t("들어 보기"), icon="volume_up", on_click=preview).props("flat dense no-caps")
            ui.label(f"“{sample}”").classes("text-xs text-gray-500 truncate min-w-0").tooltip(sample)
        est = ui.label().classes("text-xs text-gray-600")
        # 무엇을 읽을지 — 그림·표 설명, 수식(AI가 읽는 말을 만들어 둔다 — 번역에 쓰는 키, 논문마다 몇 원)
        ao, ai_ok = phone_service.audio_opts(), await run.io_bound(phone_service.ai_ready)
        with ui.row().classes("items-center gap-3 w-full"):
            cap_cb = ui.checkbox(t("그림·표 설명도 읽기"), value=ao["captions"]).props("dense").classes("text-sm")
            math_cb = ui.checkbox(t("수식도 읽기 (AI가 읽는 말을 만듦)"), value=ao["math"] and ai_ok) \
                .props("dense").classes("text-sm")
            if not ai_ok:
                math_cb.disable()
                math_cb.tooltip(t("설정 → AI에서 키를 넣으면 수식도 말로 읽습니다 — 없으면 수식은 건너뜁니다."))
        own = [True] * len(secs)        # 섹션마다 넣을지 — 나무의 체크는 이것에서 그린다

        def total() -> None:
            chosen = ([sec for c, sec in zip(own, secs) if c] if secs
                      else [sec for p in per for sec in p])
            m = sum(sec["minutes"] for sec in chosen)
            mac_row.set_visibility(eng.value in ("mac", "win"))
            gem_row.set_visibility(eng.value == "gemini")
            qwen_col.set_visibility(eng.value == "qwen")
            qwen_inst.set_visibility(not qs["ready"])
            qwen_row.set_visibility(qs["ready"])
            txt = t("약 {m}분", m=round(m))
            if eng.value == "gemini":
                req = tts.gemini_requests([round(sec["minutes"] * 60 * tts.CPS) for sec in chosen])
                txt += t(" · 예상 비용 약 ${c} (내 Gemini 키의 계정에 청구)",
                         c=f"{tts.gemini_cost(gmodel.value, m * 60):.2f}")
                txt += t(" · 요청 약 {n}번 — 무료 등급 키는 TTS가 하루 10번쯤이라 모자랄 수 있어요", n=req)
            elif eng.value == "qwen":
                txt += t(" · 무료 · 만드는 데 약 {w}분 (이 Mac에서, 처음엔 모델 받기 더)", w=round(m / tts.QWEN_SPEED))
            else:
                txt += t(" · 무료")
            est.set_text(txt)
        eng.on_value_change(lambda _: total())
        gmodel.on_value_change(lambda _: total())
        if secs and (st["terms"] or st["found"]):
            async def fill_terms() -> None:
                empty = [k for k, inp in term_inputs.items() if not (inp.value or "").strip()]
                if not empty:
                    ui.notify(t("빈 칸이 없습니다."), type="info")
                    return
                fb.disable()
                n = ui.notification(t("AI가 읽는 법을 정하는 중… ({n}개)", n=len(empty)), spinner=True, timeout=None)
                try:
                    got = await run.io_bound(phone_service.suggest_speech_terms, roots[0], empty)
                except Exception as e:  # noqa: BLE001
                    ui.notify(t("AI로 채우지 못했습니다: {e}", e=e), type="negative")
                    return
                finally:
                    n.dismiss()
                    fb.enable()
                for k, v in got.items():
                    if k in term_inputs:
                        term_inputs[k].value = v
                ui.notify(t("{n}개를 채웠습니다 — 확인하고 고친 뒤 만들기를 누르세요.", n=len(got)), type="positive")
            saved, found = st["terms"], dict(st["found"])
            rows = list(dict.fromkeys(list(saved) + [k for k, _ in st["found"]]))
            with ui.expansion(t("발음 용어집 — 영어 용어를 어떻게 읽을지 ({n}개)", n=len(rows)), icon="record_voice_over") \
                    .props("dense").classes("w-full text-sm"):
                with ui.row().classes("items-center gap-2 w-full no-wrap"):
                    ui.label(t("비워 두면 목소리가 알아서 읽습니다. 고치면 그 말이 나오는 섹션만 다시 만듭니다.")) \
                        .classes("text-xs text-gray-500 grow")
                    fb = ui.button(t("AI로 채우기"), icon="auto_awesome", on_click=fill_terms) \
                        .props("flat dense no-caps").classes("shrink-0")
                with ui.scroll_area().style("height:200px").classes("w-full"):
                    with ui.element("div").classes("md4-terms"):
                        for k in rows:
                            ui.label(k).classes("md4-term-k").tooltip(t("{n}번 나옴", n=found.get(k, 0)))
                            term_inputs[k] = ui.input(value=saved.get(k, ""), placeholder="—") \
                                .props("dense borderless hide-bottom-space")
        elif len(roots) > 1:
            ui.label(t("논문마다 저장해 둔 발음 용어집으로 읽습니다.")).classes("text-xs text-gray-500")
        if secs:
            # 섹션 나무 — 제목 단계(h1·h2·h3)로 묶는다. 위 섹션을 누르면 그 아래가 다 따라가고, 아래가 섞이면
            # 위는 반쯤(–)으로 보인다. 펼치기 화살표로 하위 섹션을 연다(처음엔 맨 위 단계만).
            kids: list[list[int]] = [[] for _ in secs]
            stack: list[int] = []
            for i, sec in enumerate(secs):
                while stack and secs[stack[-1]].get("level", 1) >= sec.get("level", 1):
                    stack.pop()
                if stack:
                    kids[stack[-1]].append(i)
                stack.append(i)
            tops = [i for i in range(len(secs)) if not any(i in k for k in kids)]

            def under(i: int) -> list[int]:
                out = [i]
                for k in kids[i]:
                    out += under(k)
                return out
            boxes: dict[int, ui.checkbox] = {}
            busy = {"on": False}

            def paint() -> None:
                busy["on"] = True
                for i, cb in boxes.items():
                    vals = {own[j] for j in under(i)}
                    cb.value = None if len(vals) > 1 else vals.pop()
                busy["on"] = False
                total()

            def flip(i: int, v) -> None:  # noqa: ANN001
                if busy["on"]:
                    return
                for j in under(i):
                    own[j] = v is not False      # 반쯤(–)을 누르면 다 넣는다
                paint()

            def node(i: int) -> None:
                sec, sub = secs[i], under(i)
                mins = sum(secs[j]["minutes"] for j in sub)
                with ui.row().classes("items-center gap-0 w-full no-wrap"):
                    if kids[i]:
                        arrow = ui.button(icon="chevron_right").props("flat dense round size=sm") \
                            .classes("shrink-0 text-gray-500")
                    else:
                        ui.element("div").classes("shrink-0").style("width:28px")
                    boxes[i] = ui.checkbox(f"{sec['title']} · {mins:.0f}′", value=True,
                                           on_change=lambda e, i=i: flip(i, e.value)).props("dense") \
                        .classes("min-w-0")
                if kids[i]:
                    with ui.column().classes("gap-0 w-full").style("padding-left:18px") as box:
                        for k in kids[i]:
                            node(k)
                    box.set_visibility(False)

                    def toggle(_=None, box=box, arrow=arrow) -> None:  # noqa: ANN001
                        box.set_visibility(not box.visible)
                        arrow.props(f"icon={'expand_more' if box.visible else 'chevron_right'}")
                    arrow.on_click(toggle)
            ui.label(t("넣을 섹션")).classes("text-xs")
            with ui.scroll_area().style("height:240px").classes("w-full border rounded"):
                for i in tops:
                    node(i)
        else:
            ui.label(t("{n}편 — 본문 전체로 만듭니다.", n=len(roots))).classes("text-xs")
        total()
        send_cb = None
        if send is None and off_web:   # 웹 리더에 없는 논문 — 보낼지는 고른다 (있는 논문은 저절로 올린다)
            send_cb = ui.checkbox(t("웹 리더로도 보내기"), value=False).props("dense").classes("text-sm")
            send_cb.tooltip(t("Google Drive를 거쳐 웹 리더({site})에서도 듣습니다 — 논문과 메모도 함께 보냅니다",
                              site=pages.DOMAIN))
            if not phone_service.configured():
                send_cb.disable()
        elif on_web:
            ui.label(t("웹 리더에 있는 논문이라 만든 뒤 웹 리더에도 올립니다.")).classes("text-xs text-gray-500")
        with ui.row().classes("justify-end w-full gap-2"):
            ui.button(t("취소"), on_click=dlg.close).props("flat")
            ui.button(t("만들기"), icon="headphones", on_click=go).props("unelevated no-caps color=primary")
    dlg.open()
    await dlg
    if not picked["go"]:
        return
    include = [sec["row"] for c, sec in zip(own, secs) if c] if secs else None
    if secs and not include:
        ui.notify(t("넣을 섹션을 하나 이상 고르세요."), type="warning")
        return
    if include is not None and len(include) == len(secs):
        include = None
    if term_inputs:   # 발음 용어집 — 만들기 전에 저장(빈 칸은 저장하지 않는다)
        await run.io_bound(phone_service.save_speech_terms, roots[0], cur_terms())
    engine = eng.value
    if engine == "qwen" and not qs["ready"]:
        ui.notify(t("먼저 Qwen3를 설치하세요."), type="warning")
        return None
    return {"engine": engine,
            "voice": gvoice.value if engine == "gemini" else (qvoice.value if engine == "qwen"
                                                              else (voice.value if names else None)),
            "model": gmodel.value if engine == "gemini" else None,
            "style": (qstyle.value or "").strip() if engine == "qwen" else None,
            "captions": bool(cap_cb.value), "math": bool(math_cb.value), "include": include,
            "send": True if send else bool(send_cb and send_cb.value)}


def start(roots: list[Path], opts: dict, titles: dict[str, str] | None = None) -> list[Path]:
    """고른 대로 논문마다 음성 만들기 작업을 띄운다(한 번에 한 편씩). 반환: 띄운 논문 — 이미 만드는 중이면 빠진다.

    이벤트 루프 안(UI 핸들러)에서 불러야 한다."""
    started: list[Path] = []
    for root in [Path(r) for r in roots]:
        def work(job, root=root):  # noqa: ANN001, ANN202 — 워커 스레드 (화면 없이 끝까지)
            def prog(i: int, n: int, done: int, tot: int, title: str) -> None:  # noqa: ARG001
                job.progress(done, tot)
                job.detail["section"] = title
            s = phone_service.make_audio([root], opts["voice"], opts["include"], prog, engine=opts["engine"],
                                         model=opts["model"], style=opts["style"], captions=opts["captions"],
                                         math=opts["math"], send=opts["send"])
            m = round(s["seconds"] / 60)
            if s["send_error"]:
                job.message = t("음성 {m}분을 만들었지만 웹 리더에 올리지 못했습니다: {e}", m=m, e=s["send_error"])
            elif s["sent"]:
                job.message = t("음성 {m}분 · {mb:.0f}MB를 올렸습니다", m=m, mb=s["bytes"] / 1e6)
            else:
                job.message = t("음성 {m}분을 만들었습니다 — 뷰어의 듣기에서 들을 수 있어요", m=m)
            return s
        if jobs.start(root, KIND, t("음성 만들기"), work, title=(titles or {}).get(str(root), ""), gate=GATE):
            started.append(root)
    return started


async def make(host, roots: list[Path], titles: dict[str, str] | None = None, send: bool | None = None,  # noqa: ANN001
               on_connect=None) -> list[Path] | None:
    """창을 열어 고르고 → (보낸다면 Drive 연결을 확인하고) → 작업을 띄운다. 반환: 띄운 논문(취소면 None)."""
    from nicegui import ui

    roots = [Path(r) for r in roots]
    busy = [r for r in roots if jobs.is_running(r, KIND)]
    if busy and len(busy) == len(roots):
        ui.notify(t("이미 이 논문의 음성을 만드는 중입니다.") if len(roots) == 1
                  else t("{n}편은 이미 음성을 만드는 중입니다.", n=len(busy)), type="warning")
        return None
    client = ui.context.client
    if getattr(client, _OPEN_ATTR, False):   # 창을 여는 중(섹션 읽기에 몇 초) — 두 번 눌러도 창은 하나
        return None
    setattr(client, _OPEN_ATTR, True)
    try:
        opts = await ask(host, roots, send)
    finally:
        setattr(client, _OPEN_ATTR, False)
    if opts is None:
        return None
    if opts["send"] and not await ensure_drive(on_connect):
        return None
    started = start(roots, opts, titles)
    if len(started) < len(roots):
        ui.notify(t("{n}편은 이미 음성을 만드는 중입니다.", n=len(roots) - len(started)), type="warning")
    return started
