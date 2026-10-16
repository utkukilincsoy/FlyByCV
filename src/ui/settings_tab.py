"""Ayarlar sekmesi: sekiz `QGroupBox` (gönderim güvenliği, CV, firma kaynakları, Google Places,
mail şablonu, yapay zekâ, mail hesabı, güncellemeler) + Kaydet / Vazgeç / Varsayılanlara dön.

Gruplar `src/ui/settings_groups/` altında yeniden kullanılabilir parçalar (PLAN.md 10.2c); bu dosya
yalnızca onları alt alta dizer, `SettingsForm` ile konuşturur (`form()` / `_set_form()`), kirlilik
takibini, kaydet/onay sırasını ve ağa çıkan testlerin arka plan işçisini yönetir. Doğrulama, onay
sırası ve okuma/yazma `src/ui/settings_data.py`'de saf Python olarak yaşıyor (`src/ui/company_data.py`
/ `src/ui/queue_data.py` kalıbı). Hiçbir widget API anahtarı/mail şifresi TUTMAZ (CLAUDE.md
kural 6): onlar `src/core/secrets.py` (keyring) üzerinden gider; ekranda yalnızca "kayıtlı ✓ /
kayıtlı değil" görünür.

"Bağlantıyı test et" butonları ağa çıkar (CLAUDE.md kural 8): gruplar yalnızca `test_requested`
yayar; bu sekme `src/ui/jobs.py::llm_test_job` / `smtp_login_test_job` / `places_key_test_job`'u
`src/ui/workers.py::run_with_progress` ile arka planda çalıştırır, arayüz dondurulmaz. Hepsi
EKRANDAKİ (henüz Kaydet'e basılmamış) seçimi dener, veritabanındaki son kaydedilen değeri değil --
"test et"in amacı budur.

Gruplardaki widget'lara eski `_xxx` adlarıyla da erişilir (`_expose_*_widgets`): testler ve dış
kod bu adları bilir.
"""
import logging
from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import Any, Callable, Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.engine import Engine

from src.company_finder.google_places import requests_this_month
from src.company_finder.source_selector import GOOGLE_PLACES_API_KEY_NAME
from src.core.cities import CityPreset, load_cities
from src.core.db import get_session, sqlite_file_path
from src.core.mail_providers import MailProviderPreset, load_mail_provider_presets
from src.core.mail_template_presets import load_generic_mail_templates
from src.core.professions import ProfessionPreset, load_profession_presets
from src.core.secrets import get_api_key
from src.ui.jobs import (
    UpdateCheckJobResult,
    llm_test_job,
    places_key_test_job,
    smtp_login_test_job,
    update_check_job,
)
from src.ui.icons import set_icon
from src.ui.settings_data import (
    Confirmation,
    SettingsForm,
    apply_form,
    default_form,
    plan_save,
    read_form,
    sender_name_hint,
)
from src.ui.settings_groups.cv_group import CvGroup
from src.ui.settings_groups.llm_group import LlmGroup, show_llm_test_result
from src.ui.settings_groups.mail_account_group import MailAccountGroup, show_smtp_test_result
from src.ui.settings_groups.mail_template_group import MailTemplateGroup
from src.ui.settings_groups.places_group import PlacesGroup, show_places_test_result
from src.ui.settings_groups.sending_group import SendingGroup
from src.ui.settings_groups.sources_group import SourcesContext, SourcesGroup
from src.ui.settings_groups.appearance_group import AppearanceGroup
from src.ui.settings_groups.backup_group import BackupGroup
from src.ui.settings_groups.updates_group import UpdatesGroup, show_update_check_result
from src.ui.onboarding_wizard import run_onboarding_wizard
from src.ui.test_mode_dialog import switch_test_mode
from src.ui.wheel_guard import install_wheel_guard
from src.ui.workers import JobWorker, run_with_progress

logger = logging.getLogger(__name__)

UNSAVED_CHANGES_TITLE = "Kaydedilmemiş değişiklik var"
UNSAVED_CHANGES_TEXT = "Ayarlarda kaydedilmemiş değişiklikler var. Ne yapmak istersiniz?"
UNSAVED_CHANGES_SAVE = "Kaydet"
UNSAVED_CHANGES_DISCARD = "Değişiklikleri at"
UNSAVED_CHANGES_STAY = "Ayarlarda kal"

REOPEN_WIZARD_TEXT = "Kurulum sihirbazını yeniden aç"

RESTORE_DEFAULTS_TEXT = (
    "Tüm alanlar varsayılan değerlere döndürülecek (henüz KAYDEDİLMEZ, Kaydet'e basmanız "
    "gerekir). Emin misiniz?"
)


class SettingsTab(QWidget):
    """Ayarlar sekmesi.

    Kirlilik takibi SİNYALLE değil KARŞILAŞTIRMAYLA yapılır: `_loaded_form` (veritabanından son
    okunan hâl) ile `form()` (widget'ların o anki hâli) karşılaştırılır -- frozen `SettingsForm`
    eşitliği bunu bedelsiz sağlıyor, her widget için ayrı `changed` sinyali dinlemeye gerek yok.
    """

    settings_saved = Signal()
    #: Sihirbaz (yeniden açılmış) sonuna kadar tamamlandı: ana pencere Otopilot sekmesine geçsin.
    autopilot_requested = Signal()
    #: "Şimdi kontrol et" yeni bir sürüm buldu (`UpdateInfo`): ana pencere şeridi göstersin.
    update_available = Signal(object)
    #: "Yedekten geri yükle" onaylandı (`Path`): geri yüklemeyi ve yeniden başlatmayı ana pencere yapar.
    restore_requested = Signal(object)

    def __init__(
        self, engine: Engine, profile_path: Path, parent: Optional[QWidget] = None
    ) -> None:
        super().__init__(parent)
        self._engine = engine
        self._profile_path = profile_path
        self._key_status_labels: dict[str, QLabel] = {}
        #: Ağa çıkan iki test ("Bağlantıyı test et") aynı anda tek bir işçiyi paylaşır --
        #: `companies_tab.py::_guard_running` ile aynı kalıp; iki işi aynı anda başlatmaya
        #: gerek yok, biri sürerken diğeri "İş sürüyor" uyarısıyla reddedilir.
        self._worker: Optional[JobWorker] = None
        #: Disk I/O (ağ DEĞİL) — bir kez okunur, sekme açıkken değişmez (dosya elle
        #: değiştirilirse yeniden başlatmak gerekir, `core/professions.py` ile aynı ilke).
        self._mail_presets: dict[str, MailProviderPreset] = load_mail_provider_presets()
        #: Meslek paketleri (`presets/professions/*.json`) + genel hedef/dil şablonları
        #: (`presets/mail_templates/*.json`) — "Hedef"/"Meslek"/"Mail dili" combo'ları
        #: değiştiğinde önerilecek şablonu hesaplamak için (`settings_data.
        #: suggested_mail_template`). Aynı ilke: bir kez okunur, sekme açıkken değişmez.
        self._profession_presets: dict[str, ProfessionPreset] = load_profession_presets()
        self._generic_mail_templates = load_generic_mail_templates()
        #: 81 il (`presets/cities.json`) — aynı ilke: bir kez okunur.
        self._cities: dict[str, CityPreset] = load_cities()
        #: Kaynak özeti için `reload()`da ve anahtar kaydet/sil sonrası tazelenir; forma bağlı
        #: DEĞİL (sır değil ama keyring'den gelir, SettingsForm'a girmez).
        self._has_places_key = False

        self._sending_group = SendingGroup(self)
        self._expose_sending_widgets()
        group1 = self._sending_group
        self._cv_group = CvGroup(self)
        self._expose_cv_widgets()
        group2 = self._cv_group
        self._sources_group = SourcesGroup(
            self._cities, self._profession_presets, self._sources_context, self
        )
        self._city_combo = self._sources_group.city_combo
        self._use_osm_check = self._sources_group.use_osm_check
        self._sources_hint_label = self._sources_group.sources_hint_label
        group_sources = self._sources_group
        self._places_group = PlacesGroup(self._places_requests_used, self)
        self._expose_places_widgets()
        group_places = self._places_group
        self._template_group = MailTemplateGroup(
            self._profession_presets, self._generic_mail_templates, self._profile_path, self
        )
        self._expose_template_widgets()
        group3 = self._template_group
        self._llm_group = LlmGroup(self)
        self._expose_llm_widgets()
        group4 = self._llm_group
        self._mail_group = MailAccountGroup(self._mail_presets, self)
        self._expose_mail_widgets()
        group5 = self._mail_group
        self._updates_group = UpdatesGroup(self)
        self._check_updates_check = self._updates_group.check_enabled_check
        self._updates_group.check_requested.connect(self._on_check_updates_now)
        group_updates = self._updates_group
        # Bellek içi (test) motorunda `sqlite_file_path` None döner: düğmeler kapalı kalır, hiçbir
        # test gerçek `data/backups/`'a ulaşamaz.
        self._backup_group = BackupGroup(sqlite_file_path(engine), self._profile_path, self)
        self._backup_group.restore_requested.connect(self.restore_requested.emit)
        self._appearance_group = AppearanceGroup(engine, self)

        content = QWidget(self)
        content_layout = QVBoxLayout(content)
        for group in (
            group1, group2, group_sources, group_places, group3, group4, group5, group_updates,
            self._backup_group, self._appearance_group,
        ):
            content_layout.addWidget(group)
        content_layout.addStretch(1)
        install_wheel_guard(content)  # kaydırırken değer değişmesin (yalnız odaktayken tekerlek)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)

        self._save_button = QPushButton("Kaydet", self)
        set_icon(self._save_button, "save")
        self._cancel_button = QPushButton("Vazgeç", self)
        self._defaults_button = QPushButton("Varsayılanlara dön", self)
        self._wizard_button = QPushButton(REOPEN_WIZARD_TEXT, self)
        self._save_button.clicked.connect(self._on_save)
        self._cancel_button.clicked.connect(self._on_cancel)
        self._defaults_button.clicked.connect(self._on_restore_defaults)
        self._wizard_button.clicked.connect(self._on_reopen_wizard)

        button_row = QHBoxLayout()
        button_row.addWidget(self._save_button)
        button_row.addWidget(self._cancel_button)
        button_row.addWidget(self._defaults_button)
        button_row.addStretch(1)
        button_row.addWidget(self._wizard_button)

        outer_layout = QVBoxLayout(self)
        outer_layout.addWidget(scroll)
        outer_layout.addLayout(button_row)

        self._loaded_form = default_form()
        self.reload()

    # -- grup kurulumu ------------------------------------------------------------------

    def _expose_sending_widgets(self) -> None:
        """Gruptaki widget'lara eski `_xxx` adlarıyla da erişilir (testler ve dış kod bu adları bilir)."""
        group = self._sending_group
        self._test_mode_label = group.test_mode_label
        self._test_mode_button = group.test_mode_button
        group.test_mode_change_requested.connect(self._on_test_mode_change_requested)
        self._send_mode_combo = group.send_mode_combo
        self._send_mode_desc_label = group.send_mode_desc_label
        self._daily_limit_spin = group.daily_limit_spin
        self._hourly_limit_spin = group.hourly_limit_spin
        self._min_delay_spin = group.min_delay_spin
        self._max_delay_spin = group.max_delay_spin
        self._working_hours_check = group.working_hours_check
        self._work_start_edit = group.work_start_edit
        self._work_end_edit = group.work_end_edit
        self._weekdays_check = group.weekdays_check

    def _expose_cv_widgets(self) -> None:
        group = self._cv_group
        self._cv_mode_combo = group.cv_mode_combo
        self._cv_mode_desc_label = group.cv_mode_desc_label
        self._own_cv_status_label = group.own_cv_status_label
        self._own_cv_row_widget = group.own_cv_row_widget
        self._tailoring_check = group.tailoring_check
        self._max_projects_spin = group.max_projects_spin

    def _on_select_own_cv(self) -> None:
        self._cv_group.on_select_own_cv()

    def _on_remove_own_cv(self) -> None:
        self._cv_group.on_remove_own_cv()

    def _expose_places_widgets(self) -> None:
        group = self._places_group
        self._use_places_check = group.use_places_check
        self._places_limit_spin = group.places_limit_spin
        self._places_usage_label = group.places_usage_label
        self._key_status_labels[GOOGLE_PLACES_API_KEY_NAME] = group.key_status_label
        group.use_places_check.stateChanged.connect(self._sources_group.update_hint)
        group.key_status_changed.connect(self._on_places_key_status_changed)
        group.test_requested.connect(self._on_test_places_key)

    def _on_places_key_status_changed(self, saved: bool) -> None:
        self._has_places_key = saved
        self._sources_group.update_hint()

    def _places_requests_used(self) -> int:
        with get_session(self._engine) as session:
            return requests_this_month(session)

    def _sources_context(self) -> SourcesContext:
        """Kaynak özetinin (`SourcesGroup`) kendi alanları dışında okuduğu değerler."""
        return SourcesContext(
            profession_id=self._profession_combo.currentData(),
            use_google_places=self._use_places_check.isChecked(),
            has_places_key=self._has_places_key,
            custom_profession=self._template_group.custom_preset(),
        )

    def _expose_template_widgets(self) -> None:
        group = self._template_group
        self._target_combo = group.target_combo
        self._profession_combo = group.profession_combo
        self._mail_language_combo = group.mail_language_combo
        self._mail_subject_edit = group.mail_subject_edit
        self._mail_body_edit = group.mail_body_edit
        self._mail_preview_button = group.preview_button
        self._custom_profession_name_edit = group.custom_fields.name_edit
        self._custom_profession_keywords_edit = group.custom_fields.keywords_edit
        # Meslek değişince kaynak özeti tazelenir (grubun kendi şablon önerisi sinyalinden SONRA
        # bağlanır, eski sırayla aynı).
        group.profession_combo.currentIndexChanged.connect(self._sources_group.update_hint)
        group.custom_fields.changed.connect(self._sources_group.update_hint)

    def _expose_llm_widgets(self) -> None:
        group = self._llm_group
        self._llm_provider_combo = group.llm_provider_combo
        self._llm_model_claude_edit = group.llm_model_claude_edit
        self._llm_model_gemini_edit = group.llm_model_gemini_edit
        self._llm_model_groq_edit = group.llm_model_groq_edit
        self._key_status_labels.update(group.key_status_labels)
        group.test_requested.connect(self._on_test_llm_connection)

    def _expose_mail_widgets(self) -> None:
        group = self._mail_group
        self._sender_email_edit = group.sender_email_edit
        self._sender_name_edit = group.sender_name_edit
        self._mail_provider_combo = group.mail_provider_combo
        self._gmail_status_label = group.password_status_label
        self._inbox_check_minutes_spin = group.inbox_check_minutes_spin
        group.test_requested.connect(self._on_test_smtp_connection)

    # -- form <-> widget dönüşümü --------------------------------------------------------

    def form(self) -> SettingsForm:
        return SettingsForm(
            **self._sending_group.values(),
            **self._cv_group.values(),
            **self._template_group.values(),
            **self._llm_group.values(),
            **self._mail_group.values(),
            **self._sources_group.values(),
            **self._places_group.values(),
            **self._updates_group.values(),
        )

    def _set_form(self, form: SettingsForm) -> None:
        self._sending_group.set_values(form)
        self._cv_group.set_values(form)
        self._template_group.set_values(form)
        self._llm_group.set_values(form)
        self._mail_group.set_values(form)
        self._sources_group.set_values(form)
        self._places_group.set_values(form)
        self._updates_group.set_values(form)
        self._sources_group.update_hint()

    # -- yükleme / kirlilik ---------------------------------------------------------------

    def reload(self) -> None:
        """Veritabanından okur, widget'ları doldurur ve keyring durumlarını tazeler."""
        with get_session(self._engine) as session:
            form = read_form(session)
        self._has_places_key = bool(get_api_key(GOOGLE_PLACES_API_KEY_NAME))
        self._loaded_form = form
        self._set_form(form)
        self._sender_name_edit.setPlaceholderText(sender_name_hint(self._profile_path))
        self._refresh_all_key_statuses()
        self._places_group.refresh_usage()
        self._backup_group.refresh_status()

    def is_dirty(self) -> bool:
        return self.form() != self._loaded_form

    def cancel_running_worker(self) -> None:
        """Çalışan bir bağlantı testi varsa iptal eder ve kısa bir süre bekler.

        `MainWindow.closeEvent` tarafından çağrılır -- `companies_tab.py::cancel_running_worker`
        ile aynı kalıp.
        """
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(5000)

    def confirm_leave(self) -> bool:
        """Kaydedilmemiş değişiklik varsa kullanıcıya üç seçenek sunar.

        `MainWindow`, sekme değişirken (Ayarlar'dan ÇIKARKEN) bunu çağırır; `False` dönerse
        sekme değişimi geri alınmalıdır.
        """
        if not self.is_dirty():
            return True

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle(UNSAVED_CHANGES_TITLE)
        box.setText(UNSAVED_CHANGES_TEXT)
        save_button = box.addButton(UNSAVED_CHANGES_SAVE, QMessageBox.AcceptRole)
        discard_button = box.addButton(UNSAVED_CHANGES_DISCARD, QMessageBox.DestructiveRole)
        box.addButton(UNSAVED_CHANGES_STAY, QMessageBox.RejectRole)
        box.exec()
        clicked = box.clickedButton()

        if clicked is save_button:
            self._on_save()
            return not self.is_dirty()  # kaydetme hata/onay reddiyle başarısız olduysa sekmede kal
        if clicked is discard_button:
            self._set_form(self._loaded_form)
            return True
        return False

    # -- kaydet / vazgeç / varsayılanlar ---------------------------------------------------

    def _on_save(self) -> None:
        new_form = self.form()
        plan = plan_save(self._loaded_form, new_form)

        if plan.errors:
            QMessageBox.critical(self, "Ayarlar kaydedilemedi", "\n".join(plan.errors))
            return

        for confirmation in plan.confirmations:
            if not self._ask_confirmation(confirmation):
                return

        with get_session(self._engine) as session:
            apply_form(session, new_form)
            session.commit()

        self._loaded_form = new_form
        if plan.warnings:
            QMessageBox.warning(self, "Uyarı", "\n".join(plan.warnings))
        self.settings_saved.emit()

    def _ask_confirmation(self, confirmation: Confirmation) -> bool:
        # Basit Evet/Hayır: `status_strip.py`/`queue_tab.py` ile aynı statik yardımcı.
        answer = QMessageBox.question(
            self,
            confirmation.title,
            confirmation.text,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        return answer == QMessageBox.Yes

    def _on_test_mode_change_requested(self, want_test_mode: bool) -> None:
        """"Gerçek gönderime geç" / "Test moduna dön": hemen yazılır (Kaydet'e bağlı DEĞİL).

        Ekrandaki diğer, kaydedilmemiş düzenlemelere dokunulmaz; yalnızca yüklenen hâl ile ekran
        aynı test modu değerine çekilir ki kirlilik takibi (`is_dirty`) bozulmasın.
        """
        if not switch_test_mode(self, self._engine, want_test_mode):
            return
        self._loaded_form = replace(self._loaded_form, test_mode=want_test_mode)
        self._sending_group.set_test_mode(want_test_mode)
        self.settings_saved.emit()

    def _on_cancel(self) -> None:
        self._set_form(self._loaded_form)

    def _on_restore_defaults(self) -> None:
        answer = QMessageBox.question(
            self,
            "Varsayılanlara dön",
            RESTORE_DEFAULTS_TEXT,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self._set_form(default_form())

    def _on_reopen_wizard(self) -> None:
        """Kurulum sihirbazını baştan açar (`reopen=True`: kaldığı yeri yazmaz, `onboarding_
        completed`'a dokunmaz). Sihirbaz ayarları DEĞİŞTİREBİLİR; bu yüzden önce kaydedilmemiş
        düzenleme sorulur, sonra ekran veritabanından tazelenir."""
        if not self.confirm_leave():
            return
        completed = run_onboarding_wizard(
            self._engine, self._profile_path, reopen=True, parent=self
        )
        self.reload()
        self.settings_saved.emit()
        if completed:  # son sayfadaki "Otopilot'a git" (PLAN.md Adım 10.2m)
            self.autopilot_requested.emit()

    # -- keyring: API anahtarları / mail şifresi ---------------------------------------------

    # Düğmeler grupların içinde bağlı; aşağıdaki ince yöntemler testlerin/dış kodun bildiği
    # eski adları korur (anahtar/şifre işi `settings_groups/*` ve `secret_prompts.py`'de).

    def _refresh_all_key_statuses(self) -> None:
        self._llm_group.refresh_all_key_statuses()
        self._places_group.refresh_key_status()
        self._mail_group.refresh_password_status()

    def _refresh_key_status(self, provider: str) -> None:
        if provider == GOOGLE_PLACES_API_KEY_NAME:
            self._places_group.refresh_key_status()
        else:
            self._llm_group.refresh_key_status(provider)

    def _on_save_api_key(self, provider: str, title: Optional[str] = None) -> None:
        if provider == GOOGLE_PLACES_API_KEY_NAME:
            self._places_group.save_key()
        else:
            self._llm_group.save_api_key(provider, title)

    def _on_delete_api_key(self, provider: str, label: Optional[str] = None) -> None:
        if provider == GOOGLE_PLACES_API_KEY_NAME:
            self._places_group.delete_key()
        else:
            self._llm_group.delete_api_key(provider, label)

    def _on_save_places_key(self) -> None:
        self._places_group.save_key()

    def _on_delete_places_key(self) -> None:
        self._places_group.delete_key()

    def _on_show_places_guide(self) -> None:
        self._places_group.show_guide()

    def _on_save_mail_password(self) -> None:
        self._mail_group.save_password()

    def _on_delete_mail_password(self) -> None:
        self._mail_group.delete_password()

    # -- bağlantı testleri: LLM / Gmail (ağa çıkar, arka planda -- CLAUDE.md kural 8) --------

    def _guard_running(self) -> bool:
        """Zaten çalışan bir bağlantı testi varsa kullanıcıyı uyarır ve `True` döner."""
        if self._worker is not None and self._worker.isRunning():
            QMessageBox.information(
                self, "İş sürüyor", "Başka bir işlem bitmeden yeni bir işlem başlatılamaz."
            )
            return True
        return False

    def _start_job(
        self, title: str, job: Callable[..., Any], on_done: Callable[[Any], None]
    ) -> None:
        def _finish(result: Any) -> None:
            self._worker_finished()
            on_done(result)

        self._worker = run_with_progress(self, title, job, _finish)
        self._worker.failed.connect(lambda _message: self._worker_finished())

    def _worker_finished(self) -> None:
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None

    def _on_test_llm_connection(self) -> None:
        if self._guard_running():
            return

        form = self.form()
        model_by_provider = {
            "claude": form.llm_model_claude,
            "gemini": form.llm_model_gemini,
            "groq": form.llm_model_groq,
        }
        model = model_by_provider.get(form.llm_provider, "")

        self._start_job(
            "LLM bağlantısı test ediliyor",
            partial(llm_test_job, form.llm_provider, model),
            partial(show_llm_test_result, self),
        )

    def _on_test_places_key(self) -> None:
        if self._guard_running():
            return
        self._start_job(
            "Google Places anahtarı test ediliyor",
            places_key_test_job,
            partial(show_places_test_result, self),
        )

    def _on_check_updates_now(self) -> None:
        if self._guard_running():
            return
        self._start_job(
            "Yeni sürüm aranıyor",
            partial(update_check_job, self._engine, force=True),
            self._show_update_check_result,
        )

    def _show_update_check_result(self, result: UpdateCheckJobResult) -> None:
        show_update_check_result(self, result)
        if result.info is not None:
            self.update_available.emit(result.info)

    def _on_test_smtp_connection(self) -> None:
        if self._guard_running():
            return

        form = self.form()
        self._start_job(
            "Mail bağlantısı test ediliyor",
            partial(
                smtp_login_test_job,
                self._profile_path,
                sender_email=form.sender_email,
                sender_name=form.sender_name,
                provider=form.mail_provider,
                custom_smtp_host=form.custom_smtp_host,
                custom_smtp_port=form.custom_smtp_port,
                custom_smtp_security=form.custom_smtp_security,
                custom_imap_host=form.custom_imap_host,
                custom_imap_port=form.custom_imap_port,
                presets=self._mail_presets,
            ),
            partial(show_smtp_test_result, self),
        )
