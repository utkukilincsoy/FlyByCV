"""Site taramasının yalnızca genel (public) internet adreslerine gitmesini sağlar (SSRF koruması).

Taranan web adresleri dışarıdan gelir (OpenStreetMap, CSV, Google Places, teknokent listeleri).
Kötü niyetli bir kayıt — ya da yönlendirme yapan bir site — tarayıcıyı `127.0.0.1`, `192.168.1.1`
gibi kullanıcının KENDİ bilgisayarına veya ev ağındaki bir cihaza (modem arayüzü...) istek
atmaya yöneltebilirdi. Her istekten (ve her yönlendirme adımından) önce host IP'ye çevrilir;
yalnızca genel adreslere izin verilir.

Bilinen sınır: bu kontrol ile gerçek bağlantı DNS'i AYRI AYRI çözer. Arada adresi değiştiren bir
DNS sunucusu ("DNS rebinding") teorik olarak kontrolü atlatabilir. Tam çözüm IP'yi bağlantıya
sabitleyen özel bir transport ister; tarayıcı yalnızca sayfa okuduğu (GET) ve okunanı yalnızca
e-posta aramak için kullandığı için bu artık risk kabul edildi.
"""
import ipaddress
import socket
from typing import Callable, Optional, Union

#: host -> o host'un çözüldüğü IP adresleri (metin). Testler sahte bir resolver verir.
Resolver = Callable[[str], list[str]]

IPAddress = Union[ipaddress.IPv4Address, ipaddress.IPv6Address]


class BlockedAddressError(Exception):
    """İstenen host genel olmayan (yerel/özel) bir IP adresine çıkıyor; istek atılmamalı."""

    def __init__(self, host: str, reason: str) -> None:
        super().__init__(f"{host}: {reason}")
        self.host = host
        self.reason = reason


def _system_resolve(host: str) -> list[str]:
    """Host'u işletim sisteminin DNS çözücüsüyle IP adreslerine çevirir (tekrarsız, sıralı)."""
    infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    return sorted({str(info[4][0]) for info in infos})


def _parse_ip(address: str) -> Optional[IPAddress]:
    """Metni IP adresine çevirir; IP değilse None. IPv6 bölge eki (`fe80::1%eth0`) atılır."""
    try:
        return ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return None


def _describe(ip: IPAddress) -> str:
    """Reddedilen adresin türü (log mesajı için, Türkçe)."""
    if ip.is_loopback:
        return "bu bilgisayarın kendisi (loopback)"
    if ip.is_link_local:
        return "yerel bağlantı adresi (link-local)"
    if ip.is_private:
        return "özel ağ adresi"
    if ip.is_unspecified:
        return "belirtilmemiş adres (0.0.0.0 / ::)"
    if ip.is_multicast:
        return "çoklu yayın adresi"
    return "genel olmayan adres"


def _unwrap(ip: IPAddress) -> IPAddress:
    """`::ffff:127.0.0.1` gibi IPv4-eşlemeli IPv6 adresini içindeki IPv4 adresine çevirir."""
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def is_public_ip(address: str) -> bool:
    """Adres genel internette mi? Loopback, özel ağ, link-local, 0.0.0.0 ve benzerleri False."""
    ip = _parse_ip(address)
    if ip is None:
        return False
    ip = _unwrap(ip)
    return ip.is_global and not ip.is_multicast


def _rejection_reason(address: str) -> Optional[str]:
    """Adres genel değilse nedenini, genelse None döner."""
    ip = _parse_ip(address)
    if ip is None:
        return "geçersiz IP adresi"
    if is_public_ip(address):
        return None
    return _describe(_unwrap(ip))


def ensure_public_host(host: str, resolver: Optional[Resolver] = None) -> None:
    """Host'un çözüldüğü TÜM adresler genel değilse `BlockedAddressError` fırlatır.

    Host bir IP adresiyse resolver hiç çağrılmaz. Bir adres bile yerelse reddedilir: bağlantının
    hangi adrese kurulacağını biz seçmiyoruz. DNS hatası (`socket.gaierror`) olduğu gibi yükselir;
    tarayıcı onu "site açılamadı" olarak işler.
    """
    bare = host.strip().strip("[]").rstrip(".")
    if not bare:
        raise BlockedAddressError(host, "host boş")

    if _parse_ip(bare) is not None:
        addresses = [bare]
    else:
        addresses = (resolver or _system_resolve)(bare)
        if not addresses:
            raise BlockedAddressError(host, "host hiçbir adrese çözülmedi")

    for address in addresses:
        reason = _rejection_reason(address)
        if reason is not None:
            raise BlockedAddressError(host, reason)
