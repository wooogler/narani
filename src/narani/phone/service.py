"""데스크톱 앱이 쓰는 모바일 리더 동작 — 홈의 '모바일로 보내기·빼기', 앱을 켤 때의 자동 갱신 (NiceGUI 무관).

저장소는 Google Drive(§phone.gdrive)다. 연결 전이거나 클라이언트 값이 없으면 아무것도 하지 않는다.

모바일에 무엇이 있는지는 Drive의 목록(index.json)이 정답이다. 홈은 카드마다 Drive에 묻지 않도록 마지막으로
본 목록을 `~/.config/narani/mobile.json`에 기억해 두고 그것을 읽는다 — 보내기·빼기·켤 때 갱신한 뒤 고쳐 쓴다.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from narani import config
from narani.phone import gdrive, sync

log = logging.getLogger(__name__)


def configured() -> bool:
    """Google 클라이언트 값이 있는지 (연결을 시작할 수 있는지)."""
    ids = gdrive.client_ids()
    return bool(ids["google_desktop_client_id"] and ids["google_desktop_client_secret"])


def connected() -> bool:
    return gdrive.load_token() is not None


# ---- 마지막으로 본 모바일 목록 ----
def _cache_path() -> Path:
    return config.CONFIG_DIR / "mobile.json"


def load_cache() -> dict:
    """{account, synced, papers: {논문 키: 목록 줄}} — 없거나 깨졌으면 빈 값."""
    try:
        data = json.loads(_cache_path().read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("papers"), dict):
            return data
    except (OSError, ValueError):
        pass
    return {"account": "", "synced": 0.0, "papers": {}}


def _remember(index: dict | None = None, **kw: object) -> dict:
    data = load_cache()
    if index is not None:
        data["papers"] = {e["key"]: {k: e.get(k) for k in ("title", "title_ko", "hash", "size", "notes_hash", "audio")}
                          for e in index.get("papers", []) if isinstance(e, dict) and e.get("key")}
        data["synced"] = time.time()
    data.update(kw)
    p = _cache_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)
    return data


def on_mobile() -> dict[str, dict]:
    """모바일에 있는 논문 — 논문 키(작업 디렉토리 이름) → 목록 줄. 연결을 끊었으면 비어 있다."""
    return load_cache()["papers"] if connected() else {}


def info() -> dict:
    """사이드바 '모바일' 머리에 보일 것 — 연결 여부·계정·편수·용량·마지막 동기화 시각."""
    c = load_cache()
    papers = c["papers"] if connected() else {}
    audio = [e["audio"] for e in papers.values() if e.get("audio")]
    return {"configured": configured(), "connected": connected(), "account": c.get("account", ""),
            "count": len(papers), "bytes": sum(int(e.get("size") or 0) for e in papers.values())
            + sum(int(a.get("bytes") or 0) for a in audio),
            "audio": len(audio), "synced": float(c.get("synced") or 0)}


# ---- 동작 ----
def connect(open_url=None) -> str:  # noqa: ANN001 — 브라우저 여는 함수 (기본 webbrowser.open)
    """브라우저에서 Google 로그인 → 연결. 반환: 연결한 계정 이메일. 실패하면 DriveError."""
    ids = gdrive.client_ids()
    kw = {"open_url": open_url} if open_url else {}
    gdrive.authorize(ids["google_desktop_client_id"], ids["google_desktop_client_secret"], **kw)
    store = gdrive.open_store()
    store.folder_id()
    who = store.account()
    _remember(sync.read_index(store), account=who)
    return who


def disconnect() -> bool:
    """연결 끊기 — 토큰을 반납하고 기억해 둔 목록도 지운다. Drive의 파일은 그대로."""
    done = gdrive.forget()
    _cache_path().unlink(missing_ok=True)
    return done


def _rows() -> list[dict]:
    from narani.workdir import recent_workdirs

    return recent_workdirs(config.resolve_workspace(), limit=100_000, include_hidden=True)


def _extra(rows: list[dict]) -> dict[str, dict]:
    return {r["name"]: {"pinned": r["pinned"]} for r in rows}


def send(roots: list[Path]) -> dict:
    """고른 논문들을 모바일로 — 더하거나 고치기만 한다(목록의 다른 논문은 그대로). 메모도 함께 오간다."""
    store = gdrive.open_store()
    out = sync.push(roots, store, prune=False, extra=_extra(_rows()))
    _remember(sync.read_index(store))
    return out


def remove(roots: list[Path]) -> dict:
    """고른 논문들을 모바일에서 뺀다 — 폰 메모는 먼저 데스크톱에 합친다."""
    store = gdrive.open_store()
    out = sync.remove(store, roots)
    _remember(sync.read_index(store))
    return out


# ---- 듣기 음성 (§phone.tts) ----
def audio_available() -> bool:
    from narani.phone import tts

    return tts.MacVoice.available()


def voices(lang: str) -> list[str]:
    """그 언어의 Mac 목소리 — 지난번에 고른 것을 맨 앞에."""
    from narani.phone import tts

    names = tts.mac_voices(lang)
    last = (load_cache().get("voices") or {}).get(lang)
    if last in names:
        names.remove(last)
        names.insert(0, last)
    return names


def gemini_ready() -> bool:
    """Gemini 키가 있는지 — 유료 목소리를 고를 수 있는지."""
    return bool(config.resolve_key("gemini"))


def gemini_choice() -> dict:
    """지난번에 고른 Gemini 목소리·모델 (처음이면 Kore · flash-lite)."""
    from narani.phone import tts

    g = load_cache().get("gemini") or {}
    model = g.get("model") if g.get("model") in tts.GEMINI_MODELS else next(iter(tts.GEMINI_MODELS))
    voice = g.get("voice") if g.get("voice") in tts.GEMINI_VOICES else tts.GEMINI_VOICES[0]
    return {"voice": voice, "model": model}


def qwen_status() -> dict:
    """Qwen3 로컬 목소리 — 이 Mac에서 되는지(Apple Silicon), 깔려 있는지."""
    from narani.phone import tts

    return {"supported": tts.qwen_supported(), "ready": tts.qwen_ready()}


def qwen_install() -> None:
    from narani.phone import tts

    tts.qwen_install()


def qwen_choice(lang: str) -> str:
    """지난번 Qwen 화자 (처음이면 언어마다 기본 — 한국어 Sohee)."""
    from narani.phone import tts

    q = load_cache().get("qwen") or {}
    sp = q.get("speaker")
    return sp if sp in tts.QWEN_SPEAKERS else tts.QWEN_DEFAULT_SPEAKER.get(lang, "Ryan")


def qwen_style() -> str:
    """지난번 Qwen 말투 지시 (처음이면 기본 — 팟캐스트 진행자처럼 밝게). 빈 문자열이면 지시 없이."""
    from narani.phone import tts

    q = load_cache().get("qwen") or {}
    return q["style"] if isinstance(q.get("style"), str) else tts.QWEN_STYLE


def voice_preview(engine: str, voice: str, lang: str, text: str, model: str | None = None,
                  style: str | None = None) -> bytes:
    """목소리 들어 보기 — 한 문장을 WAV 바이트로. gemini는 몇 원어치, qwen은 처음이면 모델을 올리느라 몇십 초."""
    import io
    import wave

    from narani.phone import tts

    if engine == "qwen":
        v = tts.QwenVoice(voice, lang, style=style)
        try:
            pcm = v.synth_units([tts.Unit(0, 0, 0, text, end_of_block=True)])[0]
        finally:
            v.close()
        rate = v.sample_rate
    else:
        return gemini_preview(voice, model or "", text)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def gemini_preview(voice: str, model: str, text: str) -> bytes:
    """목소리 들어 보기 — 한 문장을 만들어 WAV 바이트로 (몇 원어치)."""
    import io
    import wave

    from narani.phone import tts

    pcm = tts.GeminiVoice(voice, model).synth_units([tts.Unit(0, 0, 0, text, end_of_block=True)])[0]
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(tts.GeminiVoice.sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


def paper_lang(root: Path) -> str:
    """읽을 언어 — 번역이 있으면 번역 언어, 없으면 en."""
    from narani.workdir import WorkDir

    wd = WorkDir(Path(root))
    return wd.lang if wd.ko_md.exists() else "en"


def sections(root: Path) -> list[dict]:
    """논문 하나의 읽을 섹션 — 음성 만들기 창의 고르기 나무. [{row, title, level, minutes, first(첫 본문 문장)}]"""
    from narani.phone import bundle as bmod
    from narani.phone import tts

    _side, secs = tts.plan(bmod.build_paper(Path(root)))
    return [{"row": s.row, "title": s.title, "level": s.level, "minutes": tts.minutes([s]),
             "first": tts.strip_math(next((u.text for u in s.units if not u.head), ""))} for s in secs]


def speech_terms(root: Path) -> dict:
    """발음 용어집 창에 보일 것 — 저장된 표 + 이 논문 읽을 글에 나오는 로마자 용어(자주 나온 순)."""
    from narani.phone import bundle as bmod
    from narani.phone import tts

    _side, secs = tts.plan(bmod.build_paper(Path(root)))
    return {"terms": tts.load_terms(root), "found": tts.latin_terms(secs)}


def save_speech_terms(root: Path, terms: dict[str, str]) -> None:
    from narani.phone import tts

    tts.save_terms(root, terms)


def suggest_speech_terms(root: Path, terms: list[str]) -> dict[str, str]:
    """아직 읽기가 없는 용어들 → AI가 제안한 읽기 (번역에 쓰는 AI 키로, 몇 원어치)."""
    from narani.phone import tts

    return tts.suggest_readings(terms, paper_lang(root))


def make_audio(roots: list[Path], voice: str | None = None, include: list[int] | None = None,
               on_progress=None, engine: str = "mac", model: str | None = None,
               style: str | None = None, captions: bool = True, math: bool = True) -> dict:  # noqa: ANN001
    """논문들의 듣기 음성을 만들어 올린다 — 먼저 번들·메모를 맞추고(send), 음성은 바뀐 섹션만 새로 만든다.

    engine: mac(무료 — voice가 없으면 논문 언어마다 지난번 목소리) | gemini(유료 — 사용자 키, voice·model).
    include는 논문 하나일 때 넣을 섹션 행들.
    """
    from narani.phone import tts

    store = gdrive.open_store()
    sync.push(roots, store, prune=False, extra=_extra(_rows()))
    jobs = []
    chosen: dict[str, str] = {}
    gem = qwen = None
    if engine == "gemini":
        g = gemini_choice()
        gem = {"voice": voice or g["voice"], "model": model or g["model"]}
    for root in roots:
        inc = include if len(roots) == 1 else None
        if gem:
            jobs.append((Path(root), tts.GeminiVoice(gem["voice"], gem["model"]), inc))
            continue
        if engine == "qwen":
            if not tts.qwen_ready():
                raise RuntimeError("Qwen3 로컬 목소리가 설치되지 않았습니다")
            lang = paper_lang(root)
            qwen = {"speaker": voice or qwen_choice(lang), "style": qwen_style() if style is None else style}
            jobs.append((Path(root), tts.QwenVoice(qwen["speaker"], lang, style=qwen["style"]), inc))
            continue
        lang = paper_lang(root)
        name = voice or (voices(lang) or [""])[0]
        if not name:
            raise RuntimeError(f"이 컴퓨터에 '{lang}' 목소리가 없습니다")
        chosen[lang] = name
        jobs.append((Path(root), tts.MacVoice(name), inc))
    if math:   # 수식을 말로 — 아직 읽는 말이 없는 수식만 AI에게(논문마다 저장). 키가 없거나 실패하면 수식은 건너뛴다
        for root, _engine, _inc in jobs:
            prepare_math(root, captions)
    out = sync.push_audio(store, jobs, on_progress, captions=captions, math=math)
    c = load_cache()
    extra = {"gemini": gem} if gem else ({"qwen": qwen} if qwen else {})
    extra["audio_opts"] = {"captions": captions, "math": math}
    _remember(sync.read_index(store), voices={**(c.get("voices") or {}), **chosen}, **extra)
    return out


def ai_ready() -> bool:
    """번역에 쓰는 AI 키가 있는가 — 수식을 말로 바꾸는 데 쓴다."""
    from narani import config

    try:
        config.build_provider()
        return True
    except Exception:  # noqa: BLE001
        return False


def audio_opts() -> dict:
    """지난번 음성 만들기의 '캡션도 읽기'·'수식도 읽기' (처음이면 둘 다 켬)."""
    o = load_cache().get("audio_opts") or {}
    return {"captions": o.get("captions", True) is not False, "math": o.get("math", True) is not False}


def prepare_math(root: Path, captions: bool = True) -> int:
    """이 논문의 수식 중 읽는 말이 없는 것을 AI로 만들어 둔다. 만든 수 (키가 없거나 실패하면 0 — 그 수식은 건너뛴다)."""
    import logging

    from narani.phone import bundle as bmod
    from narani.phone import tts

    _side, secs = tts.plan(bmod.build_paper(Path(root)), captions)
    have = tts.load_math(root)
    need = [t for t in tts.math_in(secs) if t not in have]
    if not need or not ai_ready():
        return 0
    try:
        ctx = tts.math_contexts(secs)
        got = tts.speak_math(need, paper_lang(root), contexts={t: ctx.get(t, "") for t in need})
    except Exception as e:  # noqa: BLE001 — 수식 읽기 때문에 음성 전체가 실패하지 않게
        logging.getLogger(__name__).warning("수식 읽는 말을 만들지 못함: %s", e)
        return 0
    if got:
        tts.save_math(root, {**have, **got})
    return len(got)


def remove_audio(roots: list[Path]) -> int:
    store = gdrive.open_store()
    n = sync.remove_audio(store, [Path(r).stem for r in roots])
    _remember(sync.read_index(store))
    return n


def _remake_stale_audio(store) -> int:  # noqa: ANN001
    """본문이 바뀌어 옛 판이 된 무료 음성(Mac·Qwen)을 다시 만든다 — 섹션 캐시로 바뀐 섹션만. 유료(Gemini)는 건드리지 않는다."""
    from narani.phone import tts

    if not tts.MacVoice.available():
        return 0
    rows = {r["name"]: r["root"] for r in _rows()}
    jobs = []
    for e in sync.read_index(store)["papers"]:
        a = e.get("audio") if isinstance(e, dict) else None
        voice = str((a or {}).get("voice", ""))
        if not a or a.get("src") == e.get("hash") or e.get("key") not in rows:
            continue
        root = rows[e["key"]]
        opts = (bool(a.get("captions")), bool(a.get("math")))   # 옛 음성은 둘 다 꺼진 채 만들었다
        if voice.startswith("mac:"):
            name, _, rate = voice[4:].partition("@")
            jobs.append((root, tts.MacVoice(name, int(rate) if rate else None), a.get("include"), opts))
        elif voice.startswith("qwen:") and tts.qwen_ready():
            _, size, speaker, tag = (voice.split(":") + ["", "", ""])[:4]
            style = qwen_style() if tag else None   # 말투 지시는 지문만 남으니 지금 저장된 지시가 그 지문일 때만
            if tag and tts.qwen_style_tag(style) != tag:
                continue
            jobs.append((root, tts.QwenVoice(speaker, paper_lang(root), size, style=style), a.get("include"), opts))
    made = 0
    for opts in {j[3] for j in jobs}:   # 캡션·수식 설정이 같은 것끼리 — 지난번과 같은 설정으로 다시 만든다
        made += sync.push_audio(store, [j[:3] for j in jobs if j[3] == opts], captions=opts[0], math=opts[1])["made"]
    return made


def refresh() -> dict:
    """모바일에 있는 논문을 맞춘다 — 데스크톱에서 바뀐 논문은 다시 올리고, 폰 메모는 합치고 데스크톱 메모는
    올린다(sync.push가 지문으로 바뀐 것만 다시 굽는다). 데스크톱에서 지운 논문은 목록에 그대로 둔다."""
    store = gdrive.open_store()
    keys = {e.get("key") for e in sync.read_index(store)["papers"] if isinstance(e, dict)}
    rows = _rows()
    roots = [r["root"] for r in rows if r["name"] in keys]
    out = sync.push(roots, store, prune=False, extra=_extra(rows)) if roots else {
        "uploaded": 0, "skipped": 0, "removed": 0, "bytes": 0, "papers": len(keys), "merged": 0, "notes": 0}
    try:
        out["audio"] = _remake_stale_audio(store)
    except Exception as e:  # noqa: BLE001 — 음성을 못 만들어도 본문·메모 동기화는 끝났다
        log.info("옛 음성을 다시 만들지 못함: %s", e)
        out["audio"] = 0
    _remember(sync.read_index(store))
    return out


def translated_roots() -> list[Path]:
    return [r["root"] for r in _rows() if r["has_ko"] and not r["hidden"]]


def refresh_quietly() -> dict | None:
    """앱을 켤 때 뒤에서 refresh — 연결 전·오프라인·연결 만료면 조용히 넘어간다."""
    if not (configured() and connected()):
        return None
    try:
        out = refresh()
    except Exception as e:  # noqa: BLE001 — 켜는 길을 막지 않는다
        log.info("모바일 동기화를 건너뜀: %s", e)
        return None
    if out["uploaded"] or out["merged"] or out["notes"]:
        log.info("모바일 동기화: 다시 올림 %d편, 폰 메모 %d개 합침, 메모 파일 %d개 올림",
                 out["uploaded"], out["merged"], out["notes"])
    return out
