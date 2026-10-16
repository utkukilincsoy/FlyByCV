"""src/ui/workers.py testleri (qtbot; veritabanı YOK -- hızlı ve kararlı kalsın)."""
import time

from src.ui.jobs import Progress
from src.ui.workers import JobWorker


def test_job_worker_emits_progress_and_finishes_ok(qtbot):
    def job(*, on_progress, token):
        on_progress(Progress(1, 1, "bitti"))
        return "sonuç"

    worker = JobWorker(job)
    progress_events = []
    worker.progress.connect(
        lambda current, total, message: progress_events.append((current, total, message))
    )

    with qtbot.waitSignal(worker.finished_ok, timeout=2000) as blocker:
        worker.start()

    assert blocker.args == ["sonuç"]
    assert progress_events == [(1, 1, "bitti")]


def test_job_worker_emits_failed_on_exception(qtbot):
    def job(*, on_progress, token):
        raise RuntimeError("beklenmeyen hata")

    worker = JobWorker(job)

    with qtbot.waitSignal(worker.failed, timeout=2000) as blocker:
        worker.start()

    assert "beklenmeyen hata" in blocker.args[0]


def test_job_worker_cancel_is_observed_by_running_job(qtbot):
    def job(*, on_progress, token):
        count = 0
        while not token.is_cancelled:
            count += 1
            time.sleep(0.01)
            if count > 500:  # güvenlik supabı: testi sonsuza kadar döndürmesin
                break
        return "iptal edildi" if token.is_cancelled else "tamamlandı"

    worker = JobWorker(job)

    with qtbot.waitSignal(worker.finished_ok, timeout=3000) as blocker:
        worker.start()
        worker.cancel()

    assert blocker.args == ["iptal edildi"]
