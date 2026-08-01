"""
Scraper de canal público de Telegram -> ofertas.json

Estrategia en dos pasos:
1) Lee la vista general del canal (t.me/s/<canal>) solo para sacar la lista
   de IDs de los posts recientes.
2) Para cada ID, visita la página de ESE post individual (t.me/<canal>/<id>)
   y lee su etiqueta og:description / og:image. Esa etiqueta siempre trae el
   texto completo y fiable del mensaje, incluso en casos donde la vista
   general del canal no lo expone bien (p.ej. posts que forman parte de un
   álbum de varias fotos).

Uso:
    python scraper.py --channel ofertasychollosesp --out ofertas.json
"""

import argparse
import json
import os
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

INDEX_URL = "https://t.me/s/{channel}"
POST_URL = "https://t.me/{channel}/{post_id}"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}

# Mapea el dominio del enlace a un nombre de marketplace legible.
# Mapea un fragmento del dominio (sin depender del TLD exacto) a un nombre
# de marketplace legible. Usar el fragmento de marca en vez del dominio
# completo evita duplicados como "Aliexpress" / "AliExpress" cuando el
# enlace usa un TLD distinto (aliexpress.com vs aliexpress.us, etc.).
MARKETPLACE_DOMAINS = {
    "amazon": "Amazon",
    "amzn": "Amazon",
    # OJO: amz.tf NO es un dominio oficial de Amazon, es un acortador de
    # enlaces genérico (amz.tf) usado como marca propia — puede apuntar a
    # cualquier tienda. "amzn" no coincide con "amz.tf" (le falta la "n"),
    # así que el scraper se ve obligado a seguir la redirección real en vez
    # de asumir que es Amazon.
    "aliexpress": "AliExpress",
    "pccomponentes": "PcComponentes",
    "elcorteingles": "El Corte Inglés",
    "mediamarkt": "MediaMarkt",
    "carrefour": "Carrefour",
    "decathlon": "Decathlon",
    "zalando": "Zalando",
    "fnac": "Fnac",
    "worten": "Worten",
    "leroymerlin": "Leroy Merlin",
    "ebay": "eBay",
    "shein": "Shein",
    "ikea": "Ikea",
    "primor": "Primor",
    "druni": "Druni",
    "lidl": "Lidl",
    "aldi": "Aldi",
}

# Categorías por palabras clave, usadas si el post no lleva un hashtag que
# coincida con el nombre de una categoría. Cuantas más palabras por categoría,
# mejor clasifica — amplía esta lista según lo que sueles publicar.
CATEGORY_KEYWORDS = {
    "Tecnología": [
        "móvil", "movil", "smartphone", "iphone", "samsung galaxy", "xiaomi", "huawei",
        "portátil", "portatil", "ordenador", "laptop", "netbook", "tablet", "ipad",
        "monitor", "ssd", "disco duro", "pendrive", "usb", "cargador", "power bank",
        "batería externa", "bateria externa", "cámara", "camara", "camara de fotos",
        "gopro", "dron", "drone", "smartwatch", "reloj inteligente", "pulsera actividad",
        "impresora", "escáner", "escaner", "router", "repetidor wifi", "teclado",
        "ratón", "raton inalámbrico", "webcam", "proyector", "disco ssd", "adaptador usb",
        "hub usb", "cable hdmi", "power delivery",
    ],
    "Televisores y sonido": [
        "smart tv", "televisor", "televisión", "television", "pulgadas", "4k", "uhd",
        "dolby", "android tv", "google tv", "chromecast", "fire tv", "hdr", "oled", "qled",
    ],
    "Auriculares y sonido": [
        "auricular", "cascos", "altavoz", "barra de sonido", "bluetooth", "airpods",
        "altavoces", "soundbar", "earbuds", "walkie talkie", "micrófono", "microfono",
    ],
    "Iluminación": [
        "plafón", "plafon", "luz led", "tira led", "foco led", "bombilla", "lámpara led",
        "lampara led", "farolillo", "guirnalda luces", "linterna", "foco solar", "aplique pared",
    ],
    "Hogar": [
        "sofá", "sofa", "sábana", "sabana", "colchón", "colchon", "lámpara", "lampara",
        "cortina", "alfombra", "organizador", "estantería", "estanteria", "espejo",
        "decoración", "decoracion", "funda nórdica", "almohada", "manta", "cojín", "cojin",
        "percha", "caja almacenaje", "reloj de pared", "vela aromática", "vela aromatica",
        "ambientador", "difusor aromas", "toallas", "juego de sábanas",
    ],
    "Electrodomésticos": [
        "aspirador", "robot aspirador", "sartén", "sarten", "cafetera", "batidora",
        "freidora", "airfryer", "microondas", "lavadora", "secadora", "nevera",
        "frigorífico", "frigorifico", "plancha", "secador", "tostadora", "olla",
        "vaporeta", "robot de cocina", "picadora", "exprimidor", "báscula de cocina",
        "bascula de cocina", "termo eléctrico", "calefactor", "ventilador", "purificador de aire",
        "deshumidificador", "humidificador", "aire acondicionado",
    ],
    "Droguería y limpieza": [
        "detergente", "suavizante", "papel higiénico", "papel higienico", "papel de cocina",
        "papel horno", "colgate", "pasta de dientes", "dentífrico", "dentifrico",
        "friegasuelos", "lejía", "lejia", "estropajo", "gel de ducha", "jabón", "jabon",
        "desinfectante", "quitamanchas", "bolsas de basura", "pastillas lavavajillas",
        "cápsulas de detergente", "capsulas de detergente",
    ],
    "Moda": [
        "zapatilla", "camiseta", "pantalón", "pantalon", "chaqueta", "vestido",
        "sudadera", "abrigo", "bolso", "mochila", "cinturón", "cinturon", "calcetines",
        "calzoncillos", "sujetador", "bufanda", "gafas de sol", "polo", "camisa",
        "falda", "traje de baño", "bañador", "leggins", "jersey", "pijama", "botas",
        "sandalias", "reloj de pulsera", "cartera", "monedero", "guantes",
    ],
    "Deporte": [
        "bicicleta", "running", "gimnasio", "pesa", "mancuerna", "fitness", "patinete",
        "esterilla", "yoga", "pack de proteína", "proteina", "casco ciclismo", "natación",
        "natacion", "banda elástica", "banda elastica", "comba", "guantes de boxeo",
        "raqueta", "pelota", "balón", "balon", "cinta de correr", "bicicleta estática",
        "bicicleta estatica", "mancuernas ajustables",
    ],
    "Juguetes": [
        "juguete", "lego", "muñeca", "muneca", "puzzle", "peluche", "playmobil",
        "coche teledirigido", "figura de acción", "figura de accion", "juego de mesa",
        "playset", "pista de coches",
    ],
    "Belleza y cuidado personal": [
        "maquillaje", "perfume", "colonia", "crema facial", "champú", "champu",
        "cepillo eléctrico", "cepillo electrico", "depiladora", "afeitadora", "sérum", "serum",
        "mascarilla facial", "protector solar", "crema hidratante", "esmalte de uñas",
        "esmalte de unas", "plancha de pelo", "rizador de pelo", "cortapelos", "manicura",
        "kit de afeitado", "depilatoria", "depilatorio", "depilación", "depilacion",
        "cera depilatoria", "cuchillas de afeitar", "crema corporal", "aceite corporal",
    ],
    "Bebé": [
        "bebé", "bebe", "pañal", "panal", "carrito", "trona", "chupete", "biberón",
        "biberon", "cuna", "hamaca bebé", "hamaca bebe", "toallitas bebé", "toallitas bebe",
        "silla de paseo", "portabebé", "portabebe", "monitor de bebé", "monitor de bebe",
    ],
    "Mascotas": [
        "perro", "gato", "pienso", "correa", "arenero", "rascador", "acuario",
        "jaula", "comedero mascota", "transportín", "transportin", "collar antipulgas",
        "cama para perro", "juguete para perro", "arena para gatos",
    ],
    "Informática": [
        "pc gaming", "tarjeta gráfica", "tarjeta grafica", "placa base", "procesador",
        "memoria ram", "gaming", "silla gaming", "monitor gaming", "pendrive",
        "fuente de alimentación", "fuente de alimentacion", "torre pc", "ventilador pc",
        "disco nvme", "hub usb-c", "docking station",
    ],
    "Coche y moto": [
        "coche", "moto", "neumático", "neumatico", "gps", "dashcam", "cargador coche",
        "radar", "co-driver", "detector de radares", "alerta de radares", "aceite motor",
        "limpiaparabrisas", "funda coche", "soporte móvil coche", "soporte movil coche",
        "cámara marcha atrás", "camara marcha atras", "casco moto", "silla de coche bebé",
        "silla de coche bebe",
    ],
    "Bricolaje y jardín": [
        "taladro", "atornillador", "herramienta", "cortacésped", "cortacesped",
        "manguera", "maceta", "invernadero", "sierra", "amoladora", "escalera",
        "caja de herramientas", "guantes de jardín", "guantes de jardin", "tijeras de podar",
        "hidrolimpiadora", "generador eléctrico", "generador electrico",
    ],
    "Salud": [
        "tensiómetro", "tensiometro", "termómetro", "termometro", "báscula", "bascula",
        "mascarilla", "vitaminas", "suplemento", "colágeno", "colageno", "melatonina",
        "test de embarazo", "kinesiología", "kinesiologia", "vendas", "botiquín", "botiquin",
    ],
    "Videojuegos": [
        "ps5", "playstation", "xbox", "nintendo switch", "mando", "videojuego",
        "auriculares gaming", "silla gamer", "volante gaming", "tarjeta psn",
        "suscripción xbox game pass", "suscripcion xbox game pass",
    ],
    "Papelería y oficina": [
        "mochila escolar", "estuche", "bolígrafo", "boligrafo", "cuaderno", "agenda",
        "calculadora", "rotulador", "silla de oficina", "escritorio", "organizador de escritorio",
    ],
    "Viajes y maletas": [
        "maleta", "trolley", "mochila de viaje", "neceser", "almohada de viaje",
        "candado maleta", "báscula de maleta", "bascula de maleta",
    ],
}

PRICE_RE = re.compile(r"(\d{1,4}(?:[.,]\d{1,2})?)\s?€")
DISCOUNT_RE = re.compile(r"-\s?(\d{1,2})\s?%")
HASHTAG_RE = re.compile(r"#(\w+)")
CODE_RE = re.compile(r"(?:c[oòó]digo|cup[oòó]n|code)[\s:]+([A-Z0-9]{4,15})", re.IGNORECASE)
# Palabras de plantilla que a veces aparecen justo después de "código"/"cupón"
# pero que NO son un código real (p.ej. "‼️CUPÓN CAZADO‼️" es solo la
# cabecera del post, no un código para copiar).
BOGUS_CODE_WORDS = {
    "CAZADO", "CAZADA", "APLICADO", "APLICABLE", "APLICA", "DISPONIBLE",
    "GRATIS", "ACTIVO", "ACTIVA", "AQUI", "AQUÍ", "EXCLUSIVO", "EXCLUSIVA",
    "LIMITADO", "LIMITADA", "ESPECIAL", "AUTOMATICO", "AUTOMÁTICO",
}
URL_RE = re.compile(r"https?://\S+")

# Líneas "de plantilla" que no son el título real del producto y deben ignorarse
# al buscar la línea con el nombre del artículo.
# Líneas "de plantilla" que no son el título real del producto y deben ignorarse
# al buscar la línea con el nombre del artículo. Se compara contra una versión
# SIN acentos del texto (ver normalize()) para no depender de que el canal
# escriba bien los acentos (p.ej. "CÒDIGO" con acento grave por error).
SKIP_TITLE_RE = re.compile(
    r"^(oferta cazada|chollo|flash sale|enlace|link|codigo|cupon|"
    r"segunda unidad|\d{1,2}%? de descuento|precio por \d+ unidad)\b",
    re.IGNORECASE,
)

# Quita emojis y símbolos decorativos para detectar la línea que es un
# título real (no solo un icono suelto tipo "‼️" o "🔥").
EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF"
    "\u2000-\u206F\u2190-\u21FF\u2300-\u23FF\u2700-\u27BF\uFE0F]+"
)


def fetch_index_ids(channel: str, session: requests.Session, max_pages: int, delay: float) -> list[str]:
    """Recorre varias páginas del historial del canal (usando el parámetro
    ?before= que entiende la vista pública de Telegram) para traer más de
    los ~20 posts más recientes."""
    ids: list[str] = []
    before = None
    for _ in range(max_pages):
        url = INDEX_URL.format(channel=channel)
        if before:
            url += f"?before={before}"
        resp = session.get(url, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        page_ids = []
        for msg in soup.select(".tgme_widget_message[data-post]"):
            post_id = msg["data-post"].split("/")[-1]
            if post_id.isdigit():
                page_ids.append(post_id)
        if not page_ids:
            break
        new_ids = [i for i in page_ids if i not in ids]
        ids.extend(new_ids)
        if not new_ids:
            break  # ya no hay páginas más antiguas nuevas, hemos llegado al final
        before = min(int(i) for i in page_ids)
        time.sleep(delay)
    return ids


def fetch_post_detail(channel: str, post_id: str, session: requests.Session) -> dict:
    """Lee el texto e imagen fiables de un post individual vía sus meta tags og:*."""
    url = POST_URL.format(channel=channel, post_id=post_id)
    try:
        resp = session.get(url, timeout=12)
        resp.raise_for_status()
    except requests.RequestException:
        return {}
    soup = BeautifulSoup(resp.text, "html.parser")
    desc_tag = soup.find("meta", attrs={"property": "og:description"})
    image_tag = soup.find("meta", attrs={"property": "og:image"})
    time_tag = soup.select_one("time")
    return {
        "text": (desc_tag.get("content") or "").strip() if desc_tag else "",
        "image": image_tag.get("content") if image_tag else None,
        "date": time_tag.get("datetime") if time_tag else None,
    }


def resolve_final_url(url: str, session: requests.Session) -> str:
    """Sigue redirecciones (enlaces acortados/de afiliado) para saber el dominio real."""
    try:
        resp = session.head(url, allow_redirects=True, timeout=8)
        if resp.url:
            return resp.url
    except requests.RequestException:
        pass
    try:
        resp = session.get(url, allow_redirects=True, timeout=8, stream=True)
        return resp.url
    except requests.RequestException:
        return url


def extract_marketplace(link: str | None, session: requests.Session, resolve_links: bool) -> str:
    if not link:
        return "Otro"
    domain = urlparse(link).netloc.replace("www.", "")

    def match(d: str) -> str | None:
        for known_domain, name in MARKETPLACE_DOMAINS.items():
            if known_domain in d:
                return name
        return None

    found = match(domain)
    if found:
        return found

    # Dominio desconocido (posible acortador): solo entonces gastamos una
    # petición extra para ver a dónde redirige de verdad.
    if resolve_links:
        final = resolve_final_url(link, session)
        final_domain = urlparse(final).netloc.replace("www.", "")
        found = match(final_domain)
        if found:
            return found
        domain = final_domain

    base = domain.split(".")[0]
    return base.capitalize() if base else "Otro"


def extract_link(text: str) -> str | None:
    match = URL_RE.search(text)
    if not match:
        return None
    return match.group(0).rstrip(").,;:")


def extract_title(text: str) -> str:
    """Busca la primera línea con contenido real: descarta líneas que son solo
    emojis, líneas de plantilla ('OFERTA CAZADA -X%'), y líneas que son enlaces."""
    for line in text.split("\n"):
        if URL_RE.search(line):
            continue
        clean = EMOJI_RE.sub("", line).strip(" -•*:¡!¿?.")
        if len(clean) < 8:
            continue
        if SKIP_TITLE_RE.match(normalize(clean)):
            continue
        return clean
    # No se encontró ninguna línea de título real: suele pasar en posts que
    # son solo "código cazado" sin descripción de producto. Mejor un título
    # genérico legible que volcar el texto de plantilla tal cual.
    if re.search(r"c[oòó]digo|cup[oòó]n", text, re.IGNORECASE):
        return "Código de descuento"
    fallback = EMOJI_RE.sub("", text).strip()
    return fallback[:120] if fallback else (text.split("\n")[0] if text else "")


def extract_coupon_code(text: str) -> str | None:
    # Usamos finditer (no solo la primera coincidencia) porque a veces la
    # cabecera del post ("‼️CÓDIGO CAZADO‼️") coincide por error con el
    # patrón antes que el código real más abajo en el texto.
    for match in CODE_RE.finditer(text):
        code = match.group(1).upper()
        if code not in BOGUS_CODE_WORDS:
            return code
    return None


def extract_description(text: str, title: str, max_chars: int = 220) -> str:
    """Recoge las líneas que no son el título, un enlace, un código de cupón
    o solo hashtags, para mostrarlas como descripción del producto."""
    parts = []
    for line in text.split("\n"):
        if URL_RE.search(line):
            continue
        clean = EMOJI_RE.sub("", line).strip(" -•*:¡!¿?.")
        if not clean or clean == title:
            continue
        if SKIP_TITLE_RE.match(normalize(clean)):
            continue
        if re.fullmatch(r"(#\w+\s*)+", line.strip()):
            continue
        parts.append(clean)
    description = " ".join(parts).strip()
    return (description[:max_chars] + "…") if len(description) > max_chars else description


def normalize(s: str) -> str:
    """Quita tildes/acentos para comparar sin depender de si el texto los lleva."""
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def guess_category(text: str, hashtags: list[str]) -> str:
    normalized_text = normalize(text)
    normalized_tags = [normalize(h) for h in hashtags]

    # 1. Si algún hashtag del post coincide (total o parcialmente) con el
    #    nombre de una categoría, úsala directamente.
    for cat in CATEGORY_KEYWORDS:
        cat_norm = normalize(cat)
        if any(cat_norm in tag or tag in cat_norm for tag in normalized_tags):
            return cat

    # 2. Si no, cuenta cuántas palabras clave de cada categoría aparecen en
    #    el texto y elige la categoría con más coincidencias (más precisa
    #    que quedarse con la primera que encaje).
    best_cat, best_score = None, 0
    for cat, keywords in CATEGORY_KEYWORDS.items():
        score = sum(1 for kw in keywords if normalize(kw) in normalized_text)
        if score > best_score:
            best_cat, best_score = cat, score
    return best_cat or "Otros"


def build_oferta(channel: str, post_id: str, detail: dict, session: requests.Session, resolve_links: bool) -> dict | None:
    text = detail.get("text", "")
    if not text:
        return None  # posts sin texto (solo imagen/reenvío sin caption) se descartan

    link = extract_link(text)
    if not link:
        return None  # sin enlace no hay oferta que mostrar (p.ej. posts promocionales de "únete a nuestros canales")
    marketplace = extract_marketplace(link, session, resolve_links)
    hashtags = HASHTAG_RE.findall(text)
    price_match = PRICE_RE.findall(text)
    discount_match = DISCOUNT_RE.search(text)
    title = extract_title(text)
    description = extract_description(text, title)

    return {
        "id": f"{channel}/{post_id}",
        "date": detail.get("date"),
        "text": text,
        "title": title,
        "description": description,
        "image": detail.get("image"),
        "link": link,
        "marketplace": marketplace,
        "category": guess_category(text, hashtags),
        "hashtags": hashtags,
        "coupon_code": extract_coupon_code(text),
        "prices_found": price_match,
        "discount_percent": discount_match.group(1) if discount_match else None,
    }


def scrape(
    channel: str,
    existing_by_id: dict[str, dict],
    resolve_links: bool = True,
    max_pages: int = 6,
    delay: float = 0.25,
) -> list[dict]:
    with requests.Session() as session:
        session.headers.update(HEADERS)
        ids = fetch_index_ids(channel, session, max_pages=max_pages, delay=delay)

        ofertas = []
        for post_id in ids:
            full_id = f"{channel}/{post_id}"
            if full_id in existing_by_id:
                # Ya la teníamos de una ejecución anterior: no hace falta
                # volver a visitarla ni volver a resolver su enlace.
                ofertas.append(existing_by_id[full_id])
                continue
            detail = fetch_post_detail(channel, post_id, session)
            oferta = build_oferta(channel, post_id, detail, session, resolve_links)
            if oferta:
                ofertas.append(oferta)
            time.sleep(delay)

    ofertas.sort(key=lambda o: int(o["id"].rsplit("/", 1)[-1]), reverse=True)  # más recientes primero
    return ofertas


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel", required=True, help="Nombre del canal, sin @ (ej. ofertasychollosesp)")
    parser.add_argument("--out", default="ofertas.json")
    parser.add_argument(
        "--pages", type=int, default=6,
        help="Cuántas páginas de ~20 mensajes recorrer hacia atrás en el historial (por defecto 6 ≈ 120 posts)."
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Ignora lo ya guardado y reprocesa todas las ofertas desde cero (útil tras corregir la lógica de detección)."
    )
    args = parser.parse_args()

    existing_by_id = {}
    if os.path.exists(args.out) and not args.force:
        try:
            with open(args.out, "r", encoding="utf-8") as f:
                previous = json.load(f)
            for oferta in previous.get("ofertas", []):
                if oferta.get("id"):
                    existing_by_id[oferta["id"]] = oferta
        except (json.JSONDecodeError, OSError):
            pass  # si el archivo previo está corrupto, simplemente se regenera de cero

    try:
        ofertas = scrape(args.channel, existing_by_id, max_pages=args.pages)
    except requests.RequestException as e:
        print(f"Error al descargar el canal: {e}", file=sys.stderr)
        sys.exit(1)

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "channel": args.channel,
        "count": len(ofertas),
        "ofertas": ofertas,
    }

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    nuevas = sum(1 for o in ofertas if o["id"] not in existing_by_id)
    print(f"Guardadas {len(ofertas)} ofertas en {args.out} ({nuevas} nuevas)")


if __name__ == "__main__":
    main()
