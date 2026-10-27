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
