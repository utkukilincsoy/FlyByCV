"""Bir KEREL İK üretim betiği — `presets/cities.json`'ı OpenStreetMap Nominatim'den doldurur.

PLAN.md Adım 10.6a. Uygulama çalışırken bu betik HİÇ çağrılmaz; yalnızca geliştirici elle
çalıştırır (ağ isteği yapar, Nominatim kullanım kurallarına uyar: tanımlayıcı User-Agent,
istek başına >=1 saniye bekleme). Çıktı `presets/cities.json`'a yazılır ve git'e commit edilir
— uygulamanın kendisi hiç ağa çıkmadan bu dosyayı okur.

Kullanım:
    python scripts/generate_cities.py
"""
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "flybycv-dev (one-off cities.json generator; https://github.com/)"
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "presets" / "cities.json"

# (plaka kodu, il adı) — resmi 01-81 plaka kodu sıralaması (7 Eylül 1962 yönetmeliği,
# ilk 67'si alfabetik, 68-81 sonradan il olan ilçeler). Nominatim'e gönderilen ad.
PROVINCES: list[tuple[str, str]] = [
    ("01", "Adana"), ("02", "Adıyaman"), ("03", "Afyonkarahisar"), ("04", "Ağrı"),
    ("05", "Amasya"), ("06", "Ankara"), ("07", "Antalya"), ("08", "Artvin"),
    ("09", "Aydın"), ("10", "Balıkesir"), ("11", "Bilecik"), ("12", "Bingöl"),
    ("13", "Bitlis"), ("14", "Bolu"), ("15", "Burdur"), ("16", "Bursa"),
    ("17", "Çanakkale"), ("18", "Çankırı"), ("19", "Çorum"), ("20", "Denizli"),
    ("21", "Diyarbakır"), ("22", "Edirne"), ("23", "Elazığ"), ("24", "Erzincan"),
    ("25", "Erzurum"), ("26", "Eskişehir"), ("27", "Gaziantep"), ("28", "Giresun"),
    ("29", "Gümüşhane"), ("30", "Hakkari"), ("31", "Hatay"), ("32", "Isparta"),
    ("33", "Mersin"), ("34", "İstanbul"), ("35", "İzmir"), ("36", "Kars"),
    ("37", "Kastamonu"), ("38", "Kayseri"), ("39", "Kırklareli"), ("40", "Kırşehir"),
    ("41", "Kocaeli"), ("42", "Konya"), ("43", "Kütahya"), ("44", "Malatya"),
    ("45", "Manisa"), ("46", "Kahramanmaraş"), ("47", "Mardin"), ("48", "Muğla"),
    ("49", "Muş"), ("50", "Nevşehir"), ("51", "Niğde"), ("52", "Ordu"),
    ("53", "Rize"), ("54", "Sakarya"), ("55", "Samsun"), ("56", "Siirt"),
    ("57", "Sinop"), ("58", "Sivas"), ("59", "Tekirdağ"), ("60", "Tokat"),
    ("61", "Trabzon"), ("62", "Tunceli"), ("63", "Şanlıurfa"), ("64", "Uşak"),
    ("65", "Van"), ("66", "Yozgat"), ("67", "Zonguldak"), ("68", "Aksaray"),
    ("69", "Bayburt"), ("70", "Karaman"), ("71", "Kırıkkale"), ("72", "Batman"),
    ("73", "Şırnak"), ("74", "Bartın"), ("75", "Ardahan"), ("76", "Iğdır"),
    ("77", "Yalova"), ("78", "Karabük"), ("79", "Kilis"), ("80", "Osmaniye"),
    ("81", "Düzce"),
]


def fetch_bbox(name: str) -> dict:
    params = {
        "state": name,
        "country": "Turkey",
        "format": "json",
        "limit": "1",
        "featuretype": "state",
    }
    url = f"{NOMINATIM_URL}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if not data or data[0].get("addresstype") != "province":
        raise RuntimeError(f"'{name}' için il sınırı bulunamadı: {data}")
    south, north, west, east = (float(x) for x in data[0]["boundingbox"])
    return {
        "name_osm": data[0]["name"],
        "bbox": [west, south, east, north],  # [min_lon, min_lat, max_lon, max_lat]
        "lat": float(data[0]["lat"]),
        "lon": float(data[0]["lon"]),
    }


def load_existing_districts() -> tuple[dict[str, list[str]], str]:
    """Elle girilmiş (Nominatim'den gelmeyen) ilçe listelerini yeniden üretimde KAYBETME."""
    if not OUTPUT_PATH.is_file():
        return {}, ""
    raw = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
    by_id = {c["id"]: c["districts"] for c in raw.get("cities", []) if c.get("districts")}
    return by_id, raw.get("districts_source", "")


def main() -> None:
    districts, districts_source = load_existing_districts()
    cities = []
    for plate, name in PROVINCES:
        print(f"{plate} {name} ...", end=" ", flush=True)
        info = fetch_bbox(name)
        city = {
            "id": plate,
            "name_tr": name,
            "iso_code": f"TR-{plate}",
            "bbox": info["bbox"],
            "center": [info["lon"], info["lat"]],
        }
        if plate in districts:
            city["districts"] = districts[plate]
        cities.append(city)
        print("tamam")
        time.sleep(1.1)  # Nominatim kullanım kuralı: en fazla 1 istek/saniye

    OUTPUT_PATH.write_text(
        json.dumps({"cities": cities}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"\n{len(cities)} il yazıldı: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
