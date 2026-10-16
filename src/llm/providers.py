"""Sağlayıcı adları ve uç nokta adresleri — tek kaynak.

Bu dizeler `llm/factory.py`, `core/settings.py` (ayar değeri), `scripts/set_api_key.py`
(kasadaki kullanıcı adı) ve `scripts/try_llm.py` içinde geçiyor. Hepsi elle yazılsaydı tek bir
yazım hatası, kimsenin bulamayacağı yetim bir kasa kaydı oluştururdu: anahtar "gemeni" adıyla
kaydedilir, uygulama "gemini" adıyla arar ve sessizce anahtar kelimeye düşer.

Uç nokta adresleri bilinçli olarak AYAR DEĞİL, sabit: kullanıcının değiştirmesi gereken bir şey
değil ve yanlış yazılmış bir adres, sebebi anlaşılmayan bir arızaya yol açar. Değişen tek şey
model adıdır, o ayardır (`core/settings.py`).
"""

from src.core.settings import (
    KEY_LLM_MODEL_CLAUDE,
    KEY_LLM_MODEL_GEMINI,
    KEY_LLM_MODEL_GROQ,
)

PROVIDER_NONE = "none"
PROVIDER_CLAUDE = "claude"
PROVIDER_GEMINI = "gemini"
PROVIDER_GROQ = "groq"

#: Ayarlarda `llm_provider` için kabul edilen değerler.
LLM_PROVIDER_NAMES: tuple[str, ...] = (
    PROVIDER_NONE,
    PROVIDER_CLAUDE,
    PROVIDER_GEMINI,
    PROVIDER_GROQ,
)

#: API anahtarı gerektiren sağlayıcılar ("none" ağa çıkmaz, anahtar istemez).
API_KEY_PROVIDERS: tuple[str, ...] = (PROVIDER_CLAUDE, PROVIDER_GEMINI, PROVIDER_GROQ)

#: Her sağlayıcının model adını hangi ayardan okuduğu.
MODEL_SETTING_KEYS: dict[str, str] = {
    PROVIDER_CLAUDE: KEY_LLM_MODEL_CLAUDE,
    PROVIDER_GEMINI: KEY_LLM_MODEL_GEMINI,
    PROVIDER_GROQ: KEY_LLM_MODEL_GROQ,
}

# --- Ağ ayarları (üç sağlayıcı için ortak) ---------------------------------------------------
#: İstek zaman aşımı. `anthropic` ve `openai` SANİYE, `google-genai` MİLİSANİYE bekliyor.
REQUEST_TIMEOUT_SECONDS = 30.0
REQUEST_TIMEOUT_MS = int(REQUEST_TIMEOUT_SECONDS * 1000)

#: Başarısız istekte kaç kez daha denenecek. SDK varsayılanı 2'dir; 30 sn × 3 deneme = 90 sn
#: eder ve masaüstü arayüzünü uzun süre bekletir. Hata olduğunda zaten anahtar kelimeye düşüyoruz.
MAX_RETRIES = 1

#: Cevap için üretilebilecek en fazla token. Cevap küçük bir JSON, ama çok kısarsak JSON ortadan
#: kesilir ve her çağrı hata verir.
MAX_OUTPUT_TOKENS = 1024

#: Gemini'de daha yüksek: 2.5 serisi modellerde "düşünme" (thinking) varsayılan olarak açık ve
#: bu bütçeden harcıyor. Az verilirse düşünme token'ları biter, cevap metni hiç üretilmez.
GEMINI_MAX_OUTPUT_TOKENS = 4096

# Groq, OpenAI'nin sohbet API'siyle uyumlu bir uç nokta sunuyor: `openai` paketi yalnızca
# base_url değiştirilerek kullanılabiliyor (src/llm/openai_compat_provider.py).
GROQ_BASE_URL = "https://api.groq.com/openai/v1"

# Gemini'nin de OpenAI-uyumlu bir ucu var (/v1beta/openai/) ama BİLİNÇLİ OLARAK KULLANILMIYOR:
# Google AI Studio'nun yeni "AQ." ile başlayan anahtarları bu uçta Bearer başlığıyla 401 veriyor,
# native uç ise aynı anahtarla çalışıyor. Bu yüzden Gemini resmi `google-genai` paketiyle,
# kendi uç noktasından bağlanıyor (src/llm/gemini_provider.py). Anahtar biçimine ("AIza" / "AQ.")
# göre HİÇBİR kontrol yapılmaz; iki biçim de kabul edilir.


def describe_llm_error(error: Exception) -> str:
    """Hatayı hata tipi ve varsa HTTP durum koduyla anlatır; anahtarı ASLA yazmaz.

    Üç SDK durum kodunu farklı alanlarda tutuyor: `anthropic` ve `openai` `status_code`,
    `google-genai` ise `code` / `status`. Hepsini tek tek import etmek yerine alanlara bakılır.
    Tek kaynak: hem `scripts/try_llm.py` hem `src/ui/jobs.py::llm_test_job` burayı kullanır.
    """
    parts = [type(error).__name__]
    for field in ("status_code", "code", "status"):
        value = getattr(error, field, None)
        if value is not None:
            parts.append(f"{field}={value}")
            break
    parts.append(str(error))
    return " | ".join(parts)
