"""백그라운드 작업 멈추기 — 모바일 음성 만들기처럼 진행 콜백을 부르는 작업은 다음 콜백에서, 대기열의 작업은 바로 빠진다."""

import asyncio
import threading
import time

from narani.ui import jobs


def test_cancel_stops_running_job_at_next_progress(tmp_path):
    jobs.reset()
    started, steps = threading.Event(), []

    def work(job):
        for i in range(200):
            job.progress(i, 200)
            steps.append(i)
            started.set()
            time.sleep(0.01)
        return "done"

    async def go():
        job = jobs.start(tmp_path, "mobile_audio", "음성", work)
        while not started.is_set():
            await asyncio.sleep(0.01)
        job.cancel()
        while job.running:
            await asyncio.sleep(0.01)
        return job

    job = asyncio.run(go())
    assert job.status == "cancelled" and job.result is None and len(steps) < 200


def test_cancel_while_queued_leaves_the_gate(tmp_path):
    jobs.reset()
    gate = jobs.Gate(1)
    release = threading.Event()

    async def go():
        a = jobs.start(tmp_path / "a", "mobile_audio", "음성", lambda j: release.wait(5), gate=gate)
        b = jobs.start(tmp_path / "b", "mobile_audio", "음성", lambda j: "b", gate=gate)
        await asyncio.sleep(0.3)
        assert b.detail.get("queued")
        b.cancel()
        while b.running:
            await asyncio.sleep(0.05)
        release.set()
        while a.running:
            await asyncio.sleep(0.05)
        return a, b

    a, b = asyncio.run(go())
    assert b.status == "cancelled" and b.working == 0 and a.status == "done" and gate.active == 0 and not gate.waiting


def test_eta_from_progress():
    j = jobs.Job(root="x", kind="mobile_audio", label="음성")
    assert j.eta() == (0.0, None)                       # 아직 대기 중
    j.working = time.time() - 60
    j.progress(25, 100)
    el, left = j.eta()
    assert 59 <= el <= 61 and 175 <= left <= 185         # 1분에 1/4 → 3분 남음


def test_convert_bar_moves_forward_by_step_and_time():
    # 변환 막대 — 단계마다 1/5, 단계 안에서는 시간으로 차오르되 다음 단계를 넘지 않는다
    from narani.ui.home import _convert_fraction

    xs = [_convert_fraction("extracting", t, 10) for t in (0, 5, 30, 600)]
    assert xs == sorted(xs) and xs[0] == 0 and xs[-1] < 0.2
    assert _convert_fraction("cite", 0, 10) == 0.2 and _convert_fraction("bib", 10_000, 0) < 1.0
