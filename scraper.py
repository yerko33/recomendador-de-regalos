"""
Módulo de scraping multi-tienda en vivo para Chile (RegalaCL).
Extrae productos reales, precios CLP, enlaces directos e IMÁGENES REALES
directamente desde las páginas públicas de búsqueda de:
- Falabella (falabella.com)
- Ripley (simple.ripley.cl)
- Paris (paris.cl)
- Jumbo (jumbo.cl)
- Santa Isabel (santaisabel.cl)

Incluye:
- Limpieza de lenguaje natural en español (stopwords + sinónimos de retail)
- Filtro estricto de relevancia sobre el título real del producto
- Exclusión de alimentos/abarrotes no regalables (ej. longanizas, chorizos, aros de cebolla)
- Exclusión estricta de juguetes infantiles cuando el destinatario es adulto
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import html as html_lib
import json
import re
import unicodedata
import urllib.parse
import urllib.request

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/json,*/*;q=0.8",
    "Accept-Language": "es-CL,es;q=0.9",
}

SPANISH_STOPWORDS = {
    "a", "abuela", "abuelo", "al", "algo", "algun", "alguna", "algunos", "ama",
    "amiga", "amigo", "aniversario", "asi", "bien", "bonito", "buen", "buena",
    "bueno", "busca", "buscando", "busco", "casa", "como", "comprar", "con",
    "cosas", "cumple", "cumpleanos", "dar", "de", "del", "dia", "el", "ella",
    "ellos", "en", "encanta", "encantan", "es", "esa", "ese", "eso", "especial",
    "esta", "estar", "este", "fascina", "favorito", "gusta", "gustan",
    "gustaria", "hacer", "hace", "haciendo", "hermana", "hermano", "hija",
    "hijo", "idea", "ideas", "ir", "la", "las", "le", "les", "lindo", "lo",
    "los", "mama", "mas", "me", "mi", "mucho", "muy", "navidad", "necesita",
    "ni", "no", "nos", "o", "papa", "para", "pareja", "pero", "persona", "poco",
    "por", "porque", "practico", "prepara", "preparar", "prima", "primo", "que",
    "quiere", "regalar", "regalo", "regalos", "se", "si", "siempre", "sin",
    "sobre", "sobrina", "sobrino", "solo", "son", "su", "sus", "tambien", "tan",
    "te", "tener", "tia", "tiene", "tio", "tipo", "todo", "todos", "tomar",
    "un", "una", "unas", "uno", "unos", "usar", "usa", "ver", "y",
}

# Frases compuestas frecuentes -> término exacto de tienda
MULTIWORD_SYNONYMS = {
    "cosas dulces": "reposteria moldes hornear",
    "preparar dulces": "reposteria moldes hornear",
    "hacer dulces": "reposteria moldes hornear",
    "hacer postres": "reposteria moldes postres",
    "preparar postres": "reposteria moldes postres",
    "hacer galletas": "reposteria moldes galletas",
    "hacer tortas": "reposteria moldes torta",
    "hacer queques": "reposteria molde queque",
    "tomar cafe": "cafetera prensa francesa",
    "tomar vino": "vino copas sacacorchos",
    "escuchar musica": "audifonos parlante bluetooth",
    "cuidar piel": "skincare crema facial",
    "hacer deporte": "fitness botella termica",
    "hacer ejercicio": "fitness mancuernas yoga",
    "hacer asados": "set parrillero accesorios",
}

# Normaliza verbos y expresiones naturales a términos reales que indexan las tiendas
PHRASE_SYNONYMS = {
    "maquillarse": "set maquillaje",
    "maquillar": "set maquillaje",
    "maquillaje": "set maquillaje",
    "makeup": "maquillaje",
    "pintarse": "maquillaje labial",
    "aros": "aros joya",
    "aretes": "aros joya",
    "dulces": "reposteria moldes hornear",
    "dulce": "reposteria moldes hornear",
    "postres": "reposteria moldes postres",
    "postre": "reposteria moldes postres",
    "galletas": "reposteria moldes galletas",
    "tortas": "reposteria moldes torta",
    "torta": "reposteria moldes torta",
    "queques": "reposteria molde queque",
    "cupcakes": "reposteria molde cupcake",
    "pasteleria": "set reposteria pasteleria",
    "reposteria": "set reposteria moldes",
    "hornear": "reposteria moldes hornear",
    "parrilladas": "set parrillero accesorios asado",
    "parrillada": "set parrillero accesorios asado",
    "parrilla": "set parrillero",
    "asados": "set parrillero",
    "asado": "set parrillero",
    "cocinar": "utensilios cocina",
    "correr": "running deportivo",
    "trotar": "running deportivo",
    "entrenar": "fitness mancuernas",
    "ejercitar": "fitness entrenamiento",
    "ejercicios": "fitness entrenamiento",
    "acampar": "camping outdoor",
    "senderismo": "trekking mochila",
    "leer": "libro novela",
    "leyendo": "libro novela",
    "jugar": "gamer juegos",
    "videojuegos": "audifonos gamer videojuegos",
    "escuchar": "audifonos parlante",
    "relajarse": "spa masajeador aroma",
    "descansar": "masajeador manta",
    "perfumarse": "perfume",
    "pintar": "pintura acrilica pinceles",
    "dibujar": "dibujo lapices colores",
    "cafetero": "cafetera",
    "barista": "cafetera espresso",
    "tejer": "set crochet palillos lana",
    "coser": "set costura",
    "plantas": "macetero herramientas jardineria",
    "jardineria": "set herramientas jardineria",
}

INTEREST_QUERY_MAP = {
    "tecnologia": "audifonos bluetooth",
    "gaming": "mouse teclado gamer",
    "cocina": "set cocina freidora",
    "cafe_vinos": "cafetera vino copas",
    "deporte": "botella termica trekking",
    "belleza": "set maquillaje skincare",
    "hogar": "difusor aromas decoracion",
    "musica": "parlante audifonos bluetooth",
    "lectura": "libreta moleskine libros",
    "moda": "aros billetera cartera",
    "bienestar": "masajeador spa velas",
    "mascotas": "cama juguete perro gato",
    "juguetes": "juguete didactico lego",
    "bebes": "juguete bebe sonajero manta",
}

INTEREST_VOCAB = {
    "tecnologia": [
        "audifon", "bluet", "smartwatch", "reloj", "cargador", "powerbank",
        "bateria", "parlant", "tablet", "kindle", "usb", "inalambr", "smart",
        "camara", "proyector", "teclado", "mouse", "alexa", "echo", "airpods",
        "sony", "xiaomi", "anker", "jbl", "samsung", "apple", "huawei", "gamer",
    ],
    "gaming": [
        "gamer", "gaming", "mouse", "teclado", "rgb", "headset", "audifon",
        "control", "joystick", "mando", "playstation", "nintendo", "xbox",
        "silla", "pad", "microfono", "logitech", "razer", "hyperx", "redragon",
        "catan", "juego", "lego", "videojuego",
    ],
    "cocina": [
        "cocin", "freidor", "airfryer", "olla", "sarten", "cuchill", "batidor",
        "licuador", "horno", "hornear", "tostador", "waffler", "tabla",
        "parriller", "parrilla", "asado", "delantal", "tenaza", "pinza",
        "termometro", "molde", "reposter", "pasteler", "manga pastel",
        "boquilla", "cortador", "galleta", "cupcake", "muffin", "queque",
        "torta", "uslero", "rodillo", "espatula", "bascu", "pesa cocina",
        "bowl", "bandeja horno", "silicona", "espumador", "hervidor",
        "procesador", "oster", "tefal", "kitchen", "utensilio",
    ],
    "cafe_vinos": [
        "cafe", "cafeter", "espresso", "moka", "bialetti", "nespresso", "prensa",
        "molinillo", "taza", "termo", "vino", "copa", "sacacorcho", "decantador",
        "espumante", "whisky", "gin", "coctel", "bar", "infusor", "mate",
    ],
    "deporte": [
        "deport", "trekking", "outdoor", "camping", "botella", "termo", "stanley",
        "mochila", "yoga", "mat", "mancuerna", "pesa", "running", "biciclet",
        "fitness", "entren", "banda elast", "cuerda", "zapatilla", "lippi",
        "doite", "columbia", "nike", "adidas", "puma",
    ],
    "belleza": [
        "maquill", "labial", "gloss", "sombra", "paleta", "rubor", "blush",
        "delineador", "rimel", "mascara", "brocha", "esponja", "base ", "polvo",
        "corrector", "iluminador", "primer", "fijador", "cosmetic", "makeup",
        "skincare", "serum", "crema", "hidrat", "facial", "rostro", "contorno",
        "protector solar", "bloqueador", "perfume", "eau de", "fragancia",
        "colonia", "body mist", "exfoliant", "limpiador", "micelar", "tonico",
        "esmalte", "unas", "secador", "alisador", "ondulador", "cepill",
        "espejo", "neceser", "cosmetiquero", "estuche", "belleza", "cuidado",
        "maybelline", "loreal", "revlon", "nyx", "mac", "clinique", "lancome",
        "dior", "cerave", "roche", "eucerin", "vichy", "nivea", "natura",
        "essence", "catrice", "petrizzio", "dbs", "etienne", "garnier",
    ],
    "hogar": [
        "hogar", "deco", "manta", "cojin", "lampara", "vela", "difusor", "aroma",
        "florero", "cuadro", "espejo", "organizador", "canasto", "alfombra",
        "taza", "vaso", "plato", "bandeja", "tabla", "macetero", "planta",
        "sabana", "plumon", "toalla", "bano", "casa",
    ],
    "musica": [
        "audifon", "parlant", "speaker", "vinilo", "tornamesa", "tocadisco",
        "guitarra", "ukelele", "microfono", "audio", "bluet", "jbl", "sony",
        "marshall", "bose", "soundcore", "musica",
    ],
    "lectura": [
        "libro", "novela", "kindle", "lector", "libreta", "cuaderno", "agenda",
        "moleskine", "lapiz", "pluma", "marcador", "papeleria", "pintura",
        "acrilic", "acuarela", "pincel", "dibujo", "arte", "comic", "manga",
    ],
    "moda": [
        "aro", "aros", "arete", "pendiente", "argolla", "collar", "colgante",
        "pulsera", "anillo", "joya", "plata 925", "bano de oro", "oro ",
        "billetera", "cartera", "bolso", "mochila", "bandolera", "lente", "gafa",
        "reloj", "bufanda", "panuelo", "cinturon", "gorro", "chaqueta",
        "poleron", "zapatilla", "cuero", "accesorio",
    ],
    "bienestar": [
        "masaje", "spa", "vela", "difusor", "aroma", "aceite esencial", "sales",
        "bano", "yoga", "meditacion", "relax", "almohada", "manta", "guatero",
        "infusion", "humidificador", "cuidado", "crema",
    ],
    "mascotas": [
        "perro", "gato", "mascota", "cama ", "rascador", "juguete", "correa",
        "arnes", "collar", "comedero", "bebedero", "transportador", "snack",
    ],
    "juguetes": [
        "juguete", "lego", "muneca", "barbie", "hot wheels", "playset",
        "rompecabeza", "puzzle", "peluche", "didactico", "auto ", "pistola nerf",
        "nerf", "figura", "juego de mesa", "bloques", "infantil", "nino", "nina",
    ],
    "bebes": [
        "bebe", "sonajero", "mordedor", "estimulacion", "manta", "peluche",
        "gimnasio bebe", "movil cuna", "babero", "chupete", "mamadera",
        "pijama", "body ", "osito", "didactico", "fisher price", "chicco",
    ],
}

# Alimentos/carnes/abarrotes que NO son regalos (evita longanizas, chorizos, aros de cebolla, cereales, etc.)
NON_GIFTABLE_FOOD_PATTERNS = [
    r"\blonganiza\w*\b", r"\bchorizo\w*\b", r"\bchoricillo\w*\b", r"\bprieta\w*\b",
    r"\bsalchicha\w*\b", r"\bvienesa\w*\b", r"\bhamburguesa\w*\b", r"\btocino\b",
    r"\bcarne\b", r"\bvacuno\b", r"\bcerdo\b", r"\bpollo\b", r"\bpavo\b",
    r"\bcostillar\b", r"\blomo\b", r"\bentrana\b", r"\bsobrecostilla\b",
    r"\bhuachalomo\b", r"\babastero\b", r"\bplateada\b", r"\bchuleta\w*\b",
    r"\btrutro\w*\b", r"\bpechuga\w*\b", r"\bpescado\b", r"\bsalmon\b", r"\batun\b",
    r"\bcebolla\w*\b", r"\bcalamar\w*\b", r"\bcamaron\w*\b", r"\bmarisco\w*\b",
    r"\bcereal\w*\b", r"\barroz\b", r"\bfideo\w*\b", r"\btallarin\w*\b",
    r"\bharina\b", r"\bazucar\b", r"\bleche\b", r"\byogurt\b", r"\bqueso\b",
    r"\bjamon\b", r"\bmantequilla\b", r"\bmargarina\b", r"\baceite\b",
    r"\bsalsa\b", r"\bketchup\b", r"\bmayonesa\b", r"\bmostaza\b", r"\bpebre\b",
    r"\bchimichurri\b", r"\balino\b", r"\bcondimento\b", r"\bsazonador\b",
    r"\bpapa\w* frita\w*\b", r"\bsnack\b", r"\bmanjar\b", r"\bmermelada\b",
    r"\bgalleta\w*\b", r"\bpan \b", r"\bmarraqueta\b", r"\bhallulla\b",
    r"\bhelado\w*\b", r"\bjugo\b", r"\bbebida\b", r"\bcarbon\b", r"\bbriqueta\w*\b",
    r"\bdetergente\b", r"\blavalozas\b", r"\bcloro\b", r"\bpapel higienico\b",
    r"\bservilleta\w*\b", r"\bcongelado\w*\b", r"\bprefrito\w*\b",
    r"\bsuper cerdo\b", r"\bpf \b", r"\bllanquihue\b", r"\bsan jorge\b",
    r"\breceta del abuelo\b", r"\barian\b", r"\bsoproval\b",
]
NON_GIFTABLE_FOOD_RE = re.compile("|".join(NON_GIFTABLE_FOOD_PATTERNS))

# Productos infantiles/juguetes a excluir cuando el destinatario es adulto
KIDS_PRODUCT_RE = re.compile(
    r"\b(infantil\w*|nina\w*|nino\w*|juguete\w*|barbie|princesa\w*|frozen|"
    r"minnie|unicornio|sorpresa\w*|expendedora|muneca\w*|playset|kids|"
    r"giotto|tempera|plastilina|disfraz|didactico\w*|sonajero\w*|bebe\w*|"
    r"hello kitty|disney|stitch|my little pony|maleta make)\b"
)

CHILD_RECIPIENTS = {"bebe_0_2", "nino_3_4", "nino", "adolescente"}


def normalize_text(text: str) -> str:
    s = unicodedata.normalize("NFD", (text or "").lower())
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def extract_clean_terms(reference: str) -> list:
    norm = normalize_text(reference)
    if not norm:
        return []
    # 1. Detectar frases compuestas primero (ej. "cosas dulces" -> "reposteria moldes hornear")
    for phrase, replacement in MULTIWORD_SYNONYMS.items():
        if phrase in norm:
            norm = norm.replace(phrase, replacement)

    raw_tokens = norm.split()
    cleaned = []
    for tok in raw_tokens:
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
        first = interests[0]
        return INTEREST_QUERY_MAP.get(first, "regalo")
    return "regalo"


def _is_blocked_product(norm_title: str, interests: list, recipient: str, store_id: str) -> bool:
    """Descarta alimentos/carnes no regalables y juguetes infantiles para destinatarios adultos."""
    interest_set = set(interests or [])

    # 1. Bloquear carnes, embutidos, abarrotes y alimentos no regalables
    # (Se permiten chocolates/café/vino/té solo si el interés es cafe_vinos)
    if NON_GIFTABLE_FOOD_RE.search(norm_title):
        return True

    # En supermercados (Jumbo / Santa Isabel), si la búsqueda es moda/joyas/tecnología/gaming/belleza,
    # bloquear productos con unidades de peso alimentario típicos (ej. "250 g", "500 g", "1 kg")
    if store_id in ("jumbo", "santaisabel") and not (interest_set & {"cafe_vinos", "mascotas"}):
        if re.search(r"\b\d+\s*(kg|g|gr|grs|ml|cc|lt|l)\b", norm_title) and not (interest_set & {"belleza", "bienestar"}):
            return True

    # 2. Bloquear artículos de limpieza / no regalos y libros cuando no se pidió lectura
    if re.search(r"\b(limpiador|escobilla|escobillon|trapero|dispensador de dulces|alcancia)\b", norm_title):
        return True
    if "lectura" not in interest_set and re.search(r"\b(libro|novela|cuento|enciclopedia)\b", norm_title):
        return True

    # 3. Bloquear juguetes infantiles cuando el destinatario NO es niño/bebé/adolescente
    # y el usuario NO pidió explícitamente intereses de juguetes o bebés
    is_adult_recipient = recipient not in CHILD_RECIPIENTS and recipient != "cualquiera"
    wants_kids_stuff = bool(interest_set & {"juguetes", "bebes"})
    if is_adult_recipient and not wants_kids_stuff:
        if KIDS_PRODUCT_RE.search(norm_title):
            return True

    return False


def is_product_relevant(title: str, interests: list, reference: str, recipient: str = "cualquiera", store_id: str = "") -> tuple:
    """
    Valida que el título del producto corresponda a las referencias/intereses
    y NO sea un alimento no regalable ni un juguete infantil fuera de contexto.
    """
    norm_title = normalize_text(title)

    if _is_blocked_product(norm_title, interests, recipient, store_id):
        return False, []

    ref_terms = extract_clean_terms(reference)
    interest_stems = []
    for intr in (interests or []):
        interest_stems.extend(INTEREST_VOCAB.get(intr, []))

    matched_ref = []
    for tok in ref_terms:
        stem = tok[:6] if len(tok) >= 6 else tok
        if stem in norm_title:
            matched_ref.append(tok)

    matched_int = []
    for stem in interest_stems:
        if stem in norm_title:
            matched_int.append(stem.strip())

    # Si el usuario escribió una referencia específica (ej. "aros de oro y plata", "parrilladas"),
    # exigir que coincida con la referencia O con un término fuerte del interés.
    # Además, para "aros" exigir que sea joyería/moda y no comida (ya filtrado arriba).
    if not ref_terms and not interest_stems:
        return True, []

    matched = list(dict.fromkeys(matched_ref + matched_int))
    return len(matched) > 0, matched[:4]


def _fetch_html(url: str, timeout: float = 8.0) -> str:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="ignore")


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


def _ensure_https(url: str) -> str:
    if not url:
        return ""
    if url.startswith("//"):
        return "https:" + url
    return url.replace("http://", "https://")


def _price_acceptable(price: int, min_price: int, max_price: int) -> bool:
    if price <= 0:
        return False
    if min_price and price < int(min_price * 0.65):
        return False
    if max_price and price > int(max_price * 1.25):
        return False
    return True


def _detect_interests_from_title(title: str, requested_interests: list) -> list:
    norm_title = normalize_text(title)
    detected = []
    for intr, vocab in INTEREST_VOCAB.items():
        if any(stem in norm_title for stem in vocab):
            detected.append(intr)
    if not detected and requested_interests:
        detected = requested_interests[:1]
    return detected[:3]


def scrape_falabella(query: str, min_price: int, max_price: int, interests: list, reference: str, recipient: str):
    q = urllib.parse.quote(query)
    html = _fetch_html(f"https://www.falabella.com/falabella-cl/search?Ntt={q}")
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        return []
    nd = json.loads(m.group(1))
    results = nd.get("props", {}).get("pageProps", {}).get("results") or []
    items = []
    for r in results[:36]:
        title = (r.get("displayName") or "").strip()
        brand = (r.get("brand") or "").strip()
        full_title = f"{brand} {title}".strip() if (brand and brand.lower() not in title.lower()) else title
        media = r.get("mediaUrls") or []
        img = _ensure_https(media[0]) if media else ""
        if not title or not img:
            continue

        relevant, matched_terms = is_product_relevant(full_title, interests, reference, recipient, "falabella")
        if not relevant:
            continue

        price = 0
        old_price = None
        for p in r.get("prices") or []:
            ptype = p.get("type", "")
            pval = _parse_clp(p.get("price"))
            if not pval:
                continue
            if p.get("crossed"):
                old_price = pval
            elif ptype in ("eventPrice", "internetPrice") or price == 0:
                price = pval

        if not _price_acceptable(price, min_price, max_price):
            continue

        rating = float(r.get("rating") or 4.7)
        if rating <= 0 or rating > 5:
            rating = 4.7

        items.append({
            "id": _make_id("falabella", title, r.get("url", "")),
            "title": full_title,
            "storeId": "falabella",
            "price": price,
            "oldPrice": old_price if (old_price and old_price > price) else None,
            "rating": rating,
            "reviews": int(r.get("totalReviews") or 25),
            "image": img,
            "bg": "#ffffff",
            "url": r.get("url") or f"https://www.falabella.com/falabella-cl/search?Ntt={q}",
            "interests": _detect_interests_from_title(full_title, interests),
            "matchedTerms": matched_terms,
            "keywords": normalize_text(full_title).split(),
        })
        if len(items) >= 15:
            break
    return items


def scrape_ripley(query: str, min_price: int, max_price: int, interests: list, reference: str, recipient: str):
    q = urllib.parse.quote(query)
    html = _fetch_html(f"https://simple.ripley.cl/search/{q}")
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        return []
    nd = json.loads(m.group(1))
    prods = (
        nd.get("props", {})
        .get("pageProps", {})
        .get("findabilityProps", {})
        .get("data", {})
        .get("products")
        or []
    )
    items = []
    for r in prods[:36]:
        title = (r.get("name") or "").strip()
        brand = (r.get("brand") or "").strip()
        if title.isupper():
            title = title.title()
        full_title = f"{brand} {title}".strip() if (brand and brand.lower() not in title.lower()) else title
        imgs = r.get("images") or []
        img = _ensure_https(r.get("primaryImage") or (imgs[0] if imgs else ""))
        if not title or not img:
            continue

        relevant, matched_terms = is_product_relevant(full_title, interests, reference, recipient, "ripley")
        if not relevant:
            continue

        price = _parse_clp(r.get("priceNumber") or r.get("price") or r.get("ripleyPriceNumber"))
        old_price = _parse_clp(r.get("masterPriceNumber") or r.get("oldPrice"))
        if not _price_acceptable(price, min_price, max_price):
            continue

        sku = r.get("sku") or r.get("code") or ""
        prod_url = r.get("url") or (f"https://simple.ripley.cl/search/{urllib.parse.quote(sku)}" if sku else f"https://simple.ripley.cl/search/{q}")
        if prod_url.startswith("/"):
            prod_url = "https://simple.ripley.cl" + prod_url

        rr = r.get("rating_reviews") or {}
        rating = float(rr.get("averageRating") or 4.7) if isinstance(rr, dict) else 4.7
        if rating <= 0 or rating > 5:
            rating = 4.7

        items.append({
            "id": _make_id("ripley", title, prod_url),
            "title": full_title,
            "storeId": "ripley",
            "price": price,
            "oldPrice": old_price if (old_price and old_price > price) else None,
            "rating": rating,
            "reviews": int(rr.get("totalReviews") or 20) if isinstance(rr, dict) else 20,
            "image": img,
            "bg": "#ffffff",
            "url": prod_url,
            "interests": _detect_interests_from_title(full_title, interests),
            "matchedTerms": matched_terms,
            "keywords": normalize_text(full_title).split(),
        })
        if len(items) >= 15:
            break
    return items


def scrape_paris(query: str, min_price: int, max_price: int, interests: list, reference: str, recipient: str):
    q = urllib.parse.quote(query)
    html = _fetch_html(f"https://www.paris.cl/search?q={q}")
    blocks = re.findall(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.S)
    elements = []
    for b in blocks:
        try:
            d = json.loads(b.strip())
        except Exception:
            continue
        if isinstance(d, dict):
            me = d.get("mainEntity")
            if isinstance(me, dict) and isinstance(me.get("itemListElement"), list):
                elements = me["itemListElement"]
                break
            if isinstance(d.get("itemListElement"), list):
                elements = d["itemListElement"]
                break

    items = []
    for el in elements[:36]:
        prod = el.get("item") if isinstance(el, dict) else None
        if not isinstance(prod, dict):
            continue
        title = (prod.get("name") or "").strip()
        brand_obj = prod.get("brand")
        brand = brand_obj.get("name", "").strip() if isinstance(brand_obj, dict) else ""
        full_title = f"{brand} {title}".strip() if (brand and brand.lower() not in title.lower()) else title
        img = prod.get("image")
        if isinstance(img, list):
            img = img[0] if img else ""
        img = _ensure_https(img or "")
        if not title or not img:
            continue

        relevant, matched_terms = is_product_relevant(full_title, interests, reference, recipient, "paris")
        if not relevant:
            continue

        offers = prod.get("offers") or {}
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        price = _parse_clp(offers.get("price") or offers.get("lowPrice"))
        if not _price_acceptable(price, min_price, max_price):
            continue

        prod_url = prod.get("url") or offers.get("url") or f"https://www.paris.cl/search?q={q}"

        items.append({
            "id": _make_id("paris", title, prod_url),
            "title": full_title,
            "storeId": "paris",
            "price": price,
            "oldPrice": None,
            "rating": 4.7,
            "reviews": 35,
            "image": img,
            "bg": "#ffffff",
            "url": prod_url,
            "interests": _detect_interests_from_title(full_title, interests),
            "matchedTerms": matched_terms,
            "keywords": normalize_text(full_title).split(),
        })
        if len(items) >= 15:
            break
    return items


def scrape_abc(query: str, min_price: int, max_price: int, interests: list, reference: str, recipient: str):
    """Extrae productos e imágenes reales desde abc.cl (fusión de Abcdin y La Polar)."""
    q = urllib.parse.quote(query)
    raw_html = _fetch_html(f"https://www.abc.cl/busqueda?q={q}")
    parts = re.split(r'<div[^>]+class="[^"]*product-tile[^"]*"[^>]+data-pid="', raw_html)
    items = []
    for chunk in parts[1:36]:
        # Extraer JSON de data-gtm (ImpressionsUpdate)
        gtm_match = re.search(r'data-gtm="([^"]*ImpressionsUpdate[^"]*)"', chunk)
        title = ""
        brand = ""
        price = 0
        if gtm_match:
            try:
                gtm = json.loads(html_lib.unescape(gtm_match.group(1)))
                imp = gtm.get("ecommerce", {}).get("impressions") or {}
                if isinstance(imp, list) and imp:
                    imp = imp[0]
                title = html_lib.unescape(str(imp.get("name") or "")).strip()
                brand = html_lib.unescape(str(imp.get("brand") or "")).strip()
                price = _parse_clp(imp.get("price"))
            except Exception:
                pass

        img_match = re.search(r'<img[^>]+class="[^"]*tile-image[^"]*"[^>]+src="([^"]+)"', chunk)
        if not img_match:
            img_match = re.search(r'src="(https://www\.abc\.cl/dw/image/[^"]+)"', chunk)
        img = _ensure_https(html_lib.unescape(img_match.group(1))) if img_match else ""

        link_match = re.search(r'<a[^>]+class="[^"]*image-link[^"]*"[^>]+href="([^"]+)"', chunk)
        prod_url = html_lib.unescape(link_match.group(1)) if link_match else f"/busqueda?q={q}"
        if prod_url.startswith("/"):
            prod_url = "https://www.abc.cl" + prod_url

        if not title or not img:
            continue

        full_title = f"{brand} {title}".strip() if (brand and brand.lower() not in title.lower()) else title
        relevant, matched_terms = is_product_relevant(full_title, interests, reference, recipient, "abc")
        if not relevant:
            continue
        if not _price_acceptable(price, min_price, max_price):
            continue

        items.append({
            "id": _make_id("abc", full_title, prod_url),
            "title": full_title,
            "storeId": "abc",
            "price": price,
            "oldPrice": None,
            "rating": 4.6,
            "reviews": 24,
            "image": img,
            "bg": "#ffffff",
            "url": prod_url,
            "interests": _detect_interests_from_title(full_title, interests),
            "matchedTerms": matched_terms,
            "keywords": normalize_text(full_title).split(),
        })
        if len(items) >= 15:
            break
    return items


def scrape_cencosud_supermarket(store_id: str, domain: str, query: str, min_price: int, max_price: int, interests: list, reference: str, recipient: str):
    # Supermercados (Jumbo/Santa Isabel) no son relevantes para joyería/moda/gaming puro
    interest_set = set(interests or [])
    if interest_set and interest_set.issubset({"moda", "gaming"}):
        return []

    q = urllib.parse.quote(query)
    html = _fetch_html(f"https://{domain}/busqueda?ft={q}")
    blocks = re.findall(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.S)
    elements = []
    for b in blocks:
        try:
            d = json.loads(b.strip())
        except Exception:
            continue
        if isinstance(d, dict) and isinstance(d.get("itemListElement"), list):
            elements = d["itemListElement"]
            break

    items = []
    for el in elements[:24]:
        prod = el.get("item") if isinstance(el, dict) else None
        if not isinstance(prod, dict):
            continue
        title = (prod.get("name") or "").strip()
        img = prod.get("image")
        if isinstance(img, list):
            img = img[0] if img else ""
        img = _ensure_https(img or "")
        if not title or not img:
            continue

        relevant, matched_terms = is_product_relevant(title, interests, reference, recipient, store_id)
        if not relevant:
            continue

        offers = prod.get("offers") or {}
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        price = _parse_clp(offers.get("price") or offers.get("lowPrice"))
        if not _price_acceptable(price, min_price, max_price):
            continue

        prod_url = prod.get("url") or offers.get("url") or f"https://{domain}/busqueda?ft={q}"

        items.append({
            "id": _make_id(store_id, title, prod_url),
            "title": title,
            "storeId": store_id,
            "price": price,
            "oldPrice": None,
            "rating": 4.6,
            "reviews": 28,
            "image": img,
            "bg": "#ffffff",
            "url": prod_url,
            "interests": _detect_interests_from_title(title, interests),
            "matchedTerms": matched_terms,
            "keywords": normalize_text(title).split(),
        })
        if len(items) >= 8:
            break
    return items


def _run_batch(query: str, budget_min: int, budget_max: int, interests: list, reference: str, stores: list, recipient: str):
    batch_items = []
    statuses = {}
    tasks = {}
    with ThreadPoolExecutor(max_workers=7) as executor:
        for sid in stores:
            if sid == "falabella":
                tasks[executor.submit(scrape_falabella, query, budget_min, budget_max, interests, reference, recipient)] = sid
            elif sid == "ripley":
                tasks[executor.submit(scrape_ripley, query, budget_min, budget_max, interests, reference, recipient)] = sid
            elif sid == "paris":
                tasks[executor.submit(scrape_paris, query, budget_min, budget_max, interests, reference, recipient)] = sid
            elif sid == "abc":
                tasks[executor.submit(scrape_abc, query, budget_min, budget_max, interests, reference, recipient)] = sid
            elif sid == "jumbo":
                tasks[executor.submit(scrape_cencosud_supermarket, "jumbo", "www.jumbo.cl", query, budget_min, budget_max, interests, reference, recipient)] = sid
            elif sid == "santaisabel":
                tasks[executor.submit(scrape_cencosud_supermarket, "santaisabel", "www.santaisabel.cl", query, budget_min, budget_max, interests, reference, recipient)] = sid
            else:
                statuses[sid] = {"ok": True, "liveCount": 0, "mode": "catalog_fallback"}

        for fut in as_completed(tasks):
            sid = tasks[fut]
            try:
                res = fut.result()
                batch_items.extend(res)
                statuses[sid] = {"ok": True, "liveCount": len(res), "mode": "live" if res else "catalog_fallback"}
            except Exception as exc:
                statuses[sid] = {"ok": False, "liveCount": 0, "mode": "catalog_fallback", "reason": str(exc)[:90]}
    return batch_items, statuses


def scrape_chilean_stores(interests: list, reference: str, budget_min: int, budget_max: int, stores: list, recipient: str = "cualquiera"):
    """
    Ejecuta scraping real en paralelo en Falabella, Ripley, Paris, Abcdin/La Polar (abc),
    Jumbo y Santa Isabel. Si la búsqueda principal devuelve menos de 12 productos válidos,
    ejecuta automáticamente una segunda búsqueda complementaria por interés principal.
    """
    query = build_search_query(interests, reference)
    all_items, statuses = _run_batch(query, budget_min, budget_max, interests, reference, stores, recipient)

    # Deduplicar resultados iniciales
    seen = set()
    unique_items = []
    for it in all_items:
        key = (it["storeId"], normalize_text(it["title"]))
        if key not in seen:
            seen.add(key)
            unique_items.append(it)

    # Segunda búsqueda complementaria automática si salieron menos de 12 resultados
    complementary_query = None
    if len(unique_items) < 12 and interests:
        fallback_q = INTEREST_QUERY_MAP.get(interests[0])
        if fallback_q and normalize_text(fallback_q) != normalize_text(query):
            complementary_query = fallback_q
            # Usar las tiendas más rápidas y confiables para la búsqueda complementaria
            comp_stores = [s for s in stores if s in ("falabella", "paris", "abc", "ripley")]
            extra_items, extra_statuses = _run_batch(
                complementary_query, budget_min, budget_max, interests, "", comp_stores, recipient
            )
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
