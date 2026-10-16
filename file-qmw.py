"""
MovieBox upstream client.
- curl_cffi দিয়ে Chrome TLS impersonation (406 fix)
- guest JWT bootstrap (x-user header) — search/detail এর আসল ফিক্স
- mirror fallback + টোকেন অটো-রিফ্রেশ
"""
import base64
import hashlib
import hmac
import json
import random
import threading
import time
import uuid

try:
    from curl_cffi import requests as cffi_requests
    _HAS_CFFI = True
except ImportError:
    _HAS_CFFI = False
    import httpx

GATEWAY_SECRET = "76iRl07s0xSN9jqmEWAt79EBJZulIQIsV64FZr2O"

HOSTS = [
    "api6.aoneroom.com",
    "api5.aoneroom.com",
    "i-api.aoneroom.com",
    "api.inmoviebox.com",
]

APP_VERSION = "4.0.02"
USER_AGENT = "MovieBoxPro/16.2.1 (Android 12; Pixel 6)"
REFERER = "https://api6.aoneroom.com/"
ACCEPT = "application/json"
CONTENT_TYPE = "application/json;charset=UTF-8"

_DEVICE_ID = "".join(random.choices("0123456789abcdef", k=32))
_GAID = str(uuid.uuid4())

_BOOT = {"token": None, "ts": 0.0}
_BOOT_TTL = 600
_BOOT_LOCK = threading.Lock()

_CACHE: dict = {}
_CACHE_LOCK = threading.Lock()


# ---------------------------------------------------------------- cache

def cache_get(key):
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit and hit[1] > time.time():
            return hit[0]
        _CACHE.pop(key, None)
    return None


def cache_put(key, value, ttl=300):
    with _CACHE_LOCK:
        _CACHE[key] = (value, time.time() + ttl)
        if len(_CACHE) > 800:
            now = time.time()
            for k in [k for k, v in _CACHE.items() if v[1] < now]:
                _CACHE.pop(k, None)


# ---------------------------------------------------------------- signing

def _client_token() -> str:
    ts = str(int(time.time() * 1000))
    return ts + "," + hashlib.md5(ts[::-1].encode()).hexdigest()


def _canonical_qs(params: dict) -> str:
    if not params:
        return ""
    pairs = sorted((str(k), str(v)) for k, v in params.items())
    return "&".join(f"{k}={v}" for k, v in pairs)


def _tr_signature(method: str, path_and_query: str, body: bytes) -> str:
    ts = str(int(time.time() * 1000))
    body_md5 = hashlib.md5(body[:102400]).hexdigest() if body else ""
    canonical = "\n".join([
        method.upper(), ACCEPT, CONTENT_TYPE,
        str(len(body)), ts, body_md5, path_and_query,
    ])
    key = base64.b64decode(GATEWAY_SECRET)
    digest = hmac.new(key, canonical.encode(), hashlib.md5).digest()
    return f"{ts}|2|{base64.b64encode(digest).decode()}"


def _client_info() -> str:
    return json.dumps({
        "package_name": "com.community.oneroom",
        "version_name": "4.0.02",
        "version_code": 50020126,
        "os": "android",
        "os_version": "12",
        "install_ch": "ps",
        "install_store": "ps",
        "device_id": _DEVICE_ID,
        "gaid": _GAID,
        "brand": "Redmi",
        "model": "2201117TG",
        "system_language": "en",
        "net": "NETWORK_WIFI",
        "region": "US",
        "timezone": "UTC",
        "sp_code": "40401",
        "X-Play-Mode": "2",
    }, separators=(",", ":"))


def _headers(method, path_and_query, body, token=None):
    h = {
        "User-Agent": USER_AGENT,
        "X-M-Version": APP_VERSION,
        "Accept": ACCEPT,
        "Accept-Language": "en-US,en;q=0.9",
        "Content-Type": CONTENT_TYPE,
        "Referer": REFERER,
        "Origin": REFERER.rstrip("/"),
        "X-Client-Token": _client_token(),
        "x-tr-signature": _tr_signature(method, path_and_query, body),
        "X-Client-Info": _client_info(),
        "X-Client-Status": "0",
        "X-Play-Mode": "2",
        "Connection": "keep-alive",
    }
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


# ---------------------------------------------------------------- transport

def new_session():
    if _HAS_CFFI:
        return cffi_requests.Session(impersonate="chrome")
    return httpx.Client(timeout=25, follow_redirects=True)


def _raw(session, method, url, headers, body):
    if _HAS_CFFI:
        return session.request(method, url, headers=headers,
                               data=body if body else None, timeout=25)
    return session.request(method, url, headers=headers,
                           content=body if body else None)


def _json(resp):
    try:
        return resp.json()
    except Exception:
        return json.loads(resp.text)


def bootstrap(session=None, force=False):
    """প্রথম unauthenticated রিকোয়েস্টে x-user হেডার থেকে guest JWT নেয়।"""
    with _BOOT_LOCK:
        now = time.time()
        if not force and _BOOT["token"] and (now - _BOOT["ts"]) < _BOOT_TTL:
            return _BOOT["token"]

        s = session or new_session()
        pq = ("/wefeed-mobile-bff/tab-operating"
              "?host=api.inmoviebox.com&page=1&pageSize=24&tabId=1")

        for host in HOSTS:
            try:
                r = _raw(s, "GET", f"https://{host}{pq}", _headers("GET", pq, b""), b"")
                tok = (r.headers.get("x-user")
                       or r.headers.get("X-User")
                       or r.headers.get("x-user-token"))
                if tok:
                    _BOOT["token"] = tok
                    _BOOT["ts"] = now
                    return tok
            except Exception:
                continue
        return None


def send(method, path, params=None, json_body=None, host=None,
         auth=True, retry=True, session=None):
    s = session or new_session()

    qs = _canonical_qs(params or {})
    pq = path + (f"?{qs}" if qs else "")
    body = json.dumps(json_body, separators=(",", ":")).encode() if json_body else b""

    token = bootstrap(s) if auth else None
    headers = _headers(method, pq, body, token)

    hosts = [host] if host else HOSTS
    last = None

    for h in hosts:
        try:
            r = _raw(s, method, f"https://{h}{pq}", headers, body)

            if r.status_code in (401, 403, 406) and auth and retry:
                fresh = bootstrap(s, force=True)
                if fresh and fresh != token:
                    r2 = _raw(s, method, f"https://{h}{pq}",
                              _headers(method, pq, body, fresh), body)
                    if r2.status_code == 200:
                        return r2
                last = f"{h} -> HTTP {r.status_code}"
                continue

            if r.status_code == 200:
                return r
            last = f"{h} -> HTTP {r.status_code}"
        except Exception as e:
            last = f"{h} -> {type(e).__name__}: {e}"

    raise RuntimeError(f"all mirrors failed: {last}")


# ---------------------------------------------------------------- helpers

def pick(d, *keys, default=None):
    for k in keys:
        if isinstance(d, dict) and d.get(k) not in (None, "", []):
            return d[k]
    return default


def norm_item(it: dict) -> dict:
    cover = pick(it, "cover", "image", default={}) or {}
    return {
        "name": pick(it, "title", "name", default=""),
        "subject_id": str(pick(it, "subjectId", "subject_id", "id", default="")),
        "slug": pick(it, "detailPath", "detail_path", "slug", default=""),
        "poster_url": pick(cover, "url", default="") if isinstance(cover, dict) else str(cover),
        "year": str(pick(it, "releaseDate", "year", default=""))[:4],
        "rating": str(pick(it, "imdbRatingValue", "rating", default="")),
        "subject_type": pick(it, "subjectType", "subject_type", default=1),
    }


def extract_items(payload: dict) -> list:
    data = payload.get("data", payload)
    for k in ("items", "list", "subjects", "results", "records"):
        v = data.get(k) if isinstance(data, dict) else None
        if isinstance(v, list):
            return v
    return []


def _resolve_stream(s: dict) -> dict:
    """signCookie এর ভেতরে লুকানো আসল ম্যানিফেস্ট বের করে (dummy-video bypass)।"""
    url = s.get("url") or ""
    cookie = pick(s, "signCookie", "sign_cookie", default="") or ""

    real = None
    if "urlprefix=" in cookie:
        try:
            b64 = cookie.split("urlprefix=")[1].split(":")[0].split("&")[0]
            base = base64.b64decode(b64 + "=" * (-len(b64) % 4)).decode()
            real = base.rstrip("/") + "/index.m3u8"
        except Exception:
            real = None

    fmt = "MP4"
    if real:
        fmt = "HLS"
    elif url.endswith(".mpd"):
        fmt = "DASH"
    elif url.endswith(".m3u8"):
        fmt = "HLS"

    return {
        "quality": pick(s, "quality", "resolution", default=""),
        "format": fmt,
        "url": real or url,
        "fake_url": url if real else None,
        "cookie": cookie,
        "size": pick(s, "size", default=None),
        "duration": pick(s, "duration", default=None),
        "codec": pick(s, "codec", default=None),
        "headers": {"User-Agent": USER_AGENT, "Referer": REFERER},
    }


# ---------------------------------------------------------------- endpoints

def home() -> dict:
    ck = "home"
    if (v := cache_get(ck)):
        return v
    r = send("GET", "/wefeed-mobile-bff/tab-operating",
             params={"host": "api.inmoviebox.com", "page": 1, "pageSize": 24, "tabId": 1})
    data = _json(r)
    cache_put(ck, data, 300)
    return data


def search(query: str, page: int = 1) -> dict:
    path = "/wefeed-mobile-bff/subject-api/search"
    errors = []

    for method, params in [
        ("POST", {"keyword": query, "type": 0, "page": page, "pageSize": 20}),
        ("GET", {"q": query, "page": page, "pageSize": 20}),
    ]:
        try:
            if method == "POST":
                r = send("POST", path, json_body=params)
            else:
                r = send("GET", path, params=params)
            got = [norm_item(i) for i in extract_items(_json(r))]
            if got:
                return {"query": query, "page": page, "total": len(got), "items": got}
            errors.append(f"{method} -> empty")
        except Exception as e:
            errors.append(f"{method} -> {e}")

    raise RuntimeError("search failed: " + " | ".join(errors))


def suggest(query: str) -> dict:
    r = send("GET", "/wefeed-mobile-bff/subject-api/search-suggest",
             params={"keyword": query})
    payload = _json(r)
    data = payload.get("data", payload)
    raw = data.get("items") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        raw = data if isinstance(data, list) else []

    out = []
    for it in raw:
        if isinstance(it, str):
            out.append({"title": it, "slug": None, "subject_id": None})
        elif isinstance(it, dict):
            out.append({
                "title": pick(it, "title", "name", "keyword", default=""),
                "slug": pick(it, "detailPath", "detail_path", default=None),
                "subject_id": pick(it, "subjectId", "subject_id", default=None),
            })
    return {"suggestions": out[:12]}


def detail(subject_id: str) -> dict:
    ck = f"detail:{subject_id}"
    if (v := cache_get(ck)):
        return v

    path = "/wefeed-mobile-bff/subject-api/get"
    for host in HOSTS:
        try:
            r = send("GET", path, params={"subjectId": str(subject_id), "host": host}, host=host)
            data = _json(r).get("data", {})
            if isinstance(data, dict) and data:
                data.setdefault("subjectId", str(subject_id))
                cache_put(ck, data, 900)
                return data
        except Exception:
            continue
    raise RuntimeError(f"detail failed: {subject_id}")


def episodes(subject_id: str) -> dict:
    path = "/wefeed-mobile-bff/subject-api/season-info"
    try:
        r = send("GET", path, params={"subjectId": str(subject_id)})
        data = _json(r).get("data", {})
    except Exception:
        data = {}

    seasons = []
    for s in (data.get("seasonList") or data.get("seasons") or []):
        seasons.append({
            "season": pick(s, "se", "season", "seasonNumber", default=0),
            "episode_count": pick(s, "episodeCount", "episode_count", "count", default=0),
        })
    return {"subject_id": str(subject_id), "seasons": seasons}


def streams(subject_id: str, se: int = 0, ep: int = 0, quality: int = 1080) -> dict:
    ck = f"stream:{subject_id}:{se}:{ep}"
    if (v := cache_get(ck)):
        return v

    path = "/wefeed-mobile-bff/subject-api/play-info"
    params = {"subjectId": str(subject_id), "se": se, "ep": ep, "quality": quality}

    data = {}
    try:
        r = send("GET", path, params=params)
        data = _json(r).get("data", {}) or {}
    except Exception:
        data = {}

    raw = data.get("streamList") or data.get("streams") or []
    out = [_resolve_stream(s) for s in raw if isinstance(s, dict)]

    # সব URL একই dummy হলে বা খালি হলে legacy carrier এ ফলব্যাক
    if not out:
        try:
            r = send("POST", "/index/video/v_detail",
                     json_body={"subjectId": str(subject_id), "carrier": "301", "quality": "720p"})
            leg = _json(r).get("data", {}) or {}
            for s in (leg.get("streamList") or leg.get("list") or []):
                if isinstance(s, dict):
                    out.append(_resolve_stream(s))
        except Exception:
            pass

    result = {
        "subject_id": str(subject_id),
        "season": se,
        "episode": ep,
        "count": len(out),
        "streams": out,
        "subtitles": data.get("subTitleList") or [],
    }
    cache_put(ck, result, 240)
    return result


def captions(subject_id: str, se: int = 0, ep: int = 0, resource_id: str = "") -> dict:
    path = "/wefeed-mobile-bff/subject-api/get-ext-captions"
    params = {"subjectId": str(subject_id), "episode": ep}
    if resource_id:
        params["resourceId"] = resource_id

    try:
        r = send("GET", path, params=params)
        data = _json(r).get("data", {}) or {}
    except Exception:
        data = {}

    caps = data.get("captions") or data.get("subTitleList") or []
    out = []
    for c in caps:
        if isinstance(c, dict):
            out.append({
                "lan": pick(c, "lan", "language", "lanCode", default=""),
                "lanName": pick(c, "lanName", "languageName", default=""),
                "url": pick(c, "url", "subUrl", default=""),
            })
    return {"subject_id": str(subject_id), "se": se, "ep": ep,
            "count": len(out), "captions": out}


def catalog(kind: str = "movie", page: int = 1, page_size: int = 24) -> dict:
    ck = f"cat:{kind}:{page}"
    if (v := cache_get(ck)):
        return v

    tab = "2" if kind == "movie" else "5"
    ft = json.dumps({"classify": "All", "country": "All", "genre": "All",
                     "sort": "Latest", "year": "All"}, separators=(",", ":"))

    candidates = [
        ("/wefeed-mobile-bff/subject-api/list",
         {"type": "/home/movieFilter", "tabId": tab, "page": page,
          "pageSize": page_size, "filterType": ft}),
        ("/wefeed-mobile-bff/subject-api/list",
         {"tabId": tab, "page": page, "pageSize": page_size, "sort": "RECOMMEND"}),
        ("/wefeed-mobile-bff/subject-api/filter-items",
         {"tabId": tab, "page": page, "pageSize": page_size}),
    ]

    for path, params in candidates:
        try:
            r = send("GET", path, params=params)
            items = [norm_item(i) for i in extract_items(_json(r))]
            if items:
                res = {"kind": kind, "page": page, "per_page": page_size,
                       "count": len(items), "items": items}
                cache_put(ck, res, 300)
                return res
        except Exception:
            continue

    raise RuntimeError(f"catalog failed: {kind}")


# ---------------------------------------------------------------- debug

def probe(query: str = "avatar") -> dict:
    s = new_session()
    tok = bootstrap(s, force=True)

    out = {
        "curl_cffi": _HAS_CFFI,
        "guest_token": (tok[:26] + "…") if tok else None,
        "tests": [],
    }

    cases = [
        ("GET", "/wefeed-mobile-bff/tab-operating",
         {"host": "api.inmoviebox.com", "page": 1, "pageSize": 24, "tabId": 1}, None, False),
        ("POST", "/wefeed-mobile-bff/subject-api/search", None,
         {"keyword": query, "type": 0, "page": 1, "pageSize": 20}, True),
        ("GET", "/wefeed-mobile-bff/subject-api/get",
         {"subjectId": "8906247916759695608"}, None, True),
        ("GET", "/wefeed-mobile-bff/subject-api/list",
         {"tabId": "2", "page": 1, "pageSize": 24}, None, True),
    ]

    for method, path, params, body, auth in cases:
        qs = _canonical_qs(params or {})
        pq = path + (f"?{qs}" if qs else "")
        raw = json.dumps(body, separators=(",", ":")).encode() if body else b""
        t = bootstrap(s) if auth else None
        entry = {"method": method, "path": path, "with_token": bool(t)}
        try:
            r = _raw(s, method, f"https://{HOSTS[0]}{pq}", _headers(method, pq, raw, t), raw)
            entry["status"] = r.status_code
            entry["body_head"] = r.text[:220]
        except Exception as e:
            entry["status"] = "ERR"
            entry["body_head"] = f"{type(e).__name__}: {e}"
        out["tests"].append(entry)

    return out