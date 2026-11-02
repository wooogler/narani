"""듣기 — 웹 리더와 데스크톱 뷰어가 같은 읽을 목록·말하기 엔진을 쓴다."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("nicegui", reason="웹 UI 의존성(ui extra) 미설치")

from narani.phone import web  # noqa: E402
from narani.ui import listen  # noqa: E402
from tests.test_phone import _wd  # noqa: E402


def test_engine_is_shared_by_web_reader_and_desktop():
    js = web.site_files(local=True)["app.js"][0].decode()
    assert listen.ENGINE_JS in js and listen.ENGINE_JS in listen.JS
    assert "new SpeechAudio(webVoice)" in js and "new SpeechAudio(pickVoice)" in listen.JS
    for name in ("function SpeechAudio(pickVoice)", "function webTl(plan)", "function cellRange(cell, s, e)"):
        assert listen.ENGINE_JS.count(name) == 1 and js.count(name) == 1   # 한 곳에만


def test_plan_reads_chosen_side_and_is_cached(tmp_path):
    wd = _wd(tmp_path, "bots")
    ko = listen.info(wd.root)
    assert ko["side"] == "ko" and ko["has_ko"] and ko["audio"] is None
    assert ko["plan"]["sections"][0]["title"] == "맞춤형 봇"
    en = listen.info(wd.root, "en")
    assert en["side"] == "en" and en["plan"]["lang"] == "en" and en["plan"]["sections"][0]["title"] != "맞춤형 봇"
    assert [u[3] for u in en["plan"]["units"]] == ["Bespoke Bots"] and ko["plan"]["units"][0][3] == "맞춤형 봇"
    cache = listen.audio_dir(wd.root) / "speech-en.json"
    assert json.loads(cache.read_text())["plan"] == en["plan"]
    assert listen.info(wd.root, "en")["plan"] == en["plan"]               # 저장해 둔 것
    only_en = _wd(tmp_path, "solo", ko=None)
    assert listen.info(only_en.root, "ko")["side"] == "en"                 # 번역이 없으면 원문


def test_local_audio_copy_matches_side_and_staleness(tmp_path):
    wd = _wd(tmp_path, "bots")
    tl = {"side": "ko", "src": "old", "voice": "mac:Yuna", "duration": 3, "sections": [], "sents": []}
    listen.keep_local(wd.root, b"m4a", tl)
    got = listen.info(wd.root)
    assert got["audio"]["tl"]["voice"] == "mac:Yuna" and got["audio"]["stale"] is True
    assert listen.info(wd.root, "en")["audio"] is None                    # 다른 쪽을 읽은 음성은 쓰지 않는다
    listen.drop_local(wd.root)
    assert listen.info(wd.root)["audio"] is None


def test_viewer_has_listen_bar_and_shortcuts():
    assert 'id="md-listen"' in listen.HTML and 'id="mdl-au"' in listen.HTML
    assert "e.code === 'Space'" in listen.JS and "BracketRight" in listen.JS
    assert listen.init_js("abc") == 'window.__mdListen&&window.__mdListen.setPaper("abc");'


def test_rebuild_local_copy_from_section_cache(tmp_path):
    """사본을 남기기 전에 만든 음성 — 섹션 캐시로 다시 잇는다(목소리를 다시 돌리지 않고). 빠진 섹션이 있으면 못 잇는다."""
    from narani.phone import bundle, tts
    from tests.test_phone_tts import FakeVoice

    if not tts.codec_available():
        pytest.skip("m4a 인코더(afconvert·PyAV) 필요")
    wd = _wd(tmp_path, "bots")
    _, made = tts.build(bundle.build_paper(wd.root), FakeVoice(), listen.audio_dir(wd.root))
    rec = {"voice": "fake:test", "include": None, "captions": False, "math": False}
    assert listen.local_audio(wd.root) is None
    got = listen.info(wd.root, made["side"])
    assert got["audio"] is None                                     # 웹 리더 기록이 없으면 그대로
    assert listen.rebuild_local(wd.root, rec)
    m4a, tl = listen.local_audio(wd.root)
    assert m4a.read_bytes()[4:8] == b"ftyp" and tl["sents"] == made["sents"] and tl["voice"] == "fake:test"
    listen.drop_local(wd.root)
    next(p for p in listen.audio_dir(wd.root).glob("*.m4a")).unlink()  # 섹션 하나가 빠지면
    assert not listen.rebuild_local(wd.root, rec) and listen.local_audio(wd.root) is None
    assert not listen.rebuild_local(wd.root, None)
