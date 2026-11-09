"""
Адрес компьютера в локальной сети — по нему телефон откроет открытку.

Проблема: если на ноутбуке включён VPN (AmneziaVPN, WireGuard и т. п.) или есть
виртуальные адаптеры (VirtualBox, Hyper-V, WSL), «главным» оказывается их адрес,
а телефон в той же Wi-Fi по нему компьютер не найдёт. Поэтому смотрим все сетевые
адаптеры и выбираем Wi-Fi или Ethernet, а VPN и виртуальные — в самый конец.
"""
import ipaddress
import socket

VIRTUAL_HINTS = (
    "vpn", "amnezia", "wireguard", "wg", "openvpn", "tun", "tap", "outline", "v2ray",
    "nekoray", "hiddify", "clash", "warp", "proton", "nord", "tailscale", "zerotier",
    "hamachi", "radmin", "virtualbox", "vmware", "vethernet", "hyper-v", "wsl", "docker",
    "loopback", "bluetooth", "npcap", "virtual", "виртуальн",
)
WIFI_HINTS = ("wi-fi", "wifi", "wlan", "wireless", "беспровод")
WIRED_HINTS = ("ethernet", "локальн", "eth", "enp", "eno", "en0", "en1")


def _route_ip():
    """IP интерфейса, через который идёт маршрут по умолчанию (пакеты не отправляются)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def _vpn_like_ip(ip):
    addr = ipaddress.ip_address(ip)
    return (ip.startswith("10.8.")                                  # AmneziaVPN, WireGuard, OpenVPN
            or addr in ipaddress.ip_network("100.64.0.0/10")        # Tailscale, CGNAT
            or ip.startswith("26."))                                # Radmin VPN


def _all_addresses():
    """[(ip, имя адаптера)] для всех включённых адаптеров."""
    found = []
    try:
        import psutil
        stats = psutil.net_if_stats()
        for name, addrs in psutil.net_if_addrs().items():
            if name in stats and not stats[name].isup:
                continue
            found += [(a.address, name) for a in addrs if a.family == socket.AF_INET]
    except Exception:
        # без psutil имён адаптеров не узнать — работаем только по адресам
        try:
            found += [(info[4][0], "") for info in
                      socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)]
        except OSError:
            pass
        route = _route_ip()
        if route:
            found.append((route, ""))
    return found


def candidates():
    """Адреса этого компьютера, лучший первым: [(ip, адаптер, похоже_на_vpn)]."""
    route = _route_ip()
    best = {}
    for ip, name in _all_addresses():
        if ip.startswith(("127.", "169.254.", "0.")):
            continue
        lname = name.lower()
        suspicious = any(h in lname for h in VIRTUAL_HINTS) or _vpn_like_ip(ip)
        if suspicious:
            rank = 9.0
        elif any(h in lname for h in WIFI_HINTS):
            rank = 0.0
        elif any(h in lname for h in WIRED_HINTS):
            rank = 1.0
        else:
            rank = 3.0
        if not ipaddress.ip_address(ip).is_private:
            rank = max(rank, 5.0)
        if ip == route and not suspicious:
            rank -= 0.5  # при прочих равных — адаптер с маршрутом по умолчанию
        if ip not in best or rank < best[ip][1]:
            best[ip] = (name, rank, suspicious)
    ordered = sorted(best.items(), key=lambda kv: (kv[1][1], kv[0]))
    return [(ip, name, susp) for ip, (name, _rank, susp) in ordered]


def best_ip(override=None):
    if override:
        return override
    found = candidates()
    return found[0][0] if found else "127.0.0.1"
