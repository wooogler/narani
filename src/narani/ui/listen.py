"""듣기 — 논문을 소리 내어 읽기. 웹 리더(§phone.web)와 데스크톱 뷰어가 함께 쓴다.

무엇을 어떤 순서로 읽을지는 한 곳(§phone.tts — plan·speech_plan)에서 정한다. 음성을 만들 때도, 웹 리더의
내장 음성도, 데스크톱 뷰어도 같은 목록을 읽는다. 문장마다 [행, s, e] — 정렬 행 번호와 그 셀 평문 안의 위치라,
웹 리더 번들의 셀과 데스크톱 뷰어의 `.anno-scope` 셀(하이라이트 앵커와 같은 좌표)에 그대로 칠한다.

- ENGINE_JS: 말하기 엔진 — 브라우저 내장 음성(Web Speech)을 `<audio>`처럼 흉내 내는 SpeechAudio, 읽을 목록 →
  시간표(webTl), 셀 안 위치 → Range(cellRange). 웹 리더와 데스크톱 뷰어가 같은 IIFE 안에 끼워 쓴다.
- 데스크톱 뷰어: 툴바 '듣기' → 아래 재생 막대. 만든 음성(웹 리더용으로 만든 m4a의 이 컴퓨터 사본)이 있으면
  그것을, 없으면(또는 고르면) 컴퓨터 목소리로. 읽는 문장을 칠하고 따라 스크롤한다.
- 서버: `/listen/{token}`(읽을 목록·만든 음성 정보), `/listen/{token}/audio`(m4a).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

LOCAL_AUDIO = "listen.m4a"   # <작업 폴더>/audio/ — 만든 음성 (웹 리더로 보낼 때 이 파일을 그대로 올린다)
LOCAL_TL = "listen.json"     # 그 시간표
LOCAL_REC = "listen-rec.json"  # 만든 설정 — {include, captions, math, built, hash} (웹 리더 목록 줄의 audio에 들어간다)


def audio_dir(root: Path) -> Path:
    from narani.workdir import WorkDir

    return WorkDir(Path(root)).root / "audio"


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def keep_local(root: Path, data: bytes, tl: dict, rec: dict | None = None) -> dict:
    """만든 음성을 이 컴퓨터에 둔다 — 데스크톱 뷰어가 바로 듣고, 웹 리더로 보낼 때 이것을 그대로 올린다.

    rec은 만든 설정(include·captions·math, 이미 올린 음성을 다시 이은 것이면 그 built). 반환: 저장한 기록."""
    import hashlib
    import time

    rec = rec or {}
    out = {"include": rec.get("include"), "captions": bool(rec.get("captions")), "math": bool(rec.get("math")),
           "built": float(rec.get("built") or time.time()), "hash": hashlib.sha256(data).hexdigest()[:16]}
    d = audio_dir(root)
    _write(d / LOCAL_AUDIO, data)
    _write(d / LOCAL_TL, json.dumps(tl, ensure_ascii=False).encode("utf-8"))
    _write(d / LOCAL_REC, json.dumps(out).encode("utf-8"))
    return out


def drop_local(root: Path) -> None:
    for name in (LOCAL_AUDIO, LOCAL_TL, LOCAL_REC):
        (audio_dir(root) / name).unlink(missing_ok=True)


def local_record(root: Path) -> dict:
    """이 컴퓨터 음성의 만든 설정 — 기록 파일이 없는 옛 사본이면 웹 리더 기록(같은 목소리일 때)으로, 그것도 없으면 빈 값."""
    try:
        rec = json.loads((audio_dir(root) / LOCAL_REC).read_text(encoding="utf-8"))
        if isinstance(rec, dict):
            return rec
    except (OSError, ValueError):
        pass
    got = local_audio(root)
    mob = _mobile_audio_record(root) or {}
    if got and mob.get("voice") == got[1].get("voice"):
        return {k: mob.get(k) for k in ("include", "captions", "math", "built", "hash")}
    return {}


def build(root: Path, engine, include: list[int] | None = None, on_progress=None,  # noqa: ANN001
          captions: bool = False, math: bool = False, digest: str | None = None) -> dict:
    """이 논문의 듣기 음성을 이 컴퓨터에 만든다 — 섹션 캐시(audio/<키>.m4a)로 바뀐 섹션만 새로 말하고 이어 붙여
    keep_local. Drive와 무관하다. on_progress(만든 문장, 만들 문장, 섹션 제목). 반환: {tl, rec, bytes}."""
    from narani.phone import bundle as bmod
    from narani.phone import tts

    root = Path(root)
    b = bmod.build_paper(root, digest or bmod.content_hash(root))
    data, tl = tts.build(b, engine, audio_dir(root), include, on_progress, terms=tts.load_terms(root),
                         captions=captions, math=tts.load_math(root) if math else None)
    rec = keep_local(root, data, tl, {"include": include, "captions": captions, "math": math})
    return {"tl": tl, "rec": rec, "bytes": len(data)}


def local_audio(root: Path) -> tuple[Path, dict] | None:
    d = audio_dir(root)
    try:
        tl = json.loads((d / LOCAL_TL).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    m4a = d / LOCAL_AUDIO
    return (m4a, tl) if isinstance(tl, dict) and m4a.is_file() else None


class _CachedVoice:
    """섹션 캐시만으로 잇는 엔진 자리 — 이름(id)과 쉼(gaps)이 섹션 키에 들어가므로 만들 때와 같아야 한다.
    새로 말해야 할 섹션이 하나라도 있으면 실패한다(잇기만 하고 목소리를 다시 돌리지 않는다)."""

    def __init__(self, voice: str) -> None:
        from narani.phone import tts

        self.id = voice
        self.gaps = tts.GeminiVoice.gaps if voice.startswith("gemini:") else tts.GAP

    def synth(self, texts, tick):  # noqa: ANN001, ANN201
        raise LookupError("섹션 캐시에 없는 섹션")


def rebuild_local(root: Path, rec: dict | None) -> bool:
    """이 컴퓨터 사본이 없을 때 — 웹 리더용으로 만든 음성을 섹션 캐시(audio/<키>.m4a)로 다시 잇는다.

    사본을 남기기 전에 만든 음성(Drive에만 완성본)도 Qwen·Gemini를 다시 돌리지 않고 뷰어에서 듣게.
    rec은 그 음성의 기록(목소리·넣은 섹션·캡션·수식) — 만들 때와 같은 설정으로 이어야 같은 키가 나온다.
    본문이 그 뒤에 바뀌어 캐시에 없는 섹션이 있으면 False(앱을 켤 때 무료 음성은 다시 만들어지며 사본도 생긴다)."""
    from narani.phone import bundle as bmod
    from narani.phone import tts

    voice = str((rec or {}).get("voice") or "")
    if not voice:
        return False
    root = Path(root)
    try:
        data, tl = tts.build(bmod.build_paper(root, bmod.content_hash(root)), _CachedVoice(voice), audio_dir(root),
                             rec.get("include"), terms=tts.load_terms(root), captions=bool(rec.get("captions")),
                             math=tts.load_math(root) if rec.get("math") else None)
    except (LookupError, ValueError, OSError):
        return False
    keep_local(root, data, tl, rec)   # 웹 리더에 있는 그 음성을 이은 것 — 만든 때(built)도 그대로
    return True


def _mobile_audio_record(root: Path) -> dict | None:
    from narani.phone import service

    e = service.load_cache()["papers"].get(Path(root).stem) or {}
    return e.get("audio") if isinstance(e, dict) else None


def speech_plan(root: Path, side: str | None = None) -> dict:
    """이 논문의 읽을 목록(§tts.speech_plan) — 본문·용어집·수식 읽는 말이 그대로면 저장해 둔 것을 쓴다."""
    from narani.phone import bundle as bmod
    from narani.phone import tts

    root = Path(root)
    digest = bmod.content_hash(root)
    sig = f"{tts.speech_sig(digest, root)}:{side or ''}"
    cache = audio_dir(root) / f"speech-{side or 'auto'}.json"
    try:
        c = json.loads(cache.read_text(encoding="utf-8"))
        if c.get("sig") == sig:
            return c["plan"]
    except (OSError, ValueError, KeyError, AttributeError):
        pass
    b = bmod.build_paper(root, digest)
    p = tts.speech_plan(b, tts.load_terms(root), tts.load_math(root), side=side)
    _write(cache, json.dumps({"sig": sig, "plan": p}, ensure_ascii=False).encode("utf-8"))
    return p


def info(root: Path, side: str | None = None) -> dict:
    """뷰어 '듣기'가 받는 것 — {side, has_ko, plan, audio: {tl, stale, v} | None}.

    만든 음성은 읽는 쪽이 같을 때만 낸다. 본문이 그 뒤에 바뀌었으면 stale(문장 칠하기가 어긋날 수 있다)."""
    from narani.phone import bundle as bmod
    from narani.workdir import WorkDir

    root = Path(root)
    has_ko = WorkDir(root).ko_md.is_file()
    side = side if side in ("en", "ko") and (side == "en" or has_ko) else ("ko" if has_ko else "en")
    plan = speech_plan(root, side)
    out: dict = {"side": plan["side"], "has_ko": has_ko, "plan": plan, "audio": None}
    got = local_audio(root)
    if got is None and rebuild_local(root, _mobile_audio_record(root)):
        got = local_audio(root)
    if got and got[1].get("side") == plan["side"]:
        m4a, tl = got
        out["audio"] = {"tl": tl, "stale": tl.get("src") != bmod.content_hash(root),
                        "v": int(m4a.stat().st_mtime)}
    out.update(state(root))
    return out


def job_info(job) -> dict | None:  # noqa: ANN001 — jobs.Job | None
    """막대에 보일 음성 만들기 진행 — {pct(없으면 None), section, queued}. 돌지 않으면 None."""
    if job is None or not job.running:
        return None
    return {"pct": int(job.fraction * 100) if job.total else None, "section": str(job.detail.get("section") or ""),
            "queued": bool(job.detail.get("queued"))}


def state(root: Path) -> dict:
    """막대 ⋯ 메뉴의 상태 — {state: 만든 음성·웹 리더(§phone.service.audio_state) + label, job: 진행 | None}."""
    from narani.phone import service
    from narani.ui import audio_dialog, jobs

    st = service.audio_state(root)
    if st["local"]:
        st["local"]["label"] = audio_dialog.audio_label(st["local"])
    return {"state": st, "job": job_info(jobs.get(root, audio_dialog.KIND))}


def register_routes(app, wd_for) -> None:  # noqa: ANN001 — FastAPI 앱, 토큰 → WorkDir | None
    from fastapi.responses import FileResponse
    from starlette.exceptions import HTTPException

    @app.get("/listen/{token}")
    def _listen_info(token: str, side: str = ""):  # noqa: ANN202 — 읽을 목록 + 만든 음성
        wd = wd_for(token)
        if wd is None:
            raise HTTPException(status_code=404)
        return info(wd.root, side or None)

    @app.get("/listen/{token}/state")
    def _listen_state(token: str):  # noqa: ANN202 — 만든 음성·웹 리더 상태 (⋯ 메뉴)
        wd = wd_for(token)
        if wd is None:
            raise HTTPException(status_code=404)
        return state(wd.root)

    @app.get("/listen/{token}/audio")
    def _listen_audio(token: str):  # noqa: ANN202 — 만든 음성(m4a)
        wd = wd_for(token)
        got = local_audio(wd.root) if wd is not None else None
        if got is None:
            raise HTTPException(status_code=404)
        return FileResponse(got[0], media_type="audio/mp4")


# ---- 말하기 엔진 — 웹 리더와 데스크톱 뷰어가 같은 IIFE 안에 끼워 쓴다 (doc·lsGet 같은 바깥 이름에 기대지 않는다) ----
ENGINE_JS = r"""
  // ---- 말하기 엔진 (§ui/listen.py ENGINE_JS) — 웹 리더·데스크톱 뷰어 공통 ----
  var SS = window.speechSynthesis || null;
  var WCPS = {ko: 7.4, ja: 8, zh: 5.5};   // 1배속 초당 글자 수 어림(그 밖은 14) — 막대·남은 시간에만
  var WGAP = [0.25, 0.6, 0.9];            // 쉼(초): 문장 · 문단 끝 · 제목 (§tts.GAP)
  function langBase(l){ return String(l || 'en').toLowerCase().replace('_', '-').split('-')[0]; }
  function webTl(plan){   // 읽을 목록 → 음성 시간표와 같은 꼴 (+ durs: 문장마다 어림 길이)
    var cps = WCPS[langBase(plan.lang)] || 14, t = 0, k = 0, sents = [], durs = [], sections = [];
    plan.sections.forEach(function(sec){
      var start = t;
      for (var j = 0; j < sec.n; j++, k++){
        var u = plan.units[k], d = Math.max(0.6, u[3].length / cps);
        sents.push([Math.round(t * 100) / 100, u[0], u[1], u[2]]); durs.push(d);
        t += d + WGAP[u[4]] ;
      }
      sections.push({title: sec.title, row: sec.row, start: Math.round(start * 100) / 100, end: Math.round(t * 100) / 100});
    });
    return {side: plan.side, lang: plan.lang, duration: Math.round(t * 100) / 100, sections: sections, sents: sents, durs: durs, web: true};
  }
  function webVoices(lang){
    if (!SS) return [];
    var b = langBase(lang);
    return SS.getVoices().filter(function(v){ return langBase(v.lang) === b; });
  }
  function SpeechAudio(pickVoice){
    var me = this, et = document.createElement('span'), P = null, tl = null, i = 0, pos = 0, began = 0, tok = 0, tick = null, wait = null;
    function fire(t){ et.dispatchEvent(new Event(t)); }
    me.addEventListener = function(t, f, o){ et.addEventListener(t, f, o); };
    me.removeEventListener = function(t, f, o){ et.removeEventListener(t, f, o); };
    me.src = ''; me.paused = true; me.playbackRate = me.defaultPlaybackRate = 1; me.duration = 0; me.unlocked = false;
    me.load = function(){}; me.removeAttribute = function(){};
    me.setPlan = function(plan, t){ me.pause(); P = plan; tl = t; i = 0; pos = 0; me.duration = t.duration; };
    function idxAt(t){
      var S = tl.sents, lo = 0, hi = S.length - 1;
      while (lo < hi){ var m = (lo + hi + 1) >> 1; if (S[m][0] <= t + 0.05) lo = m; else hi = m - 1; }
      return lo;
    }
    Object.defineProperty(me, 'currentTime', {
      get: function(){
        if (!tl) return 0;
        if (me.paused || !began) return pos;
        return tl.sents[i][0] + Math.min((Date.now() - began) / 1000 * me.playbackRate, tl.durs[i] * 0.95);
      },
      set: function(t){
        if (!tl) return;
        var was = i, now = me.currentTime;
        i = idxAt(Math.max(0, Math.min(tl.duration, t || 0)));
        if (i === was && t > now + 1 && i < tl.sents.length - 1) i++;   // 긴 문장 안에서 앞으로(+15초) — 다음 문장으로
        pos = tl.sents[i][0];   // 문장 처음으로
        if (!me.paused) speak();
        fire('seeked'); fire('timeupdate');
      }
    });
    me.unlock = function(){   // iOS: 처음 말하기는 손가락 누르기 안에서 — 빈 말 한 번으로 길을 연다
      if (!SS || me.unlocked) return;
      me.unlocked = true;
      try { var u = new SpeechSynthesisUtterance(' '); u.volume = 0; SS.speak(u); } catch (x) {}
    };
    function stopTick(){ if (tick){ clearInterval(tick); tick = null; } }
    function speak(){
      clearTimeout(wait);
      var my = ++tok, u = P && P.units[i];
      if (!u){ finish(); return; }
      began = Date.now(); pos = tl.sents[i][0];
      // 말하는 중에 끊고 곧바로 새로 말하면 Chrome이 새 말을 삼킨다 — 끊은 뒤 잠깐 두고 말한다
      if (SS.speaking || SS.pending){ try { SS.cancel(); } catch (x) {} wait = setTimeout(function(){ if (my === tok && !me.paused) say(my, u); }, 120); }
      else say(my, u);
    }
    function say(my, u){
      var ut = new SpeechSynthesisUtterance(u[3]), v = pickVoice(P.lang);
      ut.lang = v ? v.lang : P.lang; if (v) ut.voice = v;
      ut.rate = Math.max(0.5, Math.min(2, me.playbackRate));
      ut.onend = function(){ if (my === tok) next(); };
      ut.onerror = function(ev){ if (my === tok && ev.error !== 'interrupted' && ev.error !== 'canceled') next(); };
      began = Date.now();
      SS.speak(ut);
      // onend가 안 오는 때(iOS가 말을 삼킬 때) — 어림 길이의 두 배가 지나도 조용하면 다음 문장으로
      var guard = (tl.durs[i] / me.playbackRate * 2 + 4) * 1000;
      setTimeout(function(){ if (my === tok && !me.paused && !SS.speaking && !SS.pending) next(); }, guard);
    }
    function next(){
      var u = P.units[i], gap = WGAP[u ? u[4] : 0] / me.playbackRate;
      tok++; i++; began = 0;
      if (i >= P.units.length){ finish(); return; }
      pos = tl.sents[i][0];
      var my = tok;
      wait = setTimeout(function(){ if (my === tok && !me.paused) speak(); }, gap * 1000);
    }
    function finish(){
      tok++; stopTick(); me.paused = true; began = 0; i = Math.max(0, tl.sents.length - 1); pos = tl.duration;
      fire('pause'); fire('ended');
    }
    me.play = function(){
      if (!SS || !P) return Promise.reject(new Error('no speech'));
      me.unlock();
      if (!me.paused) return Promise.resolve();
      if (pos >= tl.duration - 0.05){ i = 0; pos = 0; }
      me.paused = false;
      speak();
      stopTick(); tick = setInterval(function(){ fire('timeupdate'); }, 250);
      fire('play');
      return Promise.resolve();
    };
    me.pause = function(){
      if (me.paused) return;
      pos = me.currentTime; tok++; clearTimeout(wait); stopTick(); began = 0; me.paused = true;
      try { SS.cancel(); } catch (x) {}
      pos = tl.sents[i][0];   // 다시 ▶하면 그 문장 처음부터
      fire('pause');
    };
  }
  function cellRange(cell, s, e){
    var w = document.createTreeWalker(cell, NodeFilter.SHOW_TEXT), n, pos = 0, r = document.createRange(), started = false;
    while ((n = w.nextNode())){
      var len = n.data.length;
      if (!started && s < pos + len){ r.setStart(n, s - pos); started = true; }
      if (started && e <= pos + len){ r.setEnd(n, e - pos); return r; }
      pos += len;
    }
    return started ? (r.setEnd(cell, cell.childNodes.length), r) : null;
  }
"""


# ---- 데스크톱 뷰어 — 툴바 '듣기' → 본문 아래 재생 막대 ----
LISTEN_API = "__mdListen"   # 툴바 단추·논문 바뀜이 부르는 전역 이름

_ICON = {
    "play": "M8 5v14l11-7z",
    "pause": "M6 19h4V5H6v14zm8-14v14h4V5h-4z",
    "prev": "M6 6h2v12H6zm3.5 6 8.5 6V6z",
    "next": "M6 18l8.5-6L6 6v12zM16 6v12h2V6h-2z",
    "back": "M11 18V6l-8.5 6 8.5 6zm.5-6 8.5 6V6l-8.5 6z",
    "fwd": "M4 18l8.5-6L4 6v12zm9-12v12l8.5-6L13 6z",
    "more": "M6 10c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2zm12 0c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2zm-6 0c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2z",
    "close": "M19 6.41 17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z",
}


def _svg(name: str) -> str:
    return (f'<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">'
            f'<path fill="currentColor" d="{_ICON[name]}"/></svg>')


CSS = """
#md-listen { position: fixed; z-index: 8400; bottom: 14px; left: 50%; transform: translateX(-50%);
  display: none; align-items: center; gap: 4px; padding: 6px 10px; max-width: calc(100vw - 24px);
  background: #fff; color: #1f1e1c; border: 1px solid #e4e1da; border-radius: 14px;
  box-shadow: 0 8px 28px rgba(0,0,0,.14), 0 2px 6px rgba(0,0,0,.08); font-size: 13px; }
#md-listen.on { display: flex; }
#md-listen button { border: none; background: none; color: inherit; cursor: pointer; padding: 5px;
  border-radius: 8px; line-height: 0; }
#md-listen button:hover { background: rgba(0,0,0,.06); }
#md-listen button svg { display: block; }
#md-listen .mdl-pp { background: #2383e2; color: #fff; padding: 7px; border-radius: 50%; }
#md-listen .mdl-pp:hover { background: #1b74cf; }
#md-listen .mdl-mid { display: flex; flex-direction: column; min-width: 160px; width: 260px; margin: 0 6px; }
#md-listen .mdl-sec { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-weight: 600; }
#md-listen .mdl-row { display: flex; align-items: center; gap: 6px; color: #6b6862; font-size: 11px;
  font-variant-numeric: tabular-nums; }
#md-listen .mdl-seek { flex: 1; min-width: 0; accent-color: #2383e2; }
#md-listen select { font: inherit; font-size: 12px; border: 1px solid #e4e1da; border-radius: 6px;
  background: transparent; color: inherit; padding: 2px 4px; max-width: 150px; }
#md-listen .mdl-note { position: absolute; bottom: calc(100% + 6px); left: 0; right: 0; text-align: center;
  font-size: 12px; color: #6b6862; pointer-events: none; }
#md-listen .mdl-note span { background: #fff; border: 1px solid #e4e1da; border-radius: 8px; padding: 3px 8px; }
#md-listen .mdl-note:empty { display: none; }
#md-listen .mdl-more { position: relative; }
#md-listen .mdl-dot { position: absolute; top: 3px; right: 3px; width: 7px; height: 7px; border-radius: 50%;
  background: #f2a705; display: none; }
#md-listen .mdl-more.stale .mdl-dot { display: block; }
#md-listen .mdl-more.busy .mdl-dot { display: block; background: #2383e2; }
#md-listen .mdl-prog { position: absolute; left: 12px; right: 12px; top: 0; height: 2px; border-radius: 2px;
  overflow: hidden; display: none; }
#md-listen .mdl-prog.on { display: block; }
#md-listen .mdl-prog i, #md-listen .mdl-pbar i { display: block; height: 100%; width: 0; background: #2383e2;
  transition: width .4s; }
#md-listen .mdl-menu { position: absolute; right: 0; bottom: calc(100% + 8px); width: 290px; padding: 8px;
  background: #fff; border: 1px solid #e4e1da; border-radius: 12px; box-shadow: 0 8px 28px rgba(0,0,0,.14);
  display: flex; flex-direction: column; gap: 4px; font-size: 12.5px; }
#md-listen .mdl-menu[hidden] { display: none; }
#md-listen .mdl-st { color: #6b6862; line-height: 1.4; padding: 2px 6px; }
#md-listen .mdl-st b { color: #1f1e1c; font-weight: 600; }
#md-listen .mdl-st.warn { color: #a26500; }
#md-listen .mdl-st:empty { display: none; }
#md-listen .mdl-job { padding: 2px 6px 4px; color: #6b6862; }
#md-listen .mdl-pbar { height: 4px; border-radius: 4px; background: #e8eef7; overflow: hidden; margin: 4px 0 2px; }
#md-listen .mdl-menu hr { border: none; border-top: 1px solid #ece9e3; margin: 2px 0; width: 100%; }
#md-listen .mdl-menu button.mdl-mi { line-height: 1.3; text-align: left; padding: 6px 8px; font: inherit;
  display: flex; align-items: center; gap: 6px; }
#md-listen .mdl-menu button.mdl-mi[hidden] { display: none; }
body:has(.q-dialog) #md-listen { z-index: 5900; }   /* 대화상자(음성 만들기 창 등)가 막대 위로 */
::highlight(md-listen) { background-color: rgba(255, 214, 77, .55); }
#mdl-here { position: fixed; z-index: 9991; display: none; align-items: center; gap: 4px; padding: 5px 11px 5px 8px;
  border: none; border-radius: 999px; background: #2383e2; color: #fff; font-size: 12.5px; font-weight: 600;
  cursor: pointer; box-shadow: 0 4px 14px rgba(35,131,226,.35); }
#mdl-here.on { display: inline-flex; }
#mdl-here:hover, #md-sent-tip .st-play:hover { background: #1b74cf; }
#mdl-here svg, #md-sent-tip .st-play svg { display: block; }
#md-sent-tip.act { pointer-events: auto; }
#md-sent-tip .st-play { display: inline-flex; align-items: center; gap: 3px; margin-top: 6px; padding: 3px 10px 3px 6px;
  border: none; border-radius: 999px; background: #2383e2; color: #fff; font: inherit; font-size: 12px;
  font-weight: 600; cursor: pointer; }
.sbs-cell.mdl-now { box-shadow: inset 3px 0 0 #f2b705; }
body.md-listening #md-jump-back { bottom: 82px !important; }
body.md-listening .sbs-grid, body.md-listening .vpane { scroll-padding-bottom: 90px; }
@media (prefers-color-scheme: dark) {
  #md-listen { background: #232321; color: #e8e6e1; border-color: #3a3a37; }
  #md-listen button:hover { background: rgba(255,255,255,.08); }
  #md-listen .mdl-row, #md-listen .mdl-note { color: #9a968f; }
  #md-listen select { border-color: #3a3a37; }
  #md-listen .mdl-note span { background: #232321; border-color: #3a3a37; }
  #md-listen .mdl-menu { background: #232321; border-color: #3a3a37; }
  #md-listen .mdl-st, #md-listen .mdl-job { color: #9a968f; }
  #md-listen .mdl-st b { color: #e8e6e1; }
  #md-listen .mdl-st.warn { color: #e0a84a; }
  #md-listen .mdl-pbar { background: #2e3a4a; }
  #md-listen .mdl-menu hr { border-top-color: #3a3a37; }
  ::highlight(md-listen) { background-color: rgba(200, 160, 40, .45); }
}
"""

HTML = f"""
<button id="mdl-here" type="button">{_svg("play")}⦃여기부터 재생⦄</button>
<div id="md-listen" role="region" aria-label="⦃듣기⦄">
  <audio id="mdl-au" preload="auto"></audio>
  <div class="mdl-note"></div>
  <button data-a="prev" title="⦃이전 섹션 (Alt+[)⦄">{_svg("prev")}</button>
  <button data-a="back" title="⦃15초 뒤로⦄">{_svg("back")}</button>
  <button data-a="pp" class="mdl-pp" title="⦃재생 · 일시정지 (Alt+Space)⦄">{_svg("play")}</button>
  <button data-a="fwd" title="⦃15초 앞으로⦄">{_svg("fwd")}</button>
  <button data-a="next" title="⦃다음 섹션 (Alt+])⦄">{_svg("next")}</button>
  <div class="mdl-mid">
    <div class="mdl-sec"></div>
    <div class="mdl-row"><span class="mdl-cur">0:00</span><input class="mdl-seek" type="range" min="0" max="0" step="1" value="0"><span class="mdl-dur">0:00</span></div>
  </div>
  <select class="mdl-side" title="⦃읽을 쪽⦄"></select>
  <select class="mdl-src" title="⦃목소리⦄"></select>
  <select class="mdl-voice" title="⦃컴퓨터 목소리⦄"></select>
  <select class="mdl-rate" title="⦃속도⦄"></select>
  <button data-a="more" class="mdl-more" title="⦃음성 만들기 · 웹 리더⦄">{_svg("more")}<i class="mdl-dot"></i></button>
  <button data-a="close" title="⦃듣기 닫기⦄">{_svg("close")}</button>
  <div class="mdl-prog"><i></i></div>
  <div class="mdl-menu" hidden>
    <div class="mdl-st mdl-st-local"></div>
    <div class="mdl-st mdl-st-web"></div>
    <div class="mdl-job" hidden><div class="mdl-pbar"><i></i></div><span></span></div>
    <hr>
    <button data-a="make" class="mdl-mi" type="button">⦃음성 만들기…⦄</button>
    <button data-a="stop" class="mdl-mi" type="button" hidden>⦃만들기 멈추기⦄</button>
    <button data-a="send" class="mdl-mi" type="button">⦃웹 리더로 보내기⦄</button>
  </div>
</div>
"""

JS = r"""
(function(){
  if (window.__mdListen) return;
""" + ENGINE_JS + r"""
  var bar = document.getElementById('md-listen'), auEl = document.getElementById('mdl-au');
  if (!bar || !auEl) return;
  var HL = !!(window.CSS && CSS.highlights && window.Highlight);
  var ICON = {play: '""" + _ICON["play"] + r"""', pause: '""" + _ICON["pause"] + r"""'};
  function $(c){ return bar.querySelector(c); }
  function lsGet(k, d){ try { var v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch (x) { return d; } }
  function lsSet(k, v){ try { localStorage.setItem(k, JSON.stringify(v)); } catch (x) {} }
  var L = {tok: null, d: null, said: {}, side: null, src: null, tl: null, cur: -1, sec: -1, userScroll: 0, lastSave: 0, seq: 0, want: false};
  var au = auEl;
  function pickVoice(lang){   // 고른 목소리 — 처음이면 좋은 것(Premium·Enhanced·기본) 먼저 (웹 리더와 같은 규칙)
    var vs = webVoices(lang), name = lsGet('narani:listen:voice:' + langBase(lang), '');
    for (var i = 0; i < vs.length; i++) if (vs[i].name === name) return vs[i];
    var rank = function(v){ return /premium|enhanced|natural|neural/i.test(v.name) ? 0 : (v.default ? 1 : (v.localService ? 2 : 3)); };
    return vs.slice().sort(function(a, b){ return rank(a) - rank(b); })[0] || null;
  }
  var SA = new SpeechAudio(pickVoice);
  if (SS) try { SS.getVoices(); if (SS.addEventListener) SS.addEventListener('voiceschanged', function(){ fillVoices(); }); } catch (x) {}
  function fmtT(s){ s = Math.max(0, Math.floor(s || 0)); var h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60;
    return (h ? h + ':' + ('0' + m).slice(-2) : m) + ':' + ('0' + s % 60).slice(-2); }
  function note(t){ $('.mdl-note').innerHTML = ''; if (t){ var s = document.createElement('span'); s.textContent = t; $('.mdl-note').appendChild(s); } }
  function ppIcon(playing){ $('.mdl-pp path').setAttribute('d', playing ? ICON.pause : ICON.play); }
  function opt(sel, items, cur){
    sel.innerHTML = '';
    items.forEach(function(it){ var o = document.createElement('option'); o.value = it[0]; o.textContent = it[1]; if (String(it[0]) === String(cur)) o.selected = true; sel.appendChild(o); });
    sel.style.display = items.length > 1 ? '' : 'none';
  }
  function place(){   // 본문 칸 아래 가운데 — 왼쪽 패널·에이전트 창을 빼고
    var m = document.querySelector('.vmain') || document.querySelector('.sbs-grid');
    if (!m) return;
    var r = m.getBoundingClientRect(), x = r.left + r.width / 2;
    bar.style.left = x + 'px';
    var b = bar.getBoundingClientRect();   // 좁은 창 — 막대가 화면 밖으로 나가지 않게 안쪽으로 민다
    if (b.right > innerWidth - 12) x -= b.right - innerWidth + 12;
    else if (b.left < 12) x += 12 - b.left;
    bar.style.left = Math.max(x, b.width / 2 + 12) + 'px';
  }
  window.addEventListener('resize', place);

  // ---- 시간표 ----
  function sentAt(t){
    var S = L.tl.sents, lo = 0, hi = S.length - 1;
    while (lo < hi){ var m = (lo + hi + 1) >> 1; if (S[m][0] <= t + 0.05) lo = m; else hi = m - 1; }
    return lo;
  }
  function secAt(t){
    var S = L.tl.sections, k = 0;
    for (var i = 0; i < S.length; i++) if (S[i].start <= t + 0.05) k = i;
    return k;
  }

  // ---- 지금 문장 칠하기 · 따라 스크롤 ----
  var nowCell = null;
  function unmark(){
    if (HL) try { CSS.highlights.delete('md-listen'); } catch (x) {}
    if (nowCell){ nowCell.classList.remove('mdl-now'); nowCell = null; }
  }
  function cellFor(row, side){   // 위 칸의 셀만 (위아래 두 뷰어면 아래 칸은 복제, §ui/split_view.py)
    var all = document.querySelectorAll('.anno-scope[data-row="' + row + '"]'), any = null;
    for (var i = 0; i < all.length; i++){
      if (all[i].closest('.sbs-clone')) continue;
      if (all[i].getAttribute('data-side') === side) return {cell: all[i], exact: true};
      any = any || all[i];
    }
    return any ? {cell: any, exact: false} : null;
  }
  // 읽을 목록의 위치는 웹 리더 셀 기준이다 — 데스크톱 셀은 문단 사이 줄바꿈·펼친 인용([3, 저자 연도, 제목])만큼
  // 어긋난다. 그 근처에서 문장의 첫머리와 끝머리를 다시 찾아 맞춘다(못 찾으면 받은 위치 그대로).
  function nearest(txt, needle, at, from, to){
    var best = -1, bd = 1e9, j;
    if (needle.length < 2) return -1;
    while ((j = txt.indexOf(needle, from)) >= 0 && j <= to){ var d = Math.abs(j - at); if (d < bd){ bd = d; best = j; } from = j + 1; }
    return best;
  }
  function snap(cell, s, e, said){
    var txt = cell.textContent, words = said.replace(/^\s+|\s+$/g, '');
    var a = nearest(txt, words.slice(0, 6), s, Math.max(0, s - 80), s + 80);
    if (a < 0) return [s, e];
    var guess = e + (a - s), tail = words.replace(/[.!?。．！？…"'”’)\]]+$/, '').slice(-5);
    var z = nearest(txt, tail, guess - tail.length, a, guess + 400);
    var end = z >= 0 ? z + tail.length : guess;
    while (end < txt.length && /[.!?。．！？…"'”’)]/.test(txt.charAt(end))) end++;   // 마침표까지 칠한다
    return [a, Math.min(txt.length, end)];
  }
  function mark(i, force){
    var s = L.tl.sents[i]; unmark();
    if (!s) return;
    var f = cellFor(s[1], L.side), rect = null;
    L.cell = f ? f.cell : null;
    if (!f) return;
    if (HL && f.exact){
      var said = L.said[s[1] + ':' + s[2]], se = said ? snap(f.cell, s[2], s[3], said) : [s[2], s[3]];
      var r = cellRange(f.cell, se[0], se[1]);
      if (r){ CSS.highlights.set('md-listen', new Highlight(r)); rect = r.getBoundingClientRect(); }
    }
    if (!rect){ nowCell = f.cell; nowCell.classList.add('mdl-now'); rect = f.cell.getBoundingClientRect(); }
    var box = f.cell.closest('.sbs-grid, .vpane');
    if (!box || (!force && Date.now() - L.userScroll < 4000)) return;
    var b = box.getBoundingClientRect(), low = Math.min(b.bottom, bar.getBoundingClientRect().top) - 16;
    if (force || rect.top < b.top + 40 || rect.bottom > low)
      box.scrollBy({top: rect.top - b.top - b.height * 0.3, behavior: force ? 'auto' : 'smooth'});
  }
  ['wheel', 'touchmove', 'keydown'].forEach(function(ev){ document.addEventListener(ev, function(e){
    if (e.target && e.target.closest && e.target.closest('#md-listen')) return;
    if (ev !== 'keydown' || /^(Arrow|Page|Home|End| )/.test(e.key)) L.userScroll = Date.now();
  }, {passive: true, capture: true}); });

  // ---- 재생기 이벤트 — 지금 쓰는 쪽(<audio> 또는 내장 음성)에서 온 것만 ----
  function onAu(ev, f){ auEl.addEventListener(ev, function(){ if (au === auEl) f(); }); SA.addEventListener(ev, function(){ if (au === SA) f(); }); }
  onAu('timeupdate', function(){
    if (!L.tl) return;
    var t = au.currentTime, k = secAt(t), i = sentAt(t), seek = $('.mdl-seek');
    if (k !== L.sec){ L.sec = k; $('.mdl-sec').textContent = (L.tl.sections[k] || {}).title || ''; }
    if (i !== L.cur){ L.cur = i; mark(i); }
    if (document.activeElement !== seek){ seek.value = Math.floor(t); $('.mdl-cur').textContent = fmtT(t); }
    if (Date.now() - L.lastSave > 5000) savePos();
  });
  onAu('seeked', function(){ if (L.tl){ L.cur = sentAt(au.currentTime); mark(L.cur, true); } });
  onAu('play', function(){ ppIcon(true); note(''); });
  onAu('pause', function(){ ppIcon(false); savePos(); });
  onAu('ended', function(){ ppIcon(false); savePos(); });
  onAu('loadedmetadata', function(){
    au.playbackRate = rate();
    if (L.resume != null){ var t = L.resume; L.resume = null; if (t > 1 && t < (au.duration || 0) - 2) au.currentTime = t; }
  });

  function rate(){ return +lsGet('narani:listen:rate', 1) || 1; }
  function posKey(){ return 'narani:listen:pos:' + L.tok; }
  function savePos(){
    if (!L.tok || !L.tl) return;
    L.lastSave = Date.now();
    lsSet(posKey(), {side: L.side, src: L.src, t: au.currentTime || 0});
  }

  // ---- 열기 · 읽을 목록 받기 ----
  function stopAll(){
    try { auEl.pause(); } catch (x) {} SA.pause();
    unmark(); L.tl = null; L.cur = -1; L.sec = -1;
  }
  function load(side, src){
    var my = ++L.seq, tok = L.tok;
    stopAll();
    note(md4T('읽을 목록을 만드는 중…'));
    fetch('/listen/' + tok + (side ? '?side=' + side : '')).then(function(r){ if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function(d){ if (my === L.seq && tok === L.tok) setup(d, src); })
      .catch(function(){ if (my === L.seq) note(md4T('읽을 목록을 받지 못했어요.')); });
  }
  function setup(d, src){
    L.d = d; L.side = d.side; L.said = {};   // 행:위치 → 읽는 글 (만든 음성의 시간표엔 글이 없다)
    d.plan.units.forEach(function(u){ L.said[u[0] + ':' + u[1]] = u[3]; });
    var pref = src || lsGet('narani:listen:src', 'made');
    L.src = d.audio && pref !== 'web' ? 'made' : 'web';
    var saved = lsGet(posKey(), null);
    L.resume = saved && saved.side === L.side && saved.src === L.src ? saved.t : null;
    if (L.src === 'made'){
      L.tl = d.audio.tl; au = auEl;
      auEl.defaultPlaybackRate = rate(); auEl.src = '/listen/' + L.tok + '/audio?v=' + d.audio.v;
      note(d.audio.stale ? md4T('본문이 바뀐 뒤의 옛 음성이에요 — 문장 칠하기가 어긋날 수 있어요') : '');
    } else {
      auEl.removeAttribute('src'); try { auEl.load(); } catch (x) {}
      L.tl = webTl(d.plan); au = SA; SA.setPlan(d.plan, L.tl); SA.playbackRate = rate();
      if (L.resume != null){ SA.currentTime = L.resume; L.resume = null; }
      note(SS ? '' : md4T('이 화면은 컴퓨터 목소리(내장 음성)를 지원하지 않아요.'));
      if (SS && !webVoices(d.plan.lang).length) note(md4T('이 컴퓨터에 이 언어의 목소리가 없어요 — 시스템 설정의 손쉬운 사용 → 읽기 및 말하기에서 받을 수 있어요.'));
    }
    if (!L.tl.sents.length){ note(md4T('읽을 글이 없어요.')); return; }
    var seek = $('.mdl-seek'); seek.max = Math.floor(L.tl.duration || 0);
    $('.mdl-dur').textContent = fmtT(L.tl.duration); $('.mdl-cur').textContent = fmtT(au.currentTime);
    L.sec = secAt(au.currentTime || 0); $('.mdl-sec').textContent = (L.tl.sections[L.sec] || {}).title || '';
    opt($('.mdl-side'), d.has_ko ? [['ko', md4T('번역')], ['en', md4T('원문')]] : [], L.side);
    var srcs = [['web', md4T('컴퓨터 목소리')]];
    if (d.audio) srcs.unshift(['made', md4T('만든 음성') + ' · ' + String(d.audio.tl.voice || '').replace(/^(mac|win|qwen|gemini):/, '').split('@')[0]]);
    opt($('.mdl-src'), srcs, L.src);
    fillVoices();
    opt($('.mdl-rate'), [0.8, 1, 1.2, 1.5, 1.75, 2].map(function(r){ return [r, r + '×']; }), rate());
    $('.mdl-rate').style.display = '';
    L.cur = sentAt(au.currentTime || 0); mark(L.cur, true);
    if (d.state){ ST.state = d.state; ST.job = d.job || null; paintState(); }
    if (L.want){ L.want = false; play(); }
  }

  // ---- ⋯ 메뉴 — 만든 음성·웹 리더 상태, 음성 만들기(공용 창, §ui/audio_dialog.py)·웹 리더로 보내기 ----
  var ST = {state: null, job: null};
  function line(el, label, value, warn){
    el.textContent = ''; el.classList.toggle('warn', !!warn);
    if (!label) return;
    var b = document.createElement('b'); b.textContent = label; el.appendChild(b);
    if (value) el.appendChild(document.createTextNode(' ' + value));
  }
  function paintState(){
    var st = ST.state, j = ST.job, more = $('.mdl-more');
    if (st){
      var lo = st.local, w = st.web;
      if (lo) line($('.mdl-st-local'), md4T('만든 음성'), lo.label + (lo.stale ? ' — ' + md4T('본문이 바뀌어 바뀐 섹션을 다시 만들어야 해요') : ''), lo.stale);
      else line($('.mdl-st-local'), md4T('만든 음성 없음'), md4T('— 지금은 컴퓨터 목소리로 읽어요'));
      if (w.configured){
        var wt = !w.connected ? md4T('연결 안 됨') : (!w.on ? md4T('없음') :
          (w.audio === 'same' ? md4T('있음 · 이 음성') : (w.audio ? md4T('있음 · 다른 음성') : md4T('있음 · 음성 없음'))));
        line($('.mdl-st-web'), md4T('웹 리더'), wt);
      } else line($('.mdl-st-web'), '');
      $('[data-a="send"]').hidden = !w.configured;
      more.classList.toggle('stale', !!(lo && lo.stale));
    }
    var run = !!j;
    $('.mdl-job').hidden = !run; $('[data-a="stop"]').hidden = !run; $('[data-a="make"]').hidden = run;
    $('.mdl-prog').classList.toggle('on', run); more.classList.toggle('busy', run);
    if (run){
      var pct = j.pct == null ? 0 : j.pct;
      $('.mdl-prog i').style.width = pct + '%'; $('.mdl-pbar i').style.width = pct + '%';
      $('.mdl-job span').textContent = j.queued ? md4T('차례를 기다리는 중…')
        : md4T('음성 만드는 중') + (j.pct != null ? ' ' + j.pct + '%' : '…') + (j.section ? ' · ' + j.section : '');
    }
  }
  function refreshState(){
    var tok = L.tok;
    if (!tok) return;
    fetch('/listen/' + tok + '/state').then(function(r){ if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function(d){ if (tok === L.tok){ ST.state = d.state; ST.job = d.job; paintState(); } }).catch(function(){});
  }
  function menu(open){ $('.mdl-menu').hidden = !open; if (open) refreshState(); }
  document.addEventListener('mousedown', function(e){
    if ($('.mdl-menu').hidden || !e.target.closest || e.target.closest('.mdl-menu, .mdl-more')) return;
    menu(false);
  }, true);
  function fillVoices(){
    var sel = $('.mdl-voice');
    if (!L.d || L.src !== 'web'){ sel.style.display = 'none'; return; }
    var lang = L.d.plan.lang, v = pickVoice(lang);
    opt(sel, webVoices(lang).map(function(x){ return [x.name, x.name.replace(/\s*\(.*\)$/, '')]; }), v ? v.name : '');
  }
  function play(){
    var p = au.play();
    if (p && p.catch) p.catch(function(){ note(md4T('재생을 시작하지 못했어요 — ▶를 다시 눌러 보세요.')); });
  }
  function open(andPlay){
    bar.classList.add('on'); document.body.classList.add('md-listening'); place();
    if (L.tl){ if (andPlay) play(); return; }
    L.want = !!andPlay; load(null, null);
  }
  function close(){ menu(false); savePos(); stopAll(); bar.classList.remove('on'); document.body.classList.remove('md-listening'); note(''); L.d = null; }
  function toggle(){ if (bar.classList.contains('on')) close(); else open(false); }
  function pp(){ if (!L.tl){ open(true); return; } if (au.paused) play(); else au.pause(); }
  function jumpSec(dir){
    if (!L.tl) return;
    var S = L.tl.sections, t = au.currentTime, k = secAt(t);
    var to = dir < 0 ? (t - S[k].start > 3 || k === 0 ? k : k - 1) : Math.min(S.length - 1, k + 1);
    au.currentTime = S[to].start + 0.01;
  }

  bar.addEventListener('click', function(e){
    var b = e.target.closest('button[data-a]'); if (!b) return;
    var a = b.getAttribute('data-a');
    if (a === 'pp') pp();
    else if (a === 'close') close();
    else if (a === 'more') menu($('.mdl-menu').hidden);
    else if (a === 'make' || a === 'send' || a === 'stop'){
      menu(false);
      if (typeof emitEvent === 'function') emitEvent('md4-listen-' + a, {tok: L.tok});
    }
    else if (!L.tl) return;
    else if (a === 'prev') jumpSec(-1);
    else if (a === 'next') jumpSec(1);
    else if (a === 'back') au.currentTime = Math.max(0, au.currentTime - 15);
    else if (a === 'fwd') au.currentTime = Math.min(L.tl.duration, au.currentTime + 15);
  });
  $('.mdl-seek').addEventListener('input', function(e){ $('.mdl-cur').textContent = fmtT(+e.target.value); });
  $('.mdl-seek').addEventListener('change', function(e){ if (L.tl) au.currentTime = +e.target.value; });
  $('.mdl-side').addEventListener('change', function(e){ savePos(); load(e.target.value, L.src); });
  $('.mdl-src').addEventListener('change', function(e){ lsSet('narani:listen:src', e.target.value); savePos(); setup(L.d, e.target.value); });
  $('.mdl-voice').addEventListener('change', function(e){
    lsSet('narani:listen:voice:' + langBase(L.d.plan.lang), e.target.value);
    if (!au.paused){ var t = au.currentTime; au.pause(); au.currentTime = t; play(); }
  });
  $('.mdl-rate').addEventListener('change', function(e){
    lsSet('narani:listen:rate', +e.target.value); au.playbackRate = +e.target.value; auEl.defaultPlaybackRate = +e.target.value;
  });
  // 단축키 — 글을 쓰는 중(입력 칸·편집기)이 아닐 때만. Alt+←는 '읽던 자리로'(§ui/jump_back.py)가 쓴다
  document.addEventListener('keydown', function(e){
    if (!e.altKey || e.metaKey || e.ctrlKey || !L.tok) return;
    var t = e.target, typing = t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName) || (t.closest && t.closest('.cm-editor')));
    if (typing) return;
    if (e.code === 'Space'){ e.preventDefault(); pp(); }
    else if (e.code === 'BracketLeft' && L.tl){ e.preventDefault(); jumpSec(-1); }
    else if (e.code === 'BracketRight' && L.tl){ e.preventDefault(); jumpSec(1); }
  }, true);
  // 뷰어가 다시 그려지면(보기 바꿈·편집에서 돌아옴) 칠한 자리를 새 셀에 다시. 본문이 없으면(홈·편집 중) 멈춘다
  setInterval(function(){
    if (!bar.classList.contains('on')) return;
    place();
    var has = document.querySelector('.anno-scope');
    if (!has && !au.paused) au.pause();
    else if (has && L.tl && L.cur >= 0 && (!L.cell || !L.cell.isConnected)) mark(L.cur);
  }, 1000);

  // ---- 여기부터 재생 — 듣기 막대가 열려 있을 때 문장을 클릭하면 ----
  // 한 언어만 볼 때는 짝 문장 툴팁(app.py showTip) 안에 단추가 붙고, 툴팁이 안 뜨는 경우(원문|번역 두 칸, 짝 툴팁 꺼짐,
  // 짝 없는 문장)엔 클릭한 자리 옆에 알약을 띄운다. 읽는 쪽이 아닌 칸을 누르면 짝 문장(span[data-pair], §sentpair)으로 건너간다.
  function sideCell(row, side){   // 그 행·쪽의 셀 — 숨은 짝 셀(.sbs-ghost)도 (글은 같다)
    var all = document.querySelectorAll('.sbs-cell[data-row="' + row + '"][data-side="' + side + '"], .anno-scope[data-row="' + row + '"][data-side="' + side + '"]');
    for (var i = 0; i < all.length; i++) if (!all[i].closest('.sbs-clone')) return all[i];
    return null;
  }
  function offsetIn(cell, node, off){
    var r = document.createRange(); r.selectNodeContents(cell);
    try { r.setEnd(node, off); } catch (x) { return -1; }
    return r.toString().length;
  }
  function caretAt(x, y){
    if (document.caretPositionFromPoint){ var p = document.caretPositionFromPoint(x, y); return p ? {node: p.offsetNode, off: p.offset} : null; }
    if (document.caretRangeFromPoint){ var r = document.caretRangeFromPoint(x, y); return r ? {node: r.startContainer, off: r.startOffset} : null; }
    return null;
  }
  // 클릭한 셀 안 위치 → 읽는 쪽 셀 안 위치 {row, off}
  function toReadSide(cell, node, off){
    var row = +cell.getAttribute('data-row'), side = cell.getAttribute('data-side');
    var at = offsetIn(cell, node, off);
    if (side === L.side) return {row: row, off: at};
    var tgt = sideCell(row, L.side);
    if (!tgt) return {row: row, off: 0};
    // 짝 문장 span은 행에 마우스를 올려야 생긴다(app.py prepareRow) — 아직이면 만들게 하고, 클릭 위치를 담은 span을 찾는다
    if (!cell.querySelector('[data-pair]')) cell.dispatchEvent(new MouseEvent('mouseover', {bubbles: true}));
    var pe = null, sp = cell.querySelectorAll('[data-pair]');
    for (var i = 0; i < sp.length && !pe; i++){ var a = offsetIn(cell, sp[i], 0); if (at >= a && at < a + sp[i].textContent.length) pe = sp[i]; }
    var twin = pe ? tgt.querySelector('[data-pair="' + pe.getAttribute('data-pair') + '"]') : null;
    if (twin) return {row: row, off: offsetIn(tgt, twin, 0)};
    var n = cell.textContent.length || 1;   // 짝을 모르면 셀 안 비율로
    return {row: row, off: Math.round(Math.max(0, at) / n * tgt.textContent.length)};
  }
  function sentFor(row, off){   // 그 위치를 담은(또는 바로 앞에서 시작한) 문장 — 그 행에 읽을 문장이 없으면 다음 행의 첫 문장
    var S = L.tl.sents, best = -1, cell = sideCell(row, L.side);
    for (var k = 0; k < S.length; k++){
      if (S[k][1] < row) continue;
      if (S[k][1] > row) return best >= 0 ? best : k;
      var said = L.said[S[k][1] + ':' + S[k][2]], a = said && cell ? snap(cell, S[k][2], S[k][3], said)[0] : S[k][2];
      if (a <= off + 1 || best < 0) best = k;
      if (a > off + 1) return best;
    }
    return best;
  }
  function playFrom(cell, node, off){
    if (!L.tl || !cell) return false;
    var p = toReadSide(cell, node, off), k = sentFor(p.row, p.off);
    if (k < 0) return false;
    hideHere();
    L.userScroll = 0;
    au.currentTime = L.tl.sents[k][0] + 0.01;
    if (au.paused) play();
    return true;
  }
  var here = document.getElementById('mdl-here'), hereAt = null, hereTimer = null;
  function hideHere(){ clearTimeout(hereTimer); hereTimer = null; if (here) here.classList.remove('on'); }
  function isOpen(){ return bar.classList.contains('on') && !!L.tl; }
  if (here){
    here.addEventListener('mousedown', function(e){ e.preventDefault(); e.stopPropagation(); });
    here.addEventListener('click', function(e){ e.stopPropagation(); if (hereAt) playFrom(hereAt.cell, hereAt.node, hereAt.off); hideHere(); hereAt = null; });
    document.addEventListener('mousedown', function(e){ if (e.target !== here && !here.contains(e.target)) hideHere(); }, true);
    document.addEventListener('scroll', function(){ hideHere(); }, true);
    document.addEventListener('keydown', function(e){ if (e.key === 'Escape') hideHere(); });
    document.addEventListener('dblclick', function(){ hideHere(); });
    document.addEventListener('click', function(e){
      if (!isOpen() || e.detail !== 1 || !e.target.closest) return;
      if (e.target.closest('a, img, button, input, textarea, select, mark.md-anno, #md-listen, #md-sent-tip')) return;
      var cell = e.target.closest('.anno-scope');
      if (!cell) return;
      var sel = window.getSelection();
      if (sel && !sel.isCollapsed) return;   // 드래그 선택 — 색 막대 몫
      var c = caretAt(e.clientX, e.clientY);
      if (!c || !cell.contains(c.node)) return;
      var x = e.clientX, y = e.clientY;
      hereAt = {cell: cell, node: c.node, off: c.off};
      clearTimeout(hereTimer);
      hereTimer = setTimeout(function(){   // 더블클릭(메모)이면 두 번째 클릭이 물린다. 짝 툴팁이 떴으면 그 안의 단추로
        hereTimer = null;
        var tip = document.getElementById('md-sent-tip');
        if (tip && tip.classList.contains('open')) return;
        here.style.left = '0px'; here.style.top = '0px'; here.classList.add('on');
        var w = here.offsetWidth, h = here.offsetHeight;
        here.style.left = Math.min(Math.max(8, x - w / 2), innerWidth - w - 8) + 'px';
        here.style.top = (y - h - 14 > 8 ? y - h - 14 : y + 16) + 'px';
      }, 320);
    });
  }

  window.__mdListen = {
    isOpen: isOpen,
    // 시험·진단용 — 클릭한 자리가 읽을 목록의 어느 문장으로 가는지 (재생하지 않는다)
    probe: function(cell, node, off){ var p = toReadSide(cell, node, off), k = sentFor(p.row, p.off); return {row: p.row, off: p.off, k: k, said: k >= 0 ? L.said[L.tl.sents[k][1] + ':' + L.tl.sents[k][2]] : null}; },
    // 짝 문장 툴팁(app.py showTip)이 부른다 — 툴팁에 붙일 단추(듣기 막대가 닫혀 있으면 null)
    tipButton: function(el){
      if (!isOpen() || !el) return null;
      var cell = el.closest('.anno-scope'); if (!cell) return null;
      var b = document.createElement('button'); b.type = 'button'; b.className = 'st-play';
      b.innerHTML = '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path fill="currentColor" d="' + ICON.play + '"/></svg>';
      b.appendChild(document.createTextNode(md4T('여기부터 재생')));
      b.addEventListener('mousedown', function(e){ e.preventDefault(); e.stopPropagation(); });
      b.addEventListener('click', function(e){
        e.stopPropagation(); playFrom(cell, el, 0);
        var t = document.getElementById('md-sent-tip'); if (t) t.classList.remove('open', 'act');
      });
      return b;
    },
    setPaper: function(tok){ if (tok === L.tok) return; if (L.tok) close(); L.tok = tok; ST.state = null; ST.job = null; paintState(); },
    // 음성 만들기 진행(§ui/listen.py job_info) — 뷰어가 작업에 붙어 1초마다 민다. null이면 끝
    setJob: function(j){ ST.job = j || null; paintState(); },
    refreshState: refreshState,
    // 새 음성이 생겼다 — 열려 있으면 만든 음성으로 다시 받고, 닫혀 있으면 다음에 열 때 받는다
    reload: function(){
      lsSet('narani:listen:src', 'made'); ST.job = null; paintState();
      if (bar.classList.contains('on')){ savePos(); load(L.side, 'made'); } else { L.d = null; L.tl = null; }
    },
    toggle: toggle, open: open, playPause: pp
  };
})();
"""


def init_js(token: str | None) -> str:
    """화면이 바뀔 때 — 듣기는 그 논문의 것이라 다른 논문(또는 홈)으로 가면 닫는다."""
    return f"window.{LISTEN_API}&&window.{LISTEN_API}.setPaper({json.dumps(token)});"


def install() -> None:
    """셸 페이지가 한 번 부른다 — 재생 막대 CSS·마크업·스크립트."""
    from narani import i18n
    from narani.ui import spa

    spa.add_css(CSS)
    spa.add_body_html(i18n.html(HTML))
    spa.add_body_html(f"<script>{JS}</script>")
