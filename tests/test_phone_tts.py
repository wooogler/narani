"""논문 듣기(§phone.tts) — 읽을 글 고르기·셀 안 위치, 섹션 캐시, 음성 올리기·지우기."""

from __future__ import annotations

import json
import re
import shutil
from html.parser import HTMLParser

import pytest

pytest.importorskip("nicegui", reason="웹 UI 의존성(ui extra) 미설치")

from narani.phone import bundle, sync, tts  # noqa: E402
from narani.phone.store import LocalStore  # noqa: E402
from narani.workdir import WorkDir  # noqa: E402

EN = """# Bespoke Bots

Ann Lee ann@uni.edu University

We study how instructors customize chatbots for their courses. It matters a lot for teaching and learning today.

## CCS Concepts

Human-centered computing.

## 1 Introduction

Instructors build bots [1](#ref-1). We ran a study with $N=8$ people. The model is $\\sum_{i=1}^{n} w_i x_i + b$ here.

> **Figure 1:** The interface.

## 2 Method

We interviewed teachers. See https://example.com for data.

## References

<a id="ref-1"></a>[1] Lee, M. 2022. CoAuthor. CHI.
"""

KO = """# 맞춤형 봇

Ann Lee ann@uni.edu University

우리는 교수자가 자기 수업에 맞게 챗봇을 어떻게 맞춤화하는지 연구한다. 이는 오늘날 가르치고 배우는 일에 매우 중요하다.

## CCS 개념

인간 중심 컴퓨팅.

## 1 서론

교수자는 봇을 만든다 [1](#ref-1). 우리는 $N=8$ 명과 연구했다. 모델은 $\\sum_{i=1}^{n} w_i x_i + b$ 이다.

> **Figure 1:** 인터페이스.

## 2 방법

우리는 교사를 면담했다. 자료는 https://example.com 에 있다.

## References

<a id="ref-1"></a>[1] Lee, M. 2022. CoAuthor. CHI.
"""


def _wd(tmp_path, name: str = "2026_Bots") -> WorkDir:
    wd = WorkDir(tmp_path / "ws" / f"{name}.md4")
    wd.ensure()
    wd.en_md.write_text(EN, encoding="utf-8")
    wd.ko_md.write_text(KO, encoding="utf-8")
    return wd


class _CellText(HTMLParser):
    """시험용 — 행마다 번역 셀의 textContent (브라우저와 같게: 태그 안 글자를 모두 이어 붙인다)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text: dict[int, str] = {}
        self.row = None
        self.depth = 0

    def handle_starttag(self, tag, attrs):  # noqa: ANN001
        a = dict(attrs)
        if tag == "section" and (a.get("id") or "").startswith("p0-r"):
            self.row = int(a["id"][4:])
        elif tag == "div" and self.depth:
            self.depth += 1
        elif tag == "div" and "cell ko" in (a.get("class") or ""):
            self.depth = 1
            self.text[self.row] = ""

    def handle_endtag(self, tag):  # noqa: ANN001
        if tag == "div" and self.depth:
            self.depth -= 1

    def handle_data(self, data):  # noqa: ANN001
        if self.depth:
            self.text[self.row] += data


def test_plan_reads_body_and_skips_meta_citations_figures_refs(tmp_path):
    b = bundle.build_paper(_wd(tmp_path).root)
    side, secs = tts.plan(b)
    assert side == "ko"
    assert [s.title for s in secs] == [b["title_ko"], "1 서론", "2 방법"]
    texts = [u.text for s in secs for u in s.units]
    joined = " ".join(texts)
    assert "이는 오늘날 가르치고 배우는 일에 매우 중요하다." in texts            # 초록은 읽는다
    assert "교수자는 봇을 만든다." in texts                                    # 인용을 뺀 자리에 공백이 남지 않는다
    for gone in ("ann@uni.edu", "인간 중심", "Lee, M.", "Figure 1", "[1", "https://", "∑"):
        assert gone not in joined
    assert "N=8" in joined.replace(" ", "")                                   # 짧은 인라인 수식은 읽는다
    assert secs[1].units[0].head and secs[1].units[0].text == "1 서론"
    # 셀 안 위치가 리더의 textContent와 맞는다 — 그 구간에 읽을 글자가 차례로 다 있다
    cells = _CellText()
    cells.feed(b["html"])
    for u in (u for s in secs for u in s.units):
        piece = cells.text[u.row][u.s:u.e].replace(" ", "")
        want, i = u.text.replace(" ", ""), 0
        for ch in piece:
            if i < len(want) and ch == want[i]:
                i += 1
        assert i == len(want) and piece[0] == want[0] and piece[-1] == want[-1], (u.text, piece)


class FakeVoice:
    """say 대신 — 글자 수만큼의 무음 PCM. 몇 번 합성했는지 센다."""

    kind = "mac"
    id = "fake:test"

    def __init__(self) -> None:
        self.calls = 0

    def synth(self, texts, on_each=None):  # noqa: ANN001
        out = []
        for t in texts:
            self.calls += 1
            out.append(b"\x00\x00" * int(tts.RATE * 0.05 * len(t)))
            if on_each:
                on_each()
        return out


needs_afconvert = pytest.mark.skipif(not shutil.which("afconvert"), reason="macOS afconvert 필요")


@needs_afconvert
def test_build_timeline_cache_and_include(tmp_path):
    wd = _wd(tmp_path)
    b = bundle.build_paper(wd.root)
    eng = FakeVoice()
    data, tl = tts.build(b, eng, wd.root / "audio")
    _, secs = tts.plan(b)
    assert data[4:8] == b"ftyp" and tl["side"] == "ko" and tl["voice"] == "fake:test"
    assert len(tl["sents"]) == sum(len(s.units) for s in secs) == eng.calls
    ts = [s[0] for s in tl["sents"]]
    assert ts == sorted(ts) and ts[0] == 0
    assert [s["row"] for s in tl["sections"]] == [s.row for s in secs]
    assert tl["sections"][-1]["end"] == pytest.approx(tl["duration"], abs=0.05)
    # 다시 만들면 캐시로 — 합성하지 않는다
    calls = eng.calls
    tts.build(b, eng, wd.root / "audio")
    assert eng.calls == calls
    # 고른 섹션만
    _, tl2 = tts.build(b, eng, wd.root / "audio", include=[secs[1].row])
    assert [s["row"] for s in tl2["sections"]] == [secs[1].row] and eng.calls == calls
    assert {s[1] for s in tl2["sents"]} <= {u.row for u in secs[1].units}


@needs_afconvert
def test_push_audio_keeps_on_resend_and_remove_cleans_up(tmp_path):
    wd = _wd(tmp_path)
    store = LocalStore(tmp_path / "store")
    sync.push([wd.root], store)
    out = sync.push_audio(store, [(wd.root, FakeVoice(), None)])
    assert out["made"] == 1 and out["seconds"] > 0
    a_name, t_name = sync.audio_names(wd.root.stem)
    entry = sync.read_index(store)["papers"][0]
    assert entry["audio"]["file"] == a_name and entry["audio"]["src"] == entry["hash"]
    assert store.read(a_name)[4:8] == b"ftyp" and json.loads(store.read(t_name))["sents"]
    # 번역을 고쳐 다시 보내도 음성은 목록에 남는다 — 지문이 달라 '옛 음성'
    wd.ko_md.write_text(KO.replace("면담했다", "만났다"), encoding="utf-8")
    sync.push([wd.root], store)
    entry = sync.read_index(store)["papers"][0]
    assert entry["audio"]["file"] == a_name and entry["audio"]["src"] != entry["hash"]
    # 음성만 지우기, 그리고 논문 빼기
    assert sync.remove_audio(store, [wd.root.stem]) == 1
    assert store.read(a_name) is None and "audio" not in sync.read_index(store)["papers"][0]
    sync.push_audio(store, [(wd.root, FakeVoice(), None)])
    sync.remove(store, [wd.root])
    assert store.read(a_name) is None and store.read(t_name) is None


def test_push_audio_needs_paper_on_mobile(tmp_path):
    wd = _wd(tmp_path)
    store = LocalStore(tmp_path / "store")
    assert sync.push_audio(store, [(wd.root, FakeVoice(), None)])["missing"] == [wd.root.stem]


def test_mac_voice_list_parsing(monkeypatch):
    out = ("Eddy (Korean (South Korea)) ko_KR    # 안녕하세요\n"
           "Yuna                ko_KR    # 안녕하세요\n"
           "Yuna (Premium)      ko_KR    # 안녕하세요\n"
           "Kyoko               ja_JP    # こんにちは\n")

    class R:
        stdout = out
    monkeypatch.setattr(tts.MacVoice, "available", staticmethod(lambda: True))
    monkeypatch.setattr(tts.subprocess, "run", lambda *a, **k: R())
    assert tts.mac_voices("ko") == ["Yuna (Premium)", "Yuna", "Eddy (Korean (South Korea))"]
    assert tts.mac_voices("ja") == ["Kyoko"]


@needs_afconvert
def test_service_make_audio_and_refresh_remakes_stale_free_voice(tmp_path, monkeypatch):
    from narani.phone import gdrive, service

    wd = _wd(tmp_path)
    store = LocalStore(tmp_path / "store")
    rows = [{"root": wd.root, "name": wd.root.stem, "pinned": False, "has_ko": True, "hidden": False}]
    monkeypatch.setattr(service.config, "CONFIG_DIR", tmp_path / "cfg")
    monkeypatch.setattr(service, "_rows", lambda: rows)
    monkeypatch.setattr(service, "configured", lambda: True)
    monkeypatch.setattr(service, "connected", lambda: True)
    monkeypatch.setattr(gdrive, "open_store", lambda: store)
    made: list[str] = []

    class Voice(FakeVoice):   # Mac 목소리 자리에 — 이름을 받고 id는 mac:<이름>
        def __init__(self, name, rate=None):  # noqa: ANN001
            super().__init__()
            self.id = f"mac:{name}"
            made.append(name)
    monkeypatch.setattr(tts, "MacVoice", Voice)
    Voice.available = staticmethod(lambda: True)
    monkeypatch.setattr(tts, "mac_voices", lambda lang: ["Yuna", "Eddy"])

    out = service.make_audio([wd.root])                      # 모바일에 없던 논문도 먼저 보낸다
    assert out["made"] == 1 and made == ["Yuna"]
    a = service.on_mobile()[wd.root.stem]["audio"]
    assert a["voice"] == "mac:Yuna" and service.info()["audio"] == 1
    assert service.voices("ko")[0] == "Yuna"                 # 지난번 목소리가 먼저
    service.make_audio([wd.root], voice="Eddy")
    assert service.voices("ko")[0] == "Eddy"
    # 데스크톱에서 다시 번역 → 켤 때 맞추기가 무료 음성을 다시 만든다
    wd.ko_md.write_text(KO.replace("면담했다", "만났다"), encoding="utf-8")
    out = service.refresh()
    assert out["uploaded"] == 1 and out["audio"] == 1 and made[-1] == "Eddy"
    e = sync.read_index(store)["papers"][0]
    assert e["audio"]["src"] == e["hash"]
    assert service.refresh()["audio"] == 0                    # 바뀐 게 없으면 다시 만들지 않는다


# ---- Gemini ----
def _tone(sec: float, rate: int = 24000) -> bytes:
    import array
    import math

    return array.array("h", (int(8000 * math.sin(i / 7)) for i in range(int(sec * rate)))).tobytes()


def _silence(sec: float, rate: int = 24000) -> bytes:
    return b"\x00\x00" * int(sec * rate)


def test_split_by_pauses_cuts_at_the_nearest_silence():
    # 문장 셋: 2초 · 1초 · 3초, 사이에 0.4초 쉼 — 글자 수는 일부러 조금 어긋나게
    pcm = _tone(2) + _silence(0.4) + _tone(1) + _silence(0.4) + _tone(3)
    parts = tts._split_by_pauses(pcm, 24000, [25, 10, 30])
    secs = [len(p) / 2 / 24000 for p in parts]
    assert len(parts) == 3 and b"".join(parts) == pcm
    assert secs[0] == pytest.approx(2.2, abs=0.08) and secs[1] == pytest.approx(1.4, abs=0.08)
    assert tts._split_by_pauses(pcm, 24000, [5]) == [pcm]


def test_gemini_voice_request_retry_and_sentence_pieces(monkeypatch):
    import base64
    import io
    import wave

    import httpx

    seen: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        seen.append(body)
        assert req.headers["x-goog-api-key"] == "k" and str(req.url) == tts.GEMINI_URL
        if len(seen) == 1:   # 분당 한도 — 잠깐 기다렸다 다시
            return httpx.Response(429, json={"error": {"message": "Rate limit exceeded. Please retry in 2s."}})
        n = len([x for x in re.split(r"(?<=[.])\s+|\n\n", body["input"][0]["content"][0]["text"]) if x.strip()])
        pcm = b"".join(_tone(0.8) + _silence(0.4) for _ in range(n))
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(24000)
            w.writeframes(pcm)
        return httpx.Response(200, json={"steps": [{"type": "model_output", "content": [
            {"type": "audio", "data": base64.b64encode(buf.getvalue()).decode()}]}]})

    sleeps: list[float] = []
    monkeypatch.setattr("time.sleep", lambda sec: sleeps.append(sec))
    v = tts.GeminiVoice("Charon", "gemini-3.8-flash-lite-tts", key="k",
                        http=httpx.Client(transport=httpx.MockTransport(handler)), workers=1)
    units = [tts.Unit(1, 0, 3, "1 서론", head=True, end_of_block=True),
             tts.Unit(2, 0, 5, "하나다.", end_of_block=False), tts.Unit(2, 6, 11, "둘이다.", end_of_block=True)]
    ticks: list[int] = []
    out = v.synth_units(units, lambda: ticks.append(1))
    assert len(out) == 3 and all(out) and len(ticks) == 3 and sleeps == [2.0]   # 429가 말한 만큼 기다렸다 다시
    texts = [b["input"][0]["content"][0]["text"] for b in seen[1:]]
    assert texts == ["1 서론\n\n하나다. 둘이다."]                               # 섹션을 한 요청으로 묶는다
    assert seen[1]["generation_config"]["speech_config"] == [{"voice": "Charon"}]
    assert seen[1]["model"] == "gemini-3.8-flash-lite-tts" and seen[1]["response_format"] == {"type": "audio"}
    assert v.id == "gemini:gemini-3.8-flash-lite-tts:Charon" and tts.GeminiVoice.sample_rate == 24000


def test_gemini_daily_quota_stops_at_once(monkeypatch):
    import httpx

    calls = []

    def handler(req):  # noqa: ANN001
        calls.append(1)
        return httpx.Response(429, json={"error": {"message": "Rate limit exceeded for model gemini-3.8-flash-tts "
                                                   "(limit: 10 requests per day on Free Tier). Please retry in 12h51m7s"}})
    monkeypatch.setattr("time.sleep", lambda s: pytest.fail("하루 한도는 기다리지 않는다"))
    v = tts.GeminiVoice(key="k", http=httpx.Client(transport=httpx.MockTransport(handler)), workers=1)
    with pytest.raises(tts.GeminiQuota) as e:
        v.synth_units([tts.Unit(1, 0, 3, "하나다.", end_of_block=True)])
    assert len(calls) == 1 and "13" in str(e.value)   # 약 13시간 뒤
    assert tts.gemini_requests([500, 3000, 3001, 0]) == 4


def test_gemini_cost_estimate():
    hour = 3600
    assert tts.gemini_cost("gemini-3.8-flash-lite-tts", hour, today="2026-10-27") == pytest.approx(0.54)
    assert tts.gemini_cost("gemini-3.8-flash-lite-tts", hour, today="2027-01-02") == pytest.approx(1.08)
    assert tts.gemini_cost("gemini-3.8-flash-tts", hour, today="2026-12-31") == pytest.approx(0.81)


@needs_afconvert
def test_service_make_audio_with_gemini_remembers_choice_and_is_not_remade(tmp_path, monkeypatch):
    from narani.phone import gdrive, service

    wd = _wd(tmp_path)
    store = LocalStore(tmp_path / "store")
    rows = [{"root": wd.root, "name": wd.root.stem, "pinned": False, "has_ko": True, "hidden": False}]
    monkeypatch.setattr(service.config, "CONFIG_DIR", tmp_path / "cfg")
    monkeypatch.setattr(service, "_rows", lambda: rows)
    monkeypatch.setattr(service, "connected", lambda: True)
    monkeypatch.setattr(service, "configured", lambda: True)
    monkeypatch.setattr(gdrive, "open_store", lambda: store)
    made = []

    class G(tts.GeminiVoice):
        def _call(self, text):  # noqa: ANN001
            made.append((self.voice, self.model))
            return b"".join(_tone(0.3) + _silence(0.3) for _ in range(max(1, text.count("."))))
    monkeypatch.setattr(tts, "GeminiVoice", G)
    out = service.make_audio([wd.root], "Puck", engine="gemini", model="gemini-3.8-flash-tts")
    assert out["made"] == 1 and made and set(made) == {("Puck", "gemini-3.8-flash-tts")}
    assert service.on_mobile()[wd.root.stem]["audio"]["voice"] == "gemini:gemini-3.8-flash-tts:Puck"
    assert service.gemini_choice() == {"voice": "Puck", "model": "gemini-3.8-flash-tts"}
    # 유료 음성은 번역이 바뀌어도 켤 때 저절로 다시 만들지 않는다(비용) — 폰은 '옛 음성'으로 안다
    wd.ko_md.write_text(KO.replace("면담했다", "만났다"), encoding="utf-8")
    n = len(made)
    assert service.refresh()["audio"] == 0 and len(made) == n


def test_finished_audio_jobs_are_reported_once():
    import asyncio

    from narani.ui import jobs

    jobs.reset()

    async def go():
        gate = jobs.Gate(1)
        a = jobs.start("/tmp/a.md4", "mobile_audio", "모바일 음성", lambda j: 1, gate=gate)
        b = jobs.start("/tmp/b.md4", "mobile_audio", "모바일 음성", lambda j: (_ for _ in ()).throw(RuntimeError("x")), gate=gate)
        assert jobs.start("/tmp/a.md4", "mobile_audio", "모바일 음성", lambda j: 1) is None   # 같은 논문은 한 번에 하나
        while a.running or b.running:
            await asyncio.sleep(0.05)
        return a, b
    a, b = asyncio.run(go())
    assert {j.status for j in jobs.finished_unseen("mobile_audio")} == {"done", "failed"} and b.error == "x"
    for j in jobs.finished_unseen("mobile_audio"):
        j.seen = True
    assert jobs.finished_unseen("mobile_audio") == []
    jobs.reset()


# ---- Qwen (로컬) — 진짜 mlx-audio 대신 같은 약속(JSON 줄 → WAV 파일)을 지키는 가짜 일꾼 ----
_FAKE_WORKER = r'''
import json, sys, wave
cfg = json.loads(sys.stdin.readline())
print("some library chatter on stdout")
print(json.dumps({"ready": True, "rate": 24000}), flush=True)
for line in sys.stdin:
    job = json.loads(line)
    if job["text"] == "boom":
        print(json.dumps({"error": "RuntimeError: boom"}), flush=True)
        continue
    with wave.open(job["out"], "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000)
        w.writeframes(b"\x01\x00" * (240 * len(job["text"])))
    print(json.dumps({"ok": True, "lang": job["language"], "speaker": job["speaker"]}), flush=True)
'''


def test_qwen_voice_talks_to_worker_sentence_by_sentence(tmp_path, monkeypatch):
    import sys

    monkeypatch.setattr(tts, "_QWEN_WORKER", _FAKE_WORKER)
    monkeypatch.setenv("NARANI_QWEN_PYTHON", sys.executable)
    monkeypatch.setattr(tts, "qwen_dir", lambda: tmp_path / "local-tts")
    monkeypatch.setattr(tts, "qwen_supported", lambda: True)
    assert tts.qwen_ready()
    v = tts.QwenVoice("Sohee", "ko")
    assert v.id == "qwen:0.6b:Sohee" and v.language == "Korean" and tts.QwenVoice("x", "ja").language == "Japanese"
    ticks = []
    out = v.synth_units([tts.Unit(0, 0, 3, "가나다", end_of_block=True), tts.Unit(1, 0, 2, "라마")], lambda: ticks.append(1))
    assert [len(p) for p in out] == [2 * 240 * 3, 2 * 240 * 2] and len(ticks) == 2 and v.sample_rate == 24000
    with pytest.raises(RuntimeError, match="boom"):
        v.synth_units([tts.Unit(0, 0, 4, "boom")])
    v.close()
    assert v._proc is None


@needs_afconvert
def test_switching_voices_keeps_the_other_voices_cache(tmp_path):
    wd = _wd(tmp_path)
    b = bundle.build_paper(wd.root)
    cache = wd.root / "audio"
    a, g = FakeVoice(), FakeVoice()
    g.id = "fake:other"
    tts.build(b, a, cache)
    n_a = len(list(cache.glob("*.m4a")))
    tts.build(b, g, cache)                       # 다른 목소리로 — 처음 목소리 캐시는 남는다
    assert len(list(cache.glob("*.m4a"))) == 2 * n_a
    calls = a.calls
    tts.build(b, a, cache)                       # 돌아오면 다시 합성하지 않는다
    assert a.calls == calls


def test_english_gloss_after_korean_is_not_read_twice():
    def say(t):
        return [x[0] for x in tts._sentences([(c, i) for i, c in enumerate(t)])]
    assert say("프롬프팅(prompting)을 쓴다.") == ["프롬프팅을 쓴다."]
    assert say("대규모 언어 모델(LLM)과 공개 동료심사(Open Peer Review)를 본다.") == ["대규모 언어 모델과 공개 동료심사를 본다."]
    assert say("형성적 연구(N=8)를 했다.") == ["형성적 연구(N=8)를 했다."]          # 숫자·기호가 든 괄호는 내용
    assert say("Hedwig (a CLI agent) is new.") == ["Hedwig (a CLI agent) is new."]  # 영어 본문은 그대로
    s, b, e = tts._sentences([(c, i) for i, c in enumerate("앞. 프롬프팅(prompting)을 쓴다.")])[1]
    assert (b, e) == (3, 23)                                                       # 셀 안 위치는 원문 기준 그대로(괄호 포함)
