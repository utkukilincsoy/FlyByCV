import logging

import pytest
from sqlmodel import select

from src.core.db import get_session
from src.core.models import Setting
from src.core.settings import (
    CV_MODE_ATS,
    DEFAULT_MAIL_TEMPLATE_BODY,
    DEFAULT_MAIL_TEMPLATE_SUBJECT,
    DEFAULTS,
    KEY_APPLICATION_TARGET,
    KEY_AUTOPILOT_CITY,
    KEY_AUTOPILOT_QUERIES,
    KEY_BOUNCE_MIN_SAMPLE,
    KEY_BOUNCE_PAUSE_RATE,
    KEY_CUSTOM_IMAP_HOST,
    KEY_CUSTOM_IMAP_PORT,
    KEY_CUSTOM_SMTP_HOST,
    KEY_CUSTOM_SMTP_PORT,
    KEY_CUSTOM_SMTP_SECURITY,
    KEY_CV_MODE,
    KEY_DAILY_LIMIT,
    KEY_DISCOVERY_STATE,
    KEY_HOURLY_LIMIT,
    KEY_INBOX_CHECK_MINUTES,
    KEY_INBOX_LAST_CHECK_AT,
    KEY_LLM_MODEL_CLAUDE,
    KEY_LLM_MODEL_GEMINI,
    KEY_LLM_MODEL_GROQ,
    KEY_LLM_PROVIDER,
    KEY_MAIL_LANGUAGE,
    KEY_MAIL_PROVIDER,
    KEY_MAIL_TEMPLATE_BODY,
    KEY_MAIL_TEMPLATE_SUBJECT,
    KEY_MAX_DELAY_SECONDS,
    KEY_MAX_PROJECTS,
    KEY_MIN_DELAY_SECONDS,
    KEY_PAUSE_REASON,
    KEY_LEGAL_CONSENT_AT,
    KEY_LEGAL_CONSENT_VERSION,
    KEY_PLACES_MONTHLY_LIMIT,
    KEY_PLACES_REQUESTS_MONTH_KEY,
    KEY_PLACES_REQUESTS_THIS_MONTH,
    KEY_PROFESSION,
    KEY_CUSTOM_PROFESSION_KEYWORDS,
    KEY_CUSTOM_PROFESSION_NAME,
    KEY_ONBOARDING_COMPLETED,
    KEY_ONBOARDING_STEP,
    KEY_SCHEMA_VERSION,
    KEY_SEND_MODE,
    KEY_SENDER_EMAIL,
    KEY_SENDER_NAME,
    KEY_SENDING_PAUSED,
    KEY_TAILORING_ENABLED,
    KEY_TEST_MODE,
    KEY_UPDATE_CHECK_ENABLED,
    KEY_UPDATE_LAST_CHECK_AT,
    KEY_UPDATE_SKIPPED_VERSION,
    KEY_UI_THEME,
    KEY_USE_GOOGLE_PLACES,
    KEY_USE_OSM,
    KEY_WEEKDAYS_ONLY,
    KEY_WORK_END,
    KEY_WORK_START,
    KEY_WORKING_HOURS_ONLY,
    MODE_MANUAL,
    VALUE_FIELD,
    get_setting,
    set_setting,
)



@pytest.mark.parametrize(
    "key,expected",
    [
        (KEY_TAILORING_ENABLED, True),
        (KEY_MAX_PROJECTS, None),
        (KEY_LLM_PROVIDER, "none"),
        (KEY_CV_MODE, CV_MODE_ATS),
        (KEY_MAIL_TEMPLATE_SUBJECT, DEFAULT_MAIL_TEMPLATE_SUBJECT),
        (KEY_MAIL_TEMPLATE_BODY, DEFAULT_MAIL_TEMPLATE_BODY),
        (KEY_LLM_MODEL_CLAUDE, "claude-haiku-4-5"),
        (KEY_LLM_MODEL_GEMINI, "gemini-2.5-flash"),
        (KEY_LLM_MODEL_GROQ, "llama-3.3-70b-versatile"),
        (KEY_TEST_MODE, True),
        (KEY_SENDER_EMAIL, None),
        (KEY_SENDER_NAME, None),
        (KEY_SEND_MODE, MODE_MANUAL),
        (KEY_DAILY_LIMIT, 20),
        (KEY_HOURLY_LIMIT, 20),
        (KEY_MIN_DELAY_SECONDS, 60),
        (KEY_MAX_DELAY_SECONDS, 180),
        (KEY_WORKING_HOURS_ONLY, True),
        (KEY_WORK_START, "09:00"),
        (KEY_WORK_END, "18:00"),
        (KEY_WEEKDAYS_ONLY, True),
        (KEY_SENDING_PAUSED, False),
        (KEY_INBOX_CHECK_MINUTES, 30),
        (KEY_INBOX_LAST_CHECK_AT, None),
        (KEY_AUTOPILOT_CITY, None),
        (KEY_USE_OSM, True),  # taze kurulumda açık; var olan kullanıcı için göç KAPALI yazar
        (KEY_USE_GOOGLE_PLACES, False),  # para harcar: asla varsayılan açık olmaz
        (KEY_PLACES_MONTHLY_LIMIT, 900),
        (KEY_PLACES_REQUESTS_THIS_MONTH, 0),
        (KEY_PLACES_REQUESTS_MONTH_KEY, None),
        (KEY_PAUSE_REASON, None),
        (KEY_BOUNCE_PAUSE_RATE, 0.10),
        (KEY_BOUNCE_MIN_SAMPLE, 5),
        (KEY_ONBOARDING_COMPLETED, False),  # taze kurulumda sihirbaz açılır
        (KEY_ONBOARDING_STEP, None),
        # KEY_SCHEMA_VERSION burada YOK: `session` fixture'ı `init_db()` çağırır, o da göçü
        # (`settings_migration.run_settings_migrations`) çalıştırıp bu satırı HER ZAMAN yazar
        # -- "kayıt yoksa" öncülü bu anahtar için hiçbir zaman doğru olmaz (bkz.
        # `tests/test_settings_migration.py`). Varsayılan değeri (0) ayrı test edilir aşağıda.
    ],
)
def test_get_setting_returns_default_when_unset(session, key, expected):
    """Kayıt yoksa varsayılan değer dönmeli."""
    assert get_setting(session, key) == expected


def test_schema_version_default_value_is_zero():
    """Satır hiç yoksa (kuramsal olarak) 0 dönmeli -- göç bunu "henüz çalışmadı" sayar."""
    assert DEFAULTS[KEY_SCHEMA_VERSION] == 0


def test_autopilot_query_default_is_empty_list(session):
    """Faz 10 Adım 10.1d: varsayılan artık kişiye/mesleğe özel DEĞİL, boş liste.

    Aramalar meslek paketinden gelecek (Adım 10.5); var olan kullanıcının önceki listesi
    `core/settings_migration.py` ile açıkça korunur, bu varsayılan onu ETKİLEMEZ.
    """
    queries = get_setting(session, KEY_AUTOPILOT_QUERIES)
    assert queries == []


def test_discovery_state_default_is_empty_dict(session):
    """Hiç keşif yapılmamışsa boş sözlük dönmeli (hiçbir kaynak 'done' değil)."""
    assert get_setting(session, KEY_DISCOVERY_STATE) == {}


def test_get_setting_default_list_is_not_shared_reference(session):
    """Dönen varsayılan liste yerinde değiştirilirse global DEFAULTS bozulmamalı."""
    queries = get_setting(session, KEY_AUTOPILOT_QUERIES)
    queries.append("kirlendi")
    assert "kirlendi" not in get_setting(session, KEY_AUTOPILOT_QUERIES)
    assert "kirlendi" not in DEFAULTS[KEY_AUTOPILOT_QUERIES]


def test_get_setting_default_dict_is_not_shared_reference(session):
    """Aynısı boş sözlük varsayılanı (discovery_state) için de geçerli olmalı."""
    state = get_setting(session, KEY_DISCOVERY_STATE)
    state["teknokent"] = {"done": True}
    assert get_setting(session, KEY_DISCOVERY_STATE) == {}
    assert DEFAULTS[KEY_DISCOVERY_STATE] == {}


def test_get_setting_stored_dict_is_a_copy(session):
    """Kayıtlı bir değer de yerinde değiştirilince veritabanındaki değer değişmemeli."""
    set_setting(session, KEY_DISCOVERY_STATE, {"teknokent": {"done": True}})
    state = get_setting(session, KEY_DISCOVERY_STATE)
    state["teknokent"]["done"] = False
    assert get_setting(session, KEY_DISCOVERY_STATE) == {"teknokent": {"done": True}}


def test_defaults_cover_all_keys():
    """Varsayılan sözlüğü bilinen tüm ayarları tanımlamalı."""
    assert set(DEFAULTS) == {
        KEY_TAILORING_ENABLED,
        KEY_MAX_PROJECTS,
        KEY_LLM_PROVIDER,
        KEY_CV_MODE,
        KEY_MAIL_TEMPLATE_SUBJECT,
        KEY_MAIL_TEMPLATE_BODY,
        KEY_APPLICATION_TARGET,
        KEY_PROFESSION,
        KEY_CUSTOM_PROFESSION_NAME,
        KEY_CUSTOM_PROFESSION_KEYWORDS,
        KEY_MAIL_LANGUAGE,
        KEY_LLM_MODEL_CLAUDE,
        KEY_LLM_MODEL_GEMINI,
        KEY_LLM_MODEL_GROQ,
        KEY_TEST_MODE,
        KEY_SENDER_EMAIL,
        KEY_SENDER_NAME,
        KEY_MAIL_PROVIDER,
        KEY_CUSTOM_SMTP_HOST,
        KEY_CUSTOM_SMTP_PORT,
        KEY_CUSTOM_SMTP_SECURITY,
        KEY_CUSTOM_IMAP_HOST,
        KEY_CUSTOM_IMAP_PORT,
        KEY_SEND_MODE,
        KEY_DAILY_LIMIT,
        KEY_HOURLY_LIMIT,
        KEY_MIN_DELAY_SECONDS,
        KEY_MAX_DELAY_SECONDS,
        KEY_WORKING_HOURS_ONLY,
        KEY_WORK_START,
        KEY_WORK_END,
        KEY_WEEKDAYS_ONLY,
        KEY_SENDING_PAUSED,
        KEY_INBOX_CHECK_MINUTES,
        KEY_INBOX_LAST_CHECK_AT,
        KEY_AUTOPILOT_CITY,
        KEY_USE_OSM,
        KEY_USE_GOOGLE_PLACES,
        KEY_AUTOPILOT_QUERIES,
        KEY_PLACES_MONTHLY_LIMIT,
        KEY_PLACES_REQUESTS_THIS_MONTH,
        KEY_PLACES_REQUESTS_MONTH_KEY,
        KEY_DISCOVERY_STATE,
        KEY_PAUSE_REASON,
        KEY_BOUNCE_PAUSE_RATE,
        KEY_BOUNCE_MIN_SAMPLE,
        KEY_LEGAL_CONSENT_VERSION,
        KEY_LEGAL_CONSENT_AT,
        KEY_ONBOARDING_COMPLETED,
        KEY_ONBOARDING_STEP,
        KEY_UPDATE_CHECK_ENABLED,
        KEY_UPDATE_LAST_CHECK_AT,
        KEY_UPDATE_SKIPPED_VERSION,
        KEY_UI_THEME,
        KEY_SCHEMA_VERSION,
    }


def test_update_check_defaults():
    """Güncelleme kontrolü varsayılan AÇIK (KVKK metni bildirir); son kontrol/atlanan sürüm boş."""
    assert DEFAULTS[KEY_UPDATE_CHECK_ENABLED] is True
    assert DEFAULTS[KEY_UPDATE_LAST_CHECK_AT] is None
    assert DEFAULTS[KEY_UPDATE_SKIPPED_VERSION] is None


def test_set_setting_wraps_scalar_in_dict(session):
    """Setting.value bir dict; sayı 'value' anahtarı altına sarılmalı."""
    set_setting(session, KEY_MAX_PROJECTS, 3)
    assert session.get(Setting, KEY_MAX_PROJECTS).value == {VALUE_FIELD: 3}


def test_set_setting_visible_in_same_session(session):
    """flush edildiği için commit olmadan aynı oturumda okunabilmeli."""
    set_setting(session, KEY_MAIL_TEMPLATE_SUBJECT, "İş")
    assert get_setting(session, KEY_MAIL_TEMPLATE_SUBJECT) == "İş"


def test_set_setting_updates_without_duplicate_row(session):
    """İkinci yazım yeni satır açmamalı."""
    set_setting(session, KEY_MAX_PROJECTS, 3)
    set_setting(session, KEY_MAX_PROJECTS, 5)

    rows = session.exec(select(Setting).where(Setting.key == KEY_MAX_PROJECTS)).all()
    assert len(rows) == 1
    assert get_setting(session, KEY_MAX_PROJECTS) == 5


def test_set_setting_none_is_read_back_as_none(session):
    """Açıkça None yazılınca None dönmeli."""
    set_setting(session, KEY_MAX_PROJECTS, 3)
    set_setting(session, KEY_MAX_PROJECTS, None)
    assert get_setting(session, KEY_MAX_PROJECTS) is None


def test_set_setting_false_is_read_back_as_false(session):
    """False varsayılana (True) düşmemeli."""
    set_setting(session, KEY_TAILORING_ENABLED, False)
    assert get_setting(session, KEY_TAILORING_ENABLED) is False


def test_setting_survives_commit_and_new_session(engine, session):
    """Commit sonrası ayar yeni bir oturumda da okunabilmeli."""
    set_setting(session, KEY_MAIL_TEMPLATE_SUBJECT, "Yarı Zamanlı")
    session.commit()

    with get_session(engine=engine) as other:
        assert get_setting(other, KEY_MAIL_TEMPLATE_SUBJECT) == "Yarı Zamanlı"


def test_set_setting_does_not_commit(engine, session):
    """Commit çağırana ait: rollback sonrası kayıt kalmamalı."""
    set_setting(session, KEY_MAIL_TEMPLATE_SUBJECT, "İş")
    session.rollback()

    with get_session(engine=engine) as other:
        assert other.get(Setting, KEY_MAIL_TEMPLATE_SUBJECT) is None


@pytest.mark.parametrize("action", ["get", "set"])
def test_unknown_key_raises(session, action):
    """Bilinmeyen anahtar sessizce varsayılana düşmemeli."""
    with pytest.raises(KeyError, match="Bilinmeyen ayar"):
        if action == "get":
            get_setting(session, "olmayan_ayar")
        else:
            set_setting(session, "olmayan_ayar", 1)


def test_set_setting_debug_log_has_key_but_not_value(session, caplog):
    with caplog.at_level(logging.DEBUG, logger="src.core.settings"):
        set_setting(session, KEY_SENDER_EMAIL, "gizli.kisi@ornek.com")
    assert KEY_SENDER_EMAIL in caplog.text
    assert "gizli.kisi@ornek.com" not in caplog.text
