"""Yayın öncesi tarama: depoya girecek dosyalarda kişisel veri, sır ve yerel yol arar (Adım 10.9i).

Kullanım:
    python scripts/prepublish_scan.py            # bulgu varsa çıkış kodu 1
    python scripts/prepublish_scan.py --no-personal   # yerel profil/git bilgisini aramadan (yalnız kalıplar)

Taranan dosyalar: `git ls-files --cached --others --exclude-standard` yani izlenen VE henüz eklenmemiş ama
`.gitignore` ile dışlanmamış her dosya (yeni temiz depoya girecek olanlar). Aranan şeyler:
  - yasak dosya adı/türü (veritabanı, log, PDF, .env, gerçek profil ...),
  - anahtar/şifre kalıpları (Anthropic, OpenAI, Google, Groq, GitHub, AWS anahtarı, özel anahtar bloğu),
  - yerel Windows/Unix kullanıcı yolları (`C:\\Users\\<ad>\\...`),
  - kişisel değerler: yerel `data/master_profile.json`'daki ad, e-posta, telefon, LinkedIn, şirket/okul/proje adı ile `git config`
    e-postası/adı ve bu bilgisayarın kullanıcı adı. Bu değerler KODA YAZILMAZ, her çalıştırmada yerelden okunur.

Bulgular dosya:satır ve kural adıyla yazılır; bulunan değerin kendisi ekrana TAM basılmaz (maskelenir).
Bilerek içeren dosyalar (`LICENSE`, kurucunun yayıncı adı) yalnızca "ad" kuralından muaftır.
"""
import argparse
import json
import logging
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent

logger = logging.getLogger(__name__)

#: Depoya ASLA girmemesi gereken tam dosya adları (küçük harf). `PLAN.md` özel geliştirme günlüğüdür
#: (yerelde kalır); eski depoda hâlâ izlendiği için orada bu bulgu beklenir, yeni temiz depoda çıkmamalı.
FORBIDDEN_NAMES = {"plan.md", "app.db", "master_profile.json", "master_profile.json.bak", "cv.pdf", "profil_kontrol.md"}
#: Depoya ASLA girmemesi gereken uzantılar (küçük harf).
FORBIDDEN_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".db-wal", ".db-shm", ".log", ".pdf", ".env", ".bak", ".pem", ".key"}

#: (kural adı, derlenmiş kalıp). Kalıplar gerçek anahtar biçimlerini yakalar; örnek yer tutucuları (ör. "sk-...") yakalamaz.
SECRET_PATTERNS: list[tuple[str, "re.Pattern[str]"]] = [
    ("anthropic-key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")),
    ("openai-key", re.compile(r"sk-(?!ant-)(?:proj-)?[A-Za-z0-9_\-]{32,}")),
    ("google-api-key", re.compile(r"AIza[0-9A-Za-z_\-]{35}")),
    ("groq-key", re.compile(r"gsk_[A-Za-z0-9]{30,}")),
    ("github-token", re.compile(r"(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{50,}")),
    ("aws-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("private-key-block", re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----")),
    (
        "password-assignment",
        re.compile(r"""(?i)\b(?:password|passwd|parola|sifre|şifre)\b\s*[:=]\s*["'][^"'\s<>{}$%]{6,}["']"""),
    ),
]

#: Windows/Unix kullanıcı klasörü yolları; genel yer tutucu adlar bulgu sayılmaz.
_LOCAL_PATH = re.compile(r"(?i)\b[A-Z]:\\Users\\([^\\\s\"'<>|*?]+)|/(?:Users|home)/([A-Za-z0-9._\-]+)/")
PLACEHOLDER_USERS = {"public", "default", "user", "username", "kullanici", "kullanıcı", "<user>", "%username%", "all users"}

#: Kişisel değer ARAMASINDAN muaf (bilerek ad içeren) dosyalar: yalnızca "ad" türü için. `version.py`
#: yayın deposunun GitHub kullanıcı adını (`GITHUB_REPO`) içerir.
NAME_EXEMPT_FILES = {"LICENSE", "README.md", "installer/flybycv.iss", "src/core/version.py"}

#: Belirli bir kural için yol önekine göre muafiyet: test dosyaları sahte parola kullanır (gerçek anahtar
#: kalıpları testlerde de aranır).
RULE_EXEMPT_PREFIXES = {"password-assignment": ("tests/",)}

#: Geliştirme aşamasındaki easter egg'in kalıntıları (tetikleyici kelime, dosya adı, kişisel mail kullanıcı adı).
#: Yayından önce tamamen silinmeli; bulunursa yayın engellenir. Kalıplar bu dosya kendini yakalamasın diye
#: parçalı yazılmıştır.
LEFTOVER_TERMS = ("bean" + "boo", "easter" + "_egg", "aliutku" + "2001")

#: Bu kadar kısa değerler aranmaz (yanlış pozitif).
MIN_PERSONAL_LENGTH = 5

_MAX_FILE_BYTES = 2_000_000


@dataclass(frozen=True)
class Finding:
    path: str
    line: int  # 0 = dosya düzeyi bulgu
    rule: str
    detail: str


@dataclass(frozen=True)
class PersonalValue:
    kind: str  # "name" | "email" | "phone" | "linkedin" | "username" | "profile-item"
    value: str


def mask(value: str) -> str:
    """Değerin yalnızca baş/son iki karakterini gösterir."""
    if len(value) <= 6:
        return "***"
    return f"{value[:2]}…{value[-2:]} ({len(value)} karakter)"


def check_file_name(rel_path: str) -> Optional[Finding]:
    name = Path(rel_path).name.lower()
    suffix = Path(rel_path).suffix.lower()
    if "easter" + "_egg" in rel_path.lower():
        return Finding(rel_path, 0, "easter-egg", "easter egg dosyası: yayından önce silinmeli")
    if name in FORBIDDEN_NAMES or suffix in FORBIDDEN_SUFFIXES or name.startswith(".env"):
        return Finding(rel_path, 0, "forbidden-file", "yasak dosya adı/türü")
    return None


def _normalize_digits(text: str) -> str:
    return re.sub(r"\D", "", text)


def scan_text(rel_path: str, text: str, personal: Iterable[PersonalValue] = ()) -> list[Finding]:
    findings: list[Finding] = []
    personal_values = [p for p in personal if len(p.value) >= MIN_PERSONAL_LENGTH]
    for number, line in enumerate(text.splitlines(), start=1):
        for rule, pattern in SECRET_PATTERNS:
            match = pattern.search(line)
            if match and not rel_path.startswith(RULE_EXEMPT_PREFIXES.get(rule, ())):
                findings.append(Finding(rel_path, number, rule, mask(match.group(0))))
        for match in _LOCAL_PATH.finditer(line):
            user = (match.group(1) or match.group(2) or "").strip()
            if user.lower() not in PLACEHOLDER_USERS:
                findings.append(Finding(rel_path, number, "local-path", mask(match.group(0))))
        lowered = line.lower()
        for term in LEFTOVER_TERMS:
            if term in lowered:
                findings.append(Finding(rel_path, number, "easter-egg", mask(term)))
        digits = _normalize_digits(line)
        for item in personal_values:
            if item.kind == "name" and rel_path in NAME_EXEMPT_FILES:
                continue
            if item.kind == "phone":
                hit = _normalize_digits(item.value) in digits
            else:
                hit = item.value.lower() in lowered
            if hit:
                findings.append(Finding(rel_path, number, f"personal-{item.kind}", mask(item.value)))
    return findings


def list_candidate_files(root: Path) -> list[str]:
    """İzlenen + izlenmeyen ama `.gitignore` ile dışlanmamış dosyalar (silinmiş olanlar hariç)."""
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=root, capture_output=True, check=True,
    )
    names = [n for n in result.stdout.decode("utf-8").split("\0") if n]
    return sorted({n for n in names if (root / n).is_file()})


def read_text(path: Path) -> Optional[str]:
    """Metin dosyasını döndürür; ikili/çok büyük/okunamaz dosya için None."""
    try:
        if path.stat().st_size > _MAX_FILE_BYTES:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    if b"\0" in data:
        return None
    return data.decode("utf-8", errors="replace")


def scan_files(root: Path, files: Iterable[str], personal: Iterable[PersonalValue] = ()) -> list[Finding]:
    personal = list(personal)
    findings: list[Finding] = []
    for rel_path in files:
        name_finding = check_file_name(rel_path)
        if name_finding:
            findings.append(name_finding)
            continue  # yasak dosyanın içeriğini ayrıca basmaya gerek yok
        text = read_text(root / rel_path)
        if text is not None:
            findings.extend(scan_text(rel_path, text, personal))
    return findings


def _git_config(root: Path, key: str) -> str:
    try:
        out = subprocess.run(["git", "config", key], cwd=root, capture_output=True, check=False)
    except OSError:
        return ""
    return out.stdout.decode("utf-8", errors="replace").strip()


def _profile_item_values(profile_path: Path) -> list[PersonalValue]:
    """Profildeki şirket, okul ve proje adları: koda/belgeye sızarsa kişiyi ele verir."""
    try:
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    names: list[str] = []
    for section, key in (("experience", "company"), ("education", "school"), ("projects", "name")):
        for item in profile.get(section) or []:
            if isinstance(item, dict):
                names.append(str(item.get(key) or "").strip())
    return [PersonalValue("profile-item", n) for n in names if n]


def load_personal_values(root: Path) -> list[PersonalValue]:
    """Yerel profilden ve `git config`'ten kişisel değerleri okur (koda yazılmaz)."""
    values: list[PersonalValue] = []
    profile_path = root / "data" / "master_profile.json"
    if profile_path.is_file():
        try:
            info = json.loads(profile_path.read_text(encoding="utf-8")).get("personal_info", {})
        except (OSError, ValueError):
            logger.warning("Yerel profil okunamadı: %s", profile_path)
            info = {}
        for key in ("name", "email", "phone", "linkedin"):
            value = str(info.get(key) or "").strip()
            if value:
                values.append(PersonalValue(key, value))
        values.extend(_profile_item_values(profile_path))
    else:
        logger.warning("data/master_profile.json yok: profil değerleri aranmayacak.")
    for key, kind in (("user.email", "email"), ("user.name", "name")):
        value = _git_config(root, key)
        if value:
            values.append(PersonalValue(kind, value))
    username = Path.home().name
    if username:
        values.append(PersonalValue("username", username))
    return list({(v.kind, v.value.lower()): v for v in values}.values())


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Yayın öncesi kişisel veri / sır taraması.")
    parser.add_argument("--no-personal", action="store_true", help="Yerel profil ve git bilgisini aramaz.")
    args = parser.parse_args(argv)

    # Windows konsolu varsayılan olarak cp1252 kullanır; Türkçe karakterler logda patlamasın.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    personal = [] if args.no_personal else load_personal_values(PROJECT_ROOT)
    files = list_candidate_files(PROJECT_ROOT)
    logger.info("%d dosya taranıyor (%d kişisel değer aranıyor)...", len(files), len(personal))

    findings = scan_files(PROJECT_ROOT, files, personal)
    for f in findings:
        where = f.path if f.line == 0 else f"{f.path}:{f.line}"
        logger.info("[%s] %s -- %s", f.rule, where, f.detail)
    if findings:
        logger.info("\nBULGU VAR: %d. Yayınlamadan önce her birini incele/temizle.", len(findings))
        return 1
    logger.info("Temiz: bulgu yok. (Bu tarama elle incelemenin yerini tutmaz.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
