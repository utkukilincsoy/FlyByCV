"""`src/core/update_check.py` testleri (Qt YOK). Ağa HİÇ çıkılmaz: `httpx.MockTransport` kullanılır."""
import json
from datetime import datetime, timedelta

import httpx
import pytest

from src.core import update_check, version
from src.core.settings import (
    KEY_UPDATE_CHECK_ENABLED,
    KEY_UPDATE_LAST_CHECK_AT,
    KEY_UPDATE_SKIPPED_VERSION,
    get_setting,
    set_setting,
)
from src.core.update_check import (
    CHECK_INTERVAL,
    REQUEST_TIMEOUT_SECONDS,
    UpdateInfo,
    check_for_update,
    fetch_latest_version,
    format_version,
    is_check_due,
    is_newer,
    is_valid_repo,
    parse_version,
    release_page_url,
    skip_version,
)

REPO = "ornek-kullanici/flybycv"
NOW = datetime(2026, 10, 1, 12, 0, 0)
CURRENT = "1.0.0"


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    """Gerçek bir HTTP bağlantısı denenirse test düşer (`fetch_latest_version` hataları yuttuğu için
    yalnızca `None` dönmesine güvenilemez; bu yüzden denemeler ayrıca sayılır)."""
    attempts: list[str] = []

    def _refuse(self, request):
        attempts.append(str(request.url))
        raise AssertionError("Test gerçek bir HTTP isteği atmaya çalıştı")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", _refuse)
    yield
    assert attempts == []


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _release_client(body, status: int = 200, requests: list | None = None) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if requests is not None:
            requests.append(request)
        if isinstance(body, (dict, list)):
            return httpx.Response(status, json=body)
        return httpx.Response(status, content=body)

    return _client(handler)


class RecordingFetch:
    """`check_for_update(fetch=...)` için sahte sürüm sorgusu; çağrıları sayar."""

    def __init__(self, result: str | None):
        self.result = result
        self.calls: list[str] = []

    def __call__(self, repo: str) -> str | None:
        self.calls.append(repo)
        return self.result


# -- sürüm ayrıştırma / karşılaştırma -----------------------------------------------------


@pytest.mark.parametrize(
    "tag, expected",
    [("1.2.3", (1, 2, 3)), ("v1.2.3", (1, 2, 3)), (" v10.0.100 ", (10, 0, 100)), ("v0.0.1", (0, 0, 1))],
)
def test_parse_version_accepts_plain_major_minor_patch(tag, expected):
    assert parse_version(tag) == expected


@pytest.mark.parametrize(
    "tag",
    ["", "1.2", "v1.2", "1.2.3.4", "1.2.3-beta", "v1.2.3+build", "V1.2.3", "latest", "1.2.x", "1.2.3\n4", None, 123, ["1.2.3"]],
)
def test_parse_version_rejects_everything_else(tag):
    assert parse_version(tag) is None


def test_format_version_has_no_prefix():
    assert format_version((1, 2, 3)) == "1.2.3"


@pytest.mark.parametrize(
    "candidate, current, expected",
    [
        ("1.0.1", "1.0.0", True),
        ("1.1.0", "1.0.9", True),
        ("2.0.0", "1.99.99", True),
        ("1.10.0", "1.9.0", True),  # metin değil sayı karşılaştırması
        ("1.0.0", "1.0.0", False),
        ("0.9.9", "1.0.0", False),
        ("v1.0.1", "1.0.0", True),
        ("garip", "1.0.0", False),
        ("1.0.1", "garip", False),
    ],
)
def test_is_newer(candidate, current, expected):
    assert is_newer(candidate, current) is expected


# -- depo adı ve bağlantı (bağlantı KODDA kurulur) -----------------------------------------


@pytest.mark.parametrize("repo", ["kullanici/depo", "A-b_c.d/e.f-g_h", REPO])
def test_valid_repo_names(repo):
    assert is_valid_repo(repo) is True


@pytest.mark.parametrize(
    "repo",
    [None, "", "depo", "a/b/c", "/depo", "kullanici/", "a b/c", "a/b?x=1", "a/b#frag", "a/../b", "https://evil.example/x/y", "a/b\n", "../..", "./x", "x/..", "./.."],
)
def test_invalid_repo_names(repo):
    assert is_valid_repo(repo) is False


def test_release_page_url_is_built_in_code_from_the_repo():
    assert release_page_url(REPO) == f"https://github.com/{REPO}/releases/latest"


def test_default_repo_is_a_valid_published_repository():
    assert is_valid_repo(version.GITHUB_REPO) is True
    assert version.GITHUB_REPO.endswith("/flybycv")


# -- fetch_latest_version ----------------------------------------------------------------


def test_fetch_returns_the_latest_release_version_without_prefix():
    requests: list[httpx.Request] = []
    client = _release_client({"tag_name": "v1.2.0", "draft": False, "prerelease": False}, requests=requests)

    assert fetch_latest_version(REPO, client=client) == "1.2.0"

    assert len(requests) == 1
    request = requests[0]
    assert request.method == "GET"
    assert str(request.url) == f"https://api.github.com/repos/{REPO}/releases/latest"


def test_fetch_sends_no_credentials_or_personal_data():
    requests: list[httpx.Request] = []
    fetch_latest_version(REPO, client=_release_client({"tag_name": "v1.2.0"}, requests=requests))

    headers = {name.lower() for name in requests[0].headers}
    assert "authorization" not in headers
    assert "cookie" not in headers
    assert not requests[0].content


def test_fetch_never_calls_the_network_for_an_invalid_repo():
    requests: list[httpx.Request] = []
    client = _release_client({"tag_name": "v9.9.9"}, requests=requests)

    assert fetch_latest_version(None, client=client) is None
    assert fetch_latest_version("bozuk depo adi", client=client) is None
    assert requests == []


@pytest.mark.parametrize(
    "body",
    [
        {"tag_name": "v2.0.0", "prerelease": True},
        {"tag_name": "v2.0.0", "draft": True},
        {"tag_name": "v2.0.0-beta.1"},
        {"tag_name": "release-2"},
        {"tag_name": None},
        {"tag_name": 2},
        {"name": "tag_name yok"},
        [{"tag_name": "v2.0.0"}],
        "[]",
        "bu JSON değil",
        b"\xff\xfe",
        "",
    ],
)
def test_fetch_ignores_unusable_responses(body):
    assert fetch_latest_version(REPO, client=_release_client(body)) is None


@pytest.mark.parametrize("status", [301, 302, 403, 404, 429, 500, 503])
def test_fetch_is_silent_on_non_200_status(status):
    client = _release_client({"tag_name": "v2.0.0"}, status=status)

    assert fetch_latest_version(REPO, client=client) is None


@pytest.mark.parametrize(
    "error",
    [
        httpx.ConnectTimeout("zaman aşımı"),
        httpx.ReadTimeout("zaman aşımı"),
        httpx.ConnectError("bağlantı yok"),
        httpx.RemoteProtocolError("bozuk yanıt"),
        RuntimeError("beklenmeyen"),
        ValueError("beklenmeyen"),
    ],
)
def test_fetch_is_silent_on_any_error(error):
    def handler(request: httpx.Request) -> httpx.Response:
        raise error

    assert fetch_latest_version(REPO, client=_client(handler)) is None


def test_fetch_does_not_follow_redirects_and_uses_a_five_second_timeout():
    client = update_check._build_client()
    try:
        assert REQUEST_TIMEOUT_SECONDS == 5.0
        assert client.timeout == httpx.Timeout(5.0)
        assert client.follow_redirects is False
        assert version.__version__ in client.headers["User-Agent"]
        assert client.headers["User-Agent"].startswith("FlybyCV/")
    finally:
        client.close()


def test_fetch_builds_and_closes_its_own_client_when_none_is_given(monkeypatch):
    built: list[httpx.Client] = []

    def build() -> httpx.Client:
        client = _release_client({"tag_name": "v1.5.0"})
        built.append(client)
        return client

    monkeypatch.setattr(update_check, "_build_client", build)

    assert fetch_latest_version(REPO) == "1.5.0"
    assert built[0].is_closed


def test_fetch_leaves_a_caller_supplied_client_open():
    client = _release_client({"tag_name": "v1.5.0"})

    fetch_latest_version(REPO, client=client)

    assert not client.is_closed


# -- check_for_update ----------------------------------------------------------------------


def test_check_without_a_repo_does_nothing_at_all(session, monkeypatch):
    monkeypatch.setattr(version, "GITHUB_REPO", None)
    fetch = RecordingFetch("9.9.9")

    assert check_for_update(session, now=NOW, fetch=fetch) is None

    assert fetch.calls == []
    assert get_setting(session, KEY_UPDATE_LAST_CHECK_AT) is None  # ayara bile yazılmaz


def test_check_uses_the_configured_repo_by_default(session, monkeypatch):
    monkeypatch.setattr(version, "GITHUB_REPO", REPO)
    fetch = RecordingFetch("1.0.1")

    info = check_for_update(session, now=NOW, current=CURRENT, fetch=fetch)

    assert fetch.calls == [REPO]
    assert info == UpdateInfo(version="1.0.1", url=f"https://github.com/{REPO}/releases/latest")


def test_check_reports_a_newer_version_with_a_code_built_link(session):
    info = check_for_update(session, repo=REPO, now=NOW, current=CURRENT, fetch=RecordingFetch("1.2.0"))

    assert info == UpdateInfo(version="1.2.0", url=release_page_url(REPO))


def test_the_link_never_comes_from_the_response(session):
    """Yanıt `html_url`/`body` ile başka bir adres önerse bile yalnızca `tag_name` kullanılır."""
    body = {
        "tag_name": "v1.2.0",
        "html_url": "https://evil.example/indir.exe",
        "assets": [{"browser_download_url": "https://evil.example/Setup.exe"}],
        "body": "Buradan indir: https://evil.example/indir.exe",
    }
    client = _release_client(body)

    info = check_for_update(
        session,
        repo=REPO,
        now=NOW,
        current=CURRENT,
        fetch=lambda repo: fetch_latest_version(repo, client=client),
    )

    assert info is not None
    assert info.url == release_page_url(REPO)
    assert "evil" not in info.url
    assert info.version == "1.2.0"


@pytest.mark.parametrize("latest", ["1.0.0", "0.9.0", None])
def test_check_returns_none_when_not_newer_or_unknown(session, latest):
    assert check_for_update(session, repo=REPO, now=NOW, current=CURRENT, fetch=RecordingFetch(latest)) is None


def test_check_records_the_attempt_time(session):
    check_for_update(session, repo=REPO, now=NOW, current=CURRENT, fetch=RecordingFetch("1.0.0"))

    assert get_setting(session, KEY_UPDATE_LAST_CHECK_AT) == NOW.isoformat()


def test_a_failed_attempt_also_counts_for_the_daily_limit(session):
    first = RecordingFetch(None)  # çevrimdışı
    check_for_update(session, repo=REPO, now=NOW, current=CURRENT, fetch=first)
    second = RecordingFetch("2.0.0")

    result = check_for_update(session, repo=REPO, now=NOW + timedelta(hours=3), current=CURRENT, fetch=second)

    assert first.calls == [REPO]
    assert second.calls == []
    assert result is None


def test_at_most_one_automatic_check_per_day(session):
    first = RecordingFetch("1.1.0")
    second = RecordingFetch("1.1.0")
    third = RecordingFetch("1.1.0")

    assert check_for_update(session, repo=REPO, now=NOW, current=CURRENT, fetch=first) is not None
    assert check_for_update(session, repo=REPO, now=NOW + CHECK_INTERVAL - timedelta(seconds=1), current=CURRENT, fetch=second) is None
    assert check_for_update(session, repo=REPO, now=NOW + CHECK_INTERVAL, current=CURRENT, fetch=third) is not None

    assert (len(first.calls), len(second.calls), len(third.calls)) == (1, 0, 1)


def test_check_interval_is_one_day():
    assert CHECK_INTERVAL == timedelta(days=1)


def test_disabled_setting_stops_the_automatic_check(session):
    set_setting(session, KEY_UPDATE_CHECK_ENABLED, False)
    fetch = RecordingFetch("2.0.0")

    assert check_for_update(session, repo=REPO, now=NOW, current=CURRENT, fetch=fetch) is None

    assert fetch.calls == []
    assert get_setting(session, KEY_UPDATE_LAST_CHECK_AT) is None


def test_enabled_by_default(session):
    assert is_check_due(session, NOW) is True


def test_force_ignores_disabled_daily_limit_and_skipped_version(session):
    set_setting(session, KEY_UPDATE_CHECK_ENABLED, False)
    set_setting(session, KEY_UPDATE_LAST_CHECK_AT, NOW.isoformat())
    set_setting(session, KEY_UPDATE_SKIPPED_VERSION, "2.0.0")
    fetch = RecordingFetch("2.0.0")

    info = check_for_update(session, force=True, repo=REPO, now=NOW + timedelta(minutes=1), current=CURRENT, fetch=fetch)

    assert fetch.calls == [REPO]
    assert info is not None and info.version == "2.0.0"


def test_force_without_a_repo_still_never_touches_the_network(session, monkeypatch):
    monkeypatch.setattr(version, "GITHUB_REPO", None)
    fetch = RecordingFetch("2.0.0")

    assert check_for_update(session, force=True, now=NOW, fetch=fetch) is None

    assert fetch.calls == []


def test_skipped_version_is_not_reported_again_automatically(session):
    assert skip_version(session, "1.2.0") is True
    fetch = RecordingFetch("1.2.0")

    assert check_for_update(session, repo=REPO, now=NOW, current=CURRENT, fetch=fetch) is None
    assert fetch.calls == [REPO]  # kontrol yapıldı, yalnızca sonuç bastırıldı


def test_a_newer_version_than_the_skipped_one_is_reported(session):
    skip_version(session, "1.2.0")

    info = check_for_update(session, repo=REPO, now=NOW, current=CURRENT, fetch=RecordingFetch("1.3.0"))

    assert info is not None and info.version == "1.3.0"


@pytest.mark.parametrize("version_text, stored", [("v1.2.0", "1.2.0"), ("1.2.0", "1.2.0")])
def test_skip_version_stores_the_plain_version(session, version_text, stored):
    assert skip_version(session, version_text) is True

    assert get_setting(session, KEY_UPDATE_SKIPPED_VERSION) == stored


@pytest.mark.parametrize("bad", ["", "garip", "1.2", "1.2.3-beta"])
def test_skip_version_rejects_invalid_versions(session, bad):
    assert skip_version(session, bad) is False

    assert get_setting(session, KEY_UPDATE_SKIPPED_VERSION) is None


def test_last_check_in_the_future_does_not_block_checks(session):
    set_setting(session, KEY_UPDATE_LAST_CHECK_AT, (NOW + timedelta(days=30)).isoformat())

    assert is_check_due(session, NOW) is True


@pytest.mark.parametrize("garbage", ["dün", "2026-13-45", 12345, ["x"]])
def test_corrupt_last_check_is_treated_as_never_checked(session, garbage):
    set_setting(session, KEY_UPDATE_LAST_CHECK_AT, garbage)

    assert is_check_due(session, NOW) is True


def test_check_for_update_end_to_end_with_a_mock_transport(session):
    """Gerçek `fetch_latest_version` + sahte taşıma: ayar kaydı, sürüm karşılaştırması, bağlantı."""
    body = {"tag_name": "v1.4.2", "draft": False, "prerelease": False}
    requests: list[httpx.Request] = []
    client = _release_client(json.dumps(body).encode(), requests=requests)

    info = check_for_update(
        session,
        repo=REPO,
        now=NOW,
        current=CURRENT,
        fetch=lambda repo: fetch_latest_version(repo, client=client),
    )

    assert info == UpdateInfo(version="1.4.2", url=release_page_url(REPO))
    assert len(requests) == 1
    assert get_setting(session, KEY_UPDATE_LAST_CHECK_AT) == NOW.isoformat()
