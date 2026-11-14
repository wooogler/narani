"""데스크톱 앱이 쓰는 웹 리더 동작 — 홈의 '웹 리더로 보내기·빼기', 앱을 켤 때의 자동 갱신 (NiceGUI 무관).

저장소는 Google Drive(§phone.gdrive)다. 연결 전이거나 클라이언트 값이 없으면 아무것도 하지 않는다.

웹 리더에 무엇이 있는지는 Drive의 목록(index.json)이 정답이다. 홈은 카드마다 Drive에 묻지 않도록 마지막으로
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


def _remember(index: dict | None = None, store=None, **kw: object) -> dict:  # noqa: ANN001
    """index를 주면 목록을 새로 기억한다 — store도 주면 논문마다 공유 링크 id(`share`)도 함께."""
    data = load_cache()
    if index is not None:
        old = data.get("papers") or {}
        data["papers"] = {e["key"]: {k: e.get(k) for k in ("title", "title_ko", "hash", "size", "notes_hash", "audio")}
                          for e in index.get("papers", []) if isinstance(e, dict) and e.get("key")}
        for key, e in data["papers"].items():
            sid = sync.share_id(store, key) if store is not None else (old.get(key) or {}).get("share")
            if sid:
                e["share"] = sid
        data["synced"] = time.time()
    data.update(kw)
    p = _cache_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)
    return data


def on_mobile() -> dict[str, dict]:
    """웹 리더에 있는 논문 — 논문 키(작업 디렉토리 이름) → 목록 줄. 연결을 끊었으면 비어 있다."""
    return load_cache()["papers"] if connected() else {}


def info() -> dict:
    """사이드바 '웹 리더' 머리에 보일 것 — 연결 여부·계정·편수·용량·마지막 동기화 시각."""
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
    _remember(sync.read_index(store), store, account=who)
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
    """고른 논문들을 웹 리더로 — 더하거나 고치기만 한다(목록의 다른 논문은 그대로). 메모도 함께 오간다.
    이 컴퓨터에 만든 음성이 웹 리더 것보다 새것이면 그것도 올린다(다시 합성하지 않는다). out['audio'] = 올린 음성 수."""
    store = gdrive.open_store()
    out = sync.push(roots, store, prune=False, extra=_extra(_rows()))
    out["audio"] = sync.put_audio(store, roots, newer_only=True)["sent"]
    _remember(sync.read_index(store), store)
    return out


def remove(roots: list[Path]) -> dict:
    """고른 논문들을 웹 리더에서 뺀다 — 폰 메모는 먼저 데스크톱에 합친다."""
    store = gdrive.open_store()
    out = sync.remove(store, roots)
    _remember(sync.read_index(store), store)
    return out


# ---- 공유 링크 ----
def reader_link(key: str) -> str:
    """웹 리더에서 이 논문을 바로 여는 주소."""
    from narani.phone import bundle as bmod, pages

    return pages.SITE + "#!" + bmod.file_name(key).split("/", 1)[1][:-5]


def share_link(sid: str) -> str:
    from narani.phone import pages

    return pages.SITE + "#share=" + sid


def memo_tabs(root: Path) -> list[str]:
    """공유에 담을 수 있는 메모 탭 — 내용이 있는 것만."""
    from narani.phone import notes as nmod
    from narani.workdir import WorkDir

    return [m["name"] for m in nmod.memo_snapshot(WorkDir(root)) if m["text"].strip()]


def share(roots: list[Path], memos: list[str] | None = None, *, only_new: bool = False) -> dict[str, str]:
    """논문들의 공유 링크 — 웹 리더 목록에 먼저 맞추고(폰 하이라이트·메모를 합쳐 담으려고) 공유 파일을 올려 연다.

    memos는 담을 메모 탭(None = 전부, [] = 없음). only_new면 이미 공유한 논문은 다시 올리지 않고 있는 링크를 쓴다.
    반환: 논문 키 → 링크 (고른 순서대로)."""
    store = gdrive.open_store()
    roots = [Path(r) for r in roots]
    out: dict[str, str] = {}
    todo = [r for r in roots if not (only_new and sync.share_id(store, r.stem))]
    if todo:
        sync.push(todo, store, prune=False, extra=_extra(_rows()))
    for r in roots:
        sid = sync.share(store, r, memos) if r in todo else sync.share_id(store, r.stem)
        out[r.stem] = share_link(sid)
    _remember(sync.read_index(store), store)
    return out


def unshare(roots: list[Path]) -> int:
    """공유 끄기 — 공유 파일을 지워 링크가 더 이상 열리지 않게 한다."""
    store = gdrive.open_store()
    n = 0
    for r in roots:
        if sync.share_id(store, Path(r).stem):
            sync.unshare(store, Path(r).stem)
            n += 1
    _remember(sync.read_index(store), store)
    return n


# ---- 듣기 음성 (§phone.tts) ----
def audio_available() -> bool:
    """이 컴퓨터에서 음성을 만들 수 있는가 — m4a 인코더(Mac afconvert · PyAV)가 있으면. 어떤 목소리를 쓸지는 따로."""
    from narani.phone import tts

    return tts.codec_available()


def system_kind() -> str | None:
    """무료 내장 목소리 — 'mac'(say) · 'win'(SAPI) · 없으면 None."""
    from narani.phone import tts

    return tts.system_voice_kind()


def _system_voice(kind: str, name: str, rate: int | None = None):  # noqa: ANN202
    from narani.phone import tts

    return (tts.WinVoice if kind == "win" else tts.MacVoice)(name, rate)


def voices(lang: str, kind: str | None = None) -> list[str]:
    """그 언어의 내장 목소리(Mac·Windows) — 지난번에 고른 것을 맨 앞에."""
    from narani.phone import tts

    kind = kind or system_kind()
    names = tts.win_voices(lang) if kind == "win" else (tts.mac_voices(lang) if kind == "mac" else [])
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
    elif engine == "win":
        pcm, rate = tts.WinVoice(voice).synth([text])[0], tts.RATE
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


def _engines(roots: list[Path], voice: str | None, engine: str, model: str | None,
             style: str | None) -> tuple[list[tuple[Path, object]], dict]:
    """논문마다 쓸 엔진 + 다음에 기억할 고름(voices·gemini·qwen). 엔진은 논문마다 새로(Qwen 일꾼은 build가 닫는다)."""
    from narani.phone import tts

    out: list[tuple[Path, object]] = []
    chosen: dict[str, str] = {}
    gem = qwen = None
    if engine == "gemini":
        g = gemini_choice()
        gem = {"voice": voice or g["voice"], "model": model or g["model"]}
    for root in roots:
        root = Path(root)
        if gem:
            out.append((root, tts.GeminiVoice(gem["voice"], gem["model"])))
            continue
        if engine == "qwen":
            if not tts.qwen_ready():
                raise RuntimeError("Qwen3 로컬 목소리가 설치되지 않았습니다")
            lang = paper_lang(root)
            qwen = {"speaker": voice or qwen_choice(lang), "style": qwen_style() if style is None else style}
            out.append((root, tts.QwenVoice(qwen["speaker"], lang, style=qwen["style"])))
            continue
        kind = engine if engine in ("mac", "win") and (tts.WinVoice if engine == "win" else tts.MacVoice).available() \
            else system_kind()
        if not kind:
            raise RuntimeError("이 컴퓨터에는 무료 내장 목소리가 없습니다 — Gemini나 Qwen3로 만드세요")
        lang = paper_lang(root)
        name = voice or (voices(lang, kind) or [""])[0]
        if not name:
            raise RuntimeError(f"이 컴퓨터에 '{lang}' 목소리가 없습니다")
        chosen[lang] = name
        out.append((root, _system_voice(kind, name)))
    keep: dict = {"voices": {**(load_cache().get("voices") or {}), **chosen}}
    if gem:
        keep["gemini"] = gem
    elif qwen:
        keep["qwen"] = qwen
    return out, keep


def make_local(roots: list[Path], voice: str | None = None, include: list[int] | None = None,
               on_progress=None, engine: str = "mac", model: str | None = None,  # noqa: ANN001
               style: str | None = None, captions: bool = True, math: bool = True) -> dict:
    """논문들의 듣기 음성을 이 컴퓨터에 만든다 — Drive 없이. 뷰어의 듣기가 바로 쓰고, 웹 리더로 보낼 때 그대로 올린다.

    engine: mac·win(무료 내장 목소리 — voice가 없으면 논문 언어마다 지난번 목소리; 이 컴퓨터에 없는 쪽을 주면
    있는 쪽으로) | qwen(무료·로컬) | gemini(유료 — 사용자 키, voice·model). include는 논문 하나일 때 넣을 섹션 행들.
    on_progress(논문 순번, 논문 수, 만든 문장, 만들 문장, 섹션 제목). 반환: {made, seconds, bytes}.
    """
    from narani.ui import listen

    roots = [Path(r) for r in roots]
    jobs, keep = _engines(roots, voice, engine, model, style)
    if math:   # 수식을 말로 — 아직 읽는 말이 없는 수식만 AI에게(논문마다 저장). 키가 없거나 실패하면 수식은 건너뛴다
        for root, _engine in jobs:
            prepare_math(root, captions)
    made = nbytes = 0
    seconds = 0.0
    for n, (root, eng) in enumerate(jobs):
        def prog(d: int, total: int, title: str, n: int = n) -> None:
            if on_progress:
                on_progress(n, len(jobs), d, total, title)
        out = listen.build(root, eng, include if len(roots) == 1 else None, prog, captions=captions, math=math)
        made += 1
        nbytes += out["bytes"]
        seconds += out["tl"]["duration"]
    _remember(None, audio_opts={"captions": captions, "math": math}, **keep)
    return {"made": made, "seconds": seconds, "bytes": nbytes}


def send_audio(roots: list[Path], *, newer_only: bool = False) -> dict:
    """논문들을 웹 리더로 보내고(번들·메모를 맞춘 뒤) 이 컴퓨터에 만든 음성을 올린다 — 다시 합성하지 않는다.
    반환: sync.put_audio의 {sent, bytes, missing, none}."""
    store = gdrive.open_store()
    roots = [Path(r) for r in roots]
    sync.push(roots, store, prune=False, extra=_extra(_rows()))
    out = sync.put_audio(store, roots, newer_only=newer_only)
    _remember(sync.read_index(store), store)
    return out


def audio_state(root: Path) -> dict:
    """뷰어 듣기 막대의 작은 상태 — 이 컴퓨터에 만든 음성과 웹 리더.

    {local: {voice, duration, built, stale(본문이 그 뒤에 바뀜 — 바뀐 섹션만 다시 만들면 된다)} | None,
     web: {configured, connected, on(웹 리더에 있음), audio: 'same'(이 음성과 같음) | 'other' | None}}"""
    from narani.phone import bundle as bmod
    from narani.ui import listen

    root = Path(root)
    local = None
    got = listen.local_audio(root)
    if got:
        m4a, tl = got
        rec = listen.local_record(root)
        local = {"voice": str(tl.get("voice") or ""), "duration": float(tl.get("duration") or 0),
                 "built": float(rec.get("built") or m4a.stat().st_mtime), "hash": rec.get("hash"),
                 "stale": tl.get("src") != bmod.content_hash(root)}
    e = on_mobile().get(root.stem)
    a = (e or {}).get("audio") or None
    same = bool(a and local and (a.get("hash") == local["hash"]
                                 or (a.get("voice") == local["voice"] and float(a.get("built") or 0) == local["built"])))
    return {"local": local, "web": {"configured": configured(), "connected": connected(), "on": e is not None,
                                    "audio": ("same" if same else "other") if a else None}}


def make_audio(roots: list[Path], voice: str | None = None, include: list[int] | None = None,
               on_progress=None, engine: str = "mac", model: str | None = None,  # noqa: ANN001
               style: str | None = None, captions: bool = True, math: bool = True, send: bool = True) -> dict:
    """듣기 음성을 이 컴퓨터에 만들고(make_local) 웹 리더로 보낸다(send_audio).

    send=True면 고른 논문 모두를 보낸다(웹 리더에 없던 논문도 더한다). False면 이미 웹 리더에 있는 논문만 — 그
    논문의 웹 리더 음성이 옛것으로 남지 않게. 보내기가 실패해도(오프라인·연결 만료) 만든 음성은 이 컴퓨터에 남고
    send_error에 까닭을 담는다. 반환: {made, seconds, bytes(올린 양), sent, send_error}."""
    roots = [Path(r) for r in roots]
    out = make_local(roots, voice, include, on_progress, engine, model, style, captions, math)
    there = on_mobile()
    targets = roots if send else [r for r in roots if r.stem in there]
    out.update(bytes=0, sent=0, send_error=None)
    if targets:
        try:
            s = send_audio(targets)
            out.update(bytes=s["bytes"], sent=s["sent"])
        except Exception as e:  # noqa: BLE001 — 음성은 이미 이 컴퓨터에 있다; 다음 보내기·켤 때 맞추기에 다시 올린다
            log.warning("만든 음성을 웹 리더에 올리지 못함: %s", e)
            out["send_error"] = str(e) or type(e).__name__
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
    from narani.ui import listen

    for r in roots:   # 이 컴퓨터의 사본(데스크톱 뷰어의 듣기)도 함께
        listen.drop_local(Path(r))
    store = gdrive.open_store()
    n = sync.remove_audio(store, [Path(r).stem for r in roots])
    _remember(sync.read_index(store), store)
    return n


def _remake_stale_audio(store) -> int:  # noqa: ANN001
    """본문이 바뀌어 옛 판이 된 무료 음성(Mac·Windows·Qwen)을 다시 만든다 — 섹션 캐시로 바뀐 섹션만. 유료(Gemini)는
    건드리지 않는다. 엔진마다 이 컴퓨터에서 되는 것만(Mac에서 만든 음성은 Windows에서 다시 만들지 않는다)."""
    from narani.phone import tts
    rows = {r["name"]: r["root"] for r in _rows()}
    jobs = []
    for e in sync.read_index(store)["papers"]:
        a = e.get("audio") if isinstance(e, dict) else None
        voice = str((a or {}).get("voice", ""))
        if not a or a.get("src") == e.get("hash") or e.get("key") not in rows:
            continue
        root = rows[e["key"]]
        opts = (bool(a.get("captions")), bool(a.get("math")))   # 옛 음성은 둘 다 꺼진 채 만들었다
        if voice.startswith(("mac:", "win:")) and (tts.WinVoice if voice.startswith("win:") else tts.MacVoice).available():
            name, _, rate = voice[4:].partition("@")
            jobs.append((root, _system_voice(voice[:3], name, int(rate) if rate else None), a.get("include"), opts))
        elif voice.startswith("qwen:") and tts.QwenVoice.available():
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
    """웹 리더에 있는 논문을 맞춘다 — 데스크톱에서 바뀐 논문은 다시 올리고, 폰 메모는 합치고 데스크톱 메모는
    올린다(sync.push가 지문으로 바뀐 것만 다시 굽는다). 데스크톱에서 지운 논문은 목록에 그대로 둔다."""
    store = gdrive.open_store()
    keys = {e.get("key") for e in sync.read_index(store)["papers"] if isinstance(e, dict)}
    rows = _rows()
    roots = [r["root"] for r in rows if r["name"] in keys]
    out = sync.push(roots, store, prune=False, extra=_extra(rows)) if roots else {
        "uploaded": 0, "skipped": 0, "removed": 0, "bytes": 0, "papers": len(keys), "merged": 0, "notes": 0}
    try:   # 이 컴퓨터에서 만들고 못 올린 음성(오프라인이었음 등)을 먼저 올린다 — 그래야 옛 음성을 다시 만들지 않는다
        out["sent_audio"] = sync.put_audio(store, roots, newer_only=True)["sent"] if roots else 0
    except Exception as e:  # noqa: BLE001
        log.info("만든 음성을 올리지 못함: %s", e)
        out["sent_audio"] = 0
    try:
        out["audio"] = _remake_stale_audio(store)
    except Exception as e:  # noqa: BLE001 — 음성을 못 만들어도 본문·메모 동기화는 끝났다
        log.info("옛 음성을 다시 만들지 못함: %s", e)
        out["audio"] = 0
    _remember(sync.read_index(store), store)
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
        log.info("웹 리더 동기화를 건너뜀: %s", e)
        return None
    if out["uploaded"] or out["merged"] or out["notes"]:
        log.info("웹 리더 동기화: 다시 올림 %d편, 폰 메모 %d개 합침, 메모 파일 %d개 올림",
                 out["uploaded"], out["merged"], out["notes"])
    return out
