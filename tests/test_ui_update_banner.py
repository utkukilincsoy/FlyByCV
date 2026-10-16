"""src/ui/update_banner.py testleri (qtbot; gerçek ağ ve gerçek tarayıcı yok)."""
from src.core.db import get_session
from src.core.settings import KEY_UPDATE_SKIPPED_VERSION, get_setting
from src.core.update_check import UpdateInfo
from src.ui import update_banner
from src.ui.jobs import UpdateCheckJobResult
from src.ui.update_banner import UpdateBanner, format_banner_text

INFO = UpdateInfo(version="1.2.0", url="https://github.com/kullanici/depo/releases/latest")


class _FakeDesktopServices:
    opened: list = []

    @staticmethod
    def openUrl(url) -> bool:
        _FakeDesktopServices.opened.append(url.toString())
        return True


def _wait_for_worker(qtbot, banner: UpdateBanner) -> None:
    qtbot.waitUntil(lambda: banner._worker is None, timeout=5000)


def test_banner_is_hidden_until_an_update_is_shown(qtbot, engine):
    banner = UpdateBanner(engine)
    qtbot.addWidget(banner)
    assert banner.isHidden()
    assert banner.info is None

    banner.show_update(INFO)

    assert not banner.isHidden()
    assert banner._label.text() == format_banner_text(INFO)
    assert "1.2.0" in banner._label.text()


def test_start_check_shows_the_banner_when_a_new_version_exists(qtbot, engine, monkeypatch):
    seen = []
    monkeypatch.setattr(
        update_banner,
        "update_check_job",
        lambda engine, *, force, on_progress, token: seen.append(force) or UpdateCheckJobResult(info=INFO),
    )
    banner = UpdateBanner(engine)
    qtbot.addWidget(banner)

    banner.start_check()
    _wait_for_worker(qtbot, banner)

    assert seen == [False]  # açılıştaki kontrol sessiz/otomatik: force DEĞİL
    assert not banner.isHidden() and banner.info == INFO


def test_start_check_stays_hidden_when_nothing_new_or_not_configured(qtbot, engine, monkeypatch):
    banner = UpdateBanner(engine)
    qtbot.addWidget(banner)
    for result in (UpdateCheckJobResult(info=None), UpdateCheckJobResult(configured=False)):
        monkeypatch.setattr(
            update_banner,
            "update_check_job",
            lambda engine, *, force, on_progress, token, r=result: r,
        )
        banner.start_check()
        _wait_for_worker(qtbot, banner)
        assert banner.isHidden()


def test_a_failing_check_is_silent(qtbot, engine, monkeypatch):
    def boom(engine, *, force, on_progress, token):
        raise RuntimeError("ağ yok")

    monkeypatch.setattr(update_banner, "update_check_job", boom)
    banner = UpdateBanner(engine)
    qtbot.addWidget(banner)

    banner.start_check()
    _wait_for_worker(qtbot, banner)

    assert banner.isHidden()  # hata kullanıcıya gösterilmez, şerit açılmaz


def test_open_button_opens_the_code_built_release_url(qtbot, engine, monkeypatch):
    _FakeDesktopServices.opened = []
    monkeypatch.setattr(update_banner, "QDesktopServices", _FakeDesktopServices)
    banner = UpdateBanner(engine)
    qtbot.addWidget(banner)
    banner.show_update(INFO)

    banner._open_button.click()

    assert _FakeDesktopServices.opened == [INFO.url]
    assert not banner.isHidden()  # açmak şeridi kapatmaz


def test_skip_button_persists_the_skipped_version_and_hides(qtbot, engine, session):
    banner = UpdateBanner(engine)
    qtbot.addWidget(banner)
    banner.show_update(INFO)

    banner._skip_button.click()

    assert banner.isHidden()
    session.expire_all()
    assert get_setting(session, KEY_UPDATE_SKIPPED_VERSION) == "1.2.0"


def test_hide_button_hides_without_skipping(qtbot, engine, session):
    banner = UpdateBanner(engine)
    qtbot.addWidget(banner)
    banner.show_update(INFO)

    banner._hide_button.click()

    assert banner.isHidden()
    session.expire_all()
    assert get_setting(session, KEY_UPDATE_SKIPPED_VERSION) is None
