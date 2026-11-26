"""
Motor de scraping multi-tienda en vivo para Chile (RegalaCL).
Carga reglas NLP y de filtrado desde data/domain-rules.json (fuente única de verdad).
Soporta: Falabella, Ripley, Paris, Abcdin/La Polar (abc.cl), Jumbo y Santa Isabel.
"""

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError, as_completed
import hashlib
import html as html_lib
import json
import logging
import os
import re
import time
import unicodedata
import urllib.parse
import urllib.request

logger = logging.getLogger("regalacl.scraper")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RULES_PATH = os.path.join(BASE_DIR, "data", "domain-rules.json")

with open(RULES_PATH, "r", encoding="utf-8") as _f:
    RULES = json.load(_f)

SPANISH_STOPWORDS = set(RULES["stopwords"])
MULTIWORD_SYNONYMS = RULES["multiwordSynonyms"]
PHRASE_SYNONYMS = RULES["phraseSynonyms"]
INTEREST_QUERY_MAP = RULES["interestQueryMap"]
INTEREST_VOCAB = RULES["interestVocab"]
CHILD_RECIPIENTS = set(RULES["childRecipients"])

KIDS_PRODUCT_RE = re.compile(r"\b(?:" + "|".join(RULES["kidsPatterns"]) + r")\b")
NON_GIFTABLE_FOOD_RE = re.compile(r"\b(?:" + "|".join(RULES["nonGiftableFoodPatterns"]) + r")\b")
GIFT_UTENSIL_EXEMPT_RE = re.compile(r"\b(?:" + "|".join(RULES["giftUtensilExemptPatterns"]) + r")\b")
NON_GIFT_CLEANING_RE = re.compile(r"\b(escobilla|escobillon|trapero|dispensador de dulces|alcancia)\b")
BOOK_RE = re.compile(r"\b(libro|novela|cuento|enciclopedia)\b")
MODA_HOMONYMS_RE = re.compile(
    r"\b(flotador\w*|inflable\w*|piscina\w*|bestway|intex|colchoneta\w*|"
    r"bicicleta\w*|neumatico\w*|llanta\w*|basquet\w*|tripode\w*|"
    r"aro de luz|aro \d{2}|cortina\w*|ducha)\b"
)

# Pre-compilar matchers por interés usando límite de palabra \b para stems cortos (<=4 chars)
def _compile_stem(stem: str):
    clean = stem.strip()
    if len(clean) <= 4:
        return re.compile(r"\b" + re.escape(clean) + r"\b")
    return clean


COMPILED_VOCAB = {
    intr: [_compile_stem(s) for s in stems]
    for intr, stems in INTEREST_VOCAB.items()
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/json,*/*;q=0.8",
    "Accept-Language": "es-CL,es;q=0.9",
}

MAX_HTML_BYTES = 6 * 1024 * 1024  # 6 MB cap
GLOBAL_TIME_BUDGET_SEC = 19.0


def normalize_text(text: str) -> str:
    s = unicodedata.normalize("NFD", (text or "").lower())
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _matches_stem(matcher, norm_title: str) -> bool:
    if isinstance(matcher, str):
        return matcher in norm_title
    return bool(matcher.search(norm_title))


def extract_clean_terms(reference: str) -> list:
    norm = normalize_text(reference)
    if not norm:
        return []
    for phrase, replacement in MULTIWORD_SYNONYMS.items():
        if phrase in norm:
            norm = norm.replace(phrase, replacement)

    cleaned = []
    for tok in norm.split():
        if len(tok) <= 2 or tok in SPANISH_STOPWORDS:
            continue
        mapped = PHRASE_SYNONYMS.get(tok, tok)
        for part in mapped.split():
            if part not in cleaned and part not in SPANISH_STOPWORDS:
                cleaned.append(part)
    return cleaned


def build_search_query(interests: list, reference: str) -> str:
    terms = extract_clean_terms(reference)
    if terms:
        return " ".join(terms[:3])
    if interests:
        return INTEREST_QUERY_MAP.get(interests[0], "regalo")
    return "regalo"


def _is_blocked_product(norm_title: str, interests: list, reference: str, recipient: str, store_id: str) -> bool:
    interest_set = set(interests or [])
    ref_norm = normalize_text(reference)

    # 1. Bloquear alimentos/carnes no regalables, salvo si el título indica claramente un utensilio/regalo
    # (ej. "molde de galletas", "tabla de quesos", "aceite esencial", "parrilla a carbón")
    if NON_GIFTABLE_FOOD_RE.search(norm_title):
        if store_id in ("jumbo", "santaisabel") or not GIFT_UTENSIL_EXEMPT_RE.search(norm_title):
            return True

    if store_id in ("jumbo", "santaisabel") and not (interest_set & {"cafe_vinos", "mascotas"}):
        if re.search(r"\b\d+\s*(kg|g|gr|grs|ml|cc|lt|l)\b", norm_title) and not (interest_set & {"belleza", "bienestar"}):
            return True

    # 2. Bloquear artículos de limpieza y libros no solicitados
    if NON_GIFT_CLEANING_RE.search(norm_title):
        return True
    if "limpiador" in norm_title and "belleza" not in interest_set and "automovil" not in interest_set:
        return True
    if "lectura" not in interest_set and not BOOK_RE.search(ref_norm) and BOOK_RE.search(norm_title):
        return True

    # 3. Evitar homónimos de "aros" (flotadores, bicicletas, aros de luz) en Moda/Accesorios
    if (interest_set & {"moda", "accesorios"}) and not (interest_set & {"deporte", "juguetes", "hogar", "automovil"}):
        if MODA_HOMONYMS_RE.search(norm_title):
            return True

    # 4. Excluir juguetes infantiles cuando el destinatario es adulto y no pidió juguetes/bebés
    is_adult = recipient not in CHILD_RECIPIENTS and recipient != "cualquiera"
    wants_kids = bool(interest_set & {"juguetes", "bebes"})
    if is_adult and not wants_kids and KIDS_PRODUCT_RE.search(norm_title):
        return True

    return False


def is_product_relevant(title: str, interests: list, reference: str, recipient: str = "cualquiera", store_id: str = "") -> tuple:
    norm_title = normalize_text(title)
    if _is_blocked_product(norm_title, interests, reference, recipient, store_id):
        return False, []

    ref_terms = extract_clean_terms(reference)
    matched = []
    for tok in ref_terms:
        stem = tok[:6] if len(tok) >= 6 else tok
        if _matches_stem(_compile_stem(stem), norm_title):
            matched.append(tok)

    for intr in (interests or []):
        for matcher in COMPILED_VOCAB.get(intr, []):
            if _matches_stem(matcher, norm_title):
                label = matcher if isinstance(matcher, str) else intr
                matched.append(label)

    if not ref_terms and not interests:
        return True, []

    return len(matched) > 0, list(dict.fromkeys(matched))[:4]


# ---------------------------------------------------------------------------
# Utilidades HTTP y extractores reutilizables
# ---------------------------------------------------------------------------
def _fetch_html(url: str, timeout: float = 8.0) -> str:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(MAX_HTML_BYTES).decode("utf-8", errors="ignore")


def _make_id(store_id: str, title: str, url: str = "") -> str:
    digest = hashlib.md5(f"{store_id}:{title}:{url}".encode("utf-8")).hexdigest()[:10]
    return f"live-{store_id}-{digest}"


def _parse_clp(val) -> int:
    if isinstance(val, (int, float)):
        return int(val)
    if isinstance(val, list) and val:
        val = val[0]
    digits = re.sub(r"[^\d]", "", str(val or ""))
    return int(digits) if digits else 0


def _safe_float(val, default: float = 4.7) -> float:
    try:
        num = float(val)
        return num if 0 < num <= 5 else default
    except (TypeError, ValueError):
        return default


def _safe_int(val, default: int = 20) -> int:
    try:
        return max(0, int(val))
    except (TypeError, ValueError):
        return default


def _normalize_url(url: str, domain: str) -> str:
    if not url:
        return ""
    u = html_lib.unescape(str(url)).strip()
    if u.startswith("//"):
        return "https:" + u
    if u.startswith("/"):
        return f"https://{domain}{u}"
    return u.replace("http://", "https://")


def _price_acceptable(price: int, min_price: int, max_price: int) -> bool:
    if price <= 0:
        return False
    if min_price and price < int(min_price * 0.65):
        return False
    if max_price and price > int(max_price * 1.25):
        return False
    return True


def _detect_interests(title: str, requested_interests: list) -> list:
    norm_title = normalize_text(title)
    detected = [
        intr for intr, matchers in COMPILED_VOCAB.items()
        if any(_matches_stem(m, norm_title) for m in matchers)
    ]
    if not detected and requested_interests:
        detected = requested_interests[:1]
    return detected[:3]


def _extract_next_data(raw_html: str) -> dict:
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', raw_html, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(1))
    except Exception:
        return {}


def _extract_jsonld_items(raw_html: str) -> list:
    blocks = re.findall(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', raw_html, re.S)
    for b in blocks:
        try:
            d = json.loads(b.strip())
        except Exception:
            continue
        if isinstance(d, dict):
            me = d.get("mainEntity")
            if isinstance(me, dict) and isinstance(me.get("itemListElement"), list):
                return me["itemListElement"]
            if isinstance(d.get("itemListElement"), list):
                return d["itemListElement"]
    return []


def _build_store_items(store_id: str, domain: str, raw_candidates: list, ctx: dict, limit: int) -> list:
    """Pipeline único de normalización, filtrado y armado de productos para todas las tiendas."""
    items = []
    for c in raw_candidates:
        title = (c.get("title") or "").strip()
        brand = (c.get("brand") or "").strip()
        if title.isupper():
            title = title.title()
        full_title = f"{brand} {title}".strip() if (brand and brand.lower() not in title.lower()) else title
        img = _normalize_url(c.get("image") or "", domain)
        prod_url = _normalize_url(c.get("url") or "", domain)
        price = _parse_clp(c.get("price"))
        old_price = _parse_clp(c.get("oldPrice"))

        if not full_title or not img:
            continue
        relevant, matched_terms = is_product_relevant(
            full_title, ctx["interests"], ctx["reference"], ctx["recipient"], store_id
        )
        if not relevant or not _price_acceptable(price, ctx["min_price"], ctx["max_price"]):
            continue

        items.append({
            "id": _make_id(store_id, full_title, prod_url),
            "title": full_title,
            "storeId": store_id,
            "price": price,
            "oldPrice": old_price if (old_price and old_price > price) else None,
            "rating": _safe_float(c.get("rating"), 4.7),
            "reviews": _safe_int(c.get("reviews"), 25),
            "image": img,
            "bg": "#ffffff",
            "url": prod_url,
            "interests": _detect_interests(full_title, ctx["interests"]),
            "matchedTerms": matched_terms,
            "keywords": normalize_text(full_title).split(),
        })
        if len(items) >= limit:
            break
    return items


# ---------------------------------------------------------------------------
# Adaptadores ligeros por tienda chilena
# ---------------------------------------------------------------------------
def scrape_falabella(query: str, ctx: dict) -> list:
    q = urllib.parse.quote(query)
    nd = _extract_next_data(_fetch_html(f"https://www.falabella.com/falabella-cl/search?Ntt={q}"))
    results = nd.get("props", {}).get("pageProps", {}).get("results") or []
    candidates = []
    for r in results[:36]:
        price, old_price = 0, None
        for p in r.get("prices") or []:
            pval = _parse_clp(p.get("price"))
            if not pval:
                continue
            if p.get("crossed"):
                old_price = pval
            elif p.get("type") in ("eventPrice", "internetPrice") or price == 0:
                price = pval
        media = r.get("mediaUrls") or []
        candidates.append({
            "title": r.get("displayName"),
            "brand": r.get("brand"),
            "price": price,
            "oldPrice": old_price,
            "image": media[0] if media else "",
            "url": r.get("url") or f"/falabella-cl/search?Ntt={q}",
            "rating": r.get("rating"),
            "reviews": r.get("totalReviews"),
        })
    return _build_store_items("falabella", "www.falabella.com", candidates, ctx, limit=15)


def scrape_ripley(query: str, ctx: dict) -> list:
    q = urllib.parse.quote(query)
    nd = _extract_next_data(_fetch_html(f"https://simple.ripley.cl/search/{q}"))
    prods = (
        nd.get("props", {}).get("pageProps", {})
        .get("findabilityProps", {}).get("data", {}).get("products") or []
    )
    candidates = []
    for r in prods[:36]:
        imgs = r.get("images") or []
        sku = r.get("sku") or r.get("code") or ""
        rr = r.get("rating_reviews") if isinstance(r.get("rating_reviews"), dict) else {}
        candidates.append({
            "title": r.get("name"),
            "brand": r.get("brand"),
            "price": r.get("priceNumber") or r.get("price") or r.get("ripleyPriceNumber"),
            "oldPrice": r.get("masterPriceNumber") or r.get("oldPrice"),
            "image": r.get("primaryImage") or (imgs[0] if imgs else ""),
            "url": r.get("url") or (f"/search/{urllib.parse.quote(str(sku))}" if sku else f"/search/{q}"),
            "rating": rr.get("averageRating"),
            "reviews": rr.get("totalReviews"),
        })
    return _build_store_items("ripley", "simple.ripley.cl", candidates, ctx, limit=15)


def _scrape_jsonld_store(store_id: str, domain: str, search_url: str, ctx: dict, limit: int) -> list:
    elements = _extract_jsonld_items(_fetch_html(search_url))
    candidates = []
    for el in elements[:36]:
        prod = el.get("item") if isinstance(el, dict) else None
        if not isinstance(prod, dict):
            continue
        brand_obj = prod.get("brand")
        img = prod.get("image")
        offers = prod.get("offers") or {}
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        candidates.append({
            "title": prod.get("name"),
            "brand": brand_obj.get("name") if isinstance(brand_obj, dict) else "",
            "price": offers.get("price") or offers.get("lowPrice"),
            "image": img[0] if isinstance(img, list) and img else (img or ""),
            "url": prod.get("url") or offers.get("url") or search_url,
            "rating": 4.7 if store_id == "paris" else 4.6,
            "reviews": 30,
        })
    return _build_store_items(store_id, domain, candidates, ctx, limit=limit)


def scrape_paris(query: str, ctx: dict) -> list:
    q = urllib.parse.quote(query)
    return _scrape_jsonld_store("paris", "www.paris.cl", f"https://www.paris.cl/search?q={q}", ctx, limit=15)


def scrape_abc(query: str, ctx: dict) -> list:
    q = urllib.parse.quote(query)
    raw_html = _fetch_html(f"https://www.abc.cl/busqueda?q={q}")
    parts = re.split(r'<div[^>]+class="[^"]*product-tile[^"]*"[^>]+data-pid="', raw_html)
    candidates = []
    for chunk in parts[1:36]:
        title, brand, price = "", "", 0
        gtm_m = re.search(r'data-gtm="([^"]*ImpressionsUpdate[^"]*)"', chunk)
        if gtm_m:
            try:
                gtm = json.loads(html_lib.unescape(gtm_m.group(1)))
                imp = gtm.get("ecommerce", {}).get("impressions") or {}
                if isinstance(imp, list) and imp:
                    imp = imp[0]
                title = html_lib.unescape(str(imp.get("name") or "")).strip()
                brand = html_lib.unescape(str(imp.get("brand") or "")).strip()
                price = _parse_clp(imp.get("price"))
            except Exception as exc:
                logger.debug("abc.cl GTM parse skip: %s", exc)
        img_m = re.search(r'<img[^>]+class="[^"]*tile-image[^"]*"[^>]+src="([^"]+)"', chunk) or re.search(
            r'src="(https://www\.abc\.cl/dw/image/[^"]+)"', chunk
        )
        link_m = re.search(r'<a[^>]+class="[^"]*image-link[^"]*"[^>]+href="([^"]+)"', chunk)
        candidates.append({
            "title": title,
            "brand": brand,
            "price": price,
            "image": img_m.group(1) if img_m else "",
            "url": link_m.group(1) if link_m else f"/busqueda?q={q}",
            "rating": 4.6,
            "reviews": 24,
        })
    return _build_store_items("abc", "www.abc.cl", candidates, ctx, limit=15)


def _make_supermarket_scraper(store_id: str, domain: str):
    def _fn(query: str, ctx: dict) -> list:
        iset = set(ctx["interests"])
        if iset and iset.issubset({"moda", "accesorios", "gaming", "herramientas"}):
            return []
        q = urllib.parse.quote(query)
        return _scrape_jsonld_store(store_id, domain, f"https://{domain}/busqueda?ft={q}", ctx, limit=8)
    return _fn


STORE_SCRAPERS = {
    "falabella": scrape_falabella,
    "ripley": scrape_ripley,
    "paris": scrape_paris,
    "abc": scrape_abc,
    "jumbo": _make_supermarket_scraper("jumbo", "www.jumbo.cl"),
    "santaisabel": _make_supermarket_scraper("santaisabel", "www.santaisabel.cl"),
}


# ---------------------------------------------------------------------------
# Orquestación paralela con presupuesto de tiempo global y validación API única
# ---------------------------------------------------------------------------
def _run_batch(query: str, ctx: dict, stores: list, deadline: float):
    batch_items, statuses = [], {}
    unique_stores = list(dict.fromkeys(s for s in stores if isinstance(s, str)))[:12]

    with ThreadPoolExecutor(max_workers=6) as executor:
        tasks = {}
        for sid in unique_stores:
            fn = STORE_SCRAPERS.get(sid)
            if fn:
                tasks[executor.submit(fn, query, ctx)] = sid
            else:
                statuses[sid] = {"ok": True, "liveCount": 0, "mode": "catalog_fallback"}

        remaining_sec = max(1.0, deadline - time.monotonic())
        try:
            for fut in as_completed(tasks, timeout=remaining_sec):
                sid = tasks[fut]
                try:
                    res = fut.result()
                    batch_items.extend(res)
                    statuses[sid] = {"ok": True, "liveCount": len(res), "mode": "live" if res else "catalog_fallback"}
                except Exception as exc:
                    logger.warning("Store %s failed: %s", sid, exc)
                    statuses[sid] = {"ok": False, "liveCount": 0, "mode": "catalog_fallback", "reason": "store_unavailable"}
        except FuturesTimeoutError:
            for fut, sid in tasks.items():
                if sid not in statuses:
                    fut.cancel()
                    statuses[sid] = {"ok": False, "liveCount": 0, "mode": "catalog_fallback", "reason": "timeout"}

    return batch_items, statuses


def scrape_chilean_stores(interests: list, reference: str, budget_min: int, budget_max: int, stores: list, recipient: str = "cualquiera"):
    clean_interests = [str(i)[:30] for i in (interests or [])[:10] if isinstance(i, str)]
    clean_stores = [s for s in (stores or []) if isinstance(s, str)]
    clean_ref = str(reference or "")[:200]
    if budget_min > budget_max:
        budget_min, budget_max = budget_max, budget_min

    deadline = time.monotonic() + GLOBAL_TIME_BUDGET_SEC
    query = build_search_query(clean_interests, clean_ref)
    ctx = {
        "interests": clean_interests,
        "reference": clean_ref,
        "min_price": budget_min,
        "max_price": budget_max,
        "recipient": str(recipient or "cualquiera")[:40],
    }

    all_items, statuses = _run_batch(query, ctx, clean_stores, deadline)

    seen, unique_items = set(), []
    for it in all_items:
        key = (it["storeId"], normalize_text(it["title"]))
        if key not in seen:
            seen.add(key)
            unique_items.append(it)

    complementary_query = None
    if len(unique_items) < 12 and clean_interests and (deadline - time.monotonic()) > 4.0:
        fallback_q = INTEREST_QUERY_MAP.get(clean_interests[0])
        if fallback_q and normalize_text(fallback_q) != normalize_text(query):
            complementary_query = fallback_q
            comp_ctx = {**ctx, "reference": ""}
            comp_stores = [s for s in clean_stores if s in ("falabella", "paris", "abc", "ripley")]
            extra_items, extra_statuses = _run_batch(complementary_query, comp_ctx, comp_stores, deadline)
            for it in extra_items:
                key = (it["storeId"], normalize_text(it["title"]))
                if key not in seen:
                    seen.add(key)
                    unique_items.append(it)
            for sid, st in extra_statuses.items():
                if sid in statuses:
                    statuses[sid]["liveCount"] = statuses[sid].get("liveCount", 0) + st.get("liveCount", 0)
                    if statuses[sid]["liveCount"] > 0:
                        statuses[sid]["mode"] = "live"

    return {
        "query": query,
        "complementaryQuery": complementary_query,
        "items": unique_items,
        "storeStatuses": statuses,
    }


def handle_scrape_payload(data: dict) -> tuple:
    """Validador canónico compartido entre server.py (local) y api/scrape.py (Vercel)."""
    if not isinstance(data, dict):
        return 400, {"error": "Formato inválido.", "items": [], "storeStatuses": {}}

    interests = data.get("interests")
    stores = data.get("stores")
    if interests is not None and not isinstance(interests, list):
        return 400, {"error": "Campo 'interests' debe ser una lista.", "items": [], "storeStatuses": {}}
    if stores is not None and not isinstance(stores, list):
        return 400, {"error": "Campo 'stores' debe ser una lista.", "items": [], "storeStatuses": {}}

    try:
        budget_min = max(0, int(data.get("budgetMin") or 0))
        budget_max = min(5_000_000, max(1000, int(data.get("budgetMax") or 500000)))
    except (TypeError, ValueError):
        return 400, {"error": "Presupuesto inválido.", "items": [], "storeStatuses": {}}

    try:
        result = scrape_chilean_stores(
            interests=interests or [],
            reference=str(data.get("reference") or "")[:200],
            budget_min=budget_min,
            budget_max=budget_max,
            stores=stores or [],
            recipient=str(data.get("recipient") or "cualquiera")[:40],
        )
        return 200, result
    except Exception as exc:
        logger.exception("Scrape payload error: %s", exc)
        return 500, {"error": "No se pudo completar la búsqueda.", "items": [], "storeStatuses": {}}
