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
import re
import sys
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

INDEX_URL = "https://t.me/s/{channel}"
POST_URL = "https://t.me/{channel}/{post_id}"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; OfertasBot/1.0)"}

# Mapea el dominio del enlace a un nombre de marketplace legible.
MARKETPLACE_DOMAINS = {
    "amazon.es": "Amazon",
    "amzn.to": "Amazon",
    "amzn.eu": "Amazon",
    "amz.tf": "Amazon",
    "aliexpress.com": "AliExpress",
    "es.aliexpress.com": "AliExpress",
    "pccomponentes.com": "PcComponentes",
    "elcorteingles.es": "El Corte Inglés",
    "mediamarkt.es": "MediaMarkt",
    "carrefour.es": "Carrefour",
    "decathlon.es": "Decathlon",
    "zalando.es": "Zalando",
    "fnac.es": "Fnac",
    "worten.es": "Worten",
    "leroymerlin.es": "Leroy Merlin",
}

# Categorías por palabras clave, usadas si el post no lleva hashtag de categoría.
CATEGORY_KEYWORDS = {
    "Tecnología": ["móvil", "portátil", "auricular", "smartwatch", "tablet", "monitor", "ssd", "cargador", "smartphone"],
    "Hogar": ["cocina", "sofá", "sábana", "colchón", "aspirador", "sartén", "robot aspirador", "lámpara"],
    "Moda": ["zapatilla", "camiseta", "pantalón", "chaqueta", "vestido", "sudadera", "abrigo"],
    "Deporte": ["bicicleta", "running", "gimnasio", "pesa", "fitness", "patinete"],
    "Juguetes": ["juguete", "lego", "muñeca", "puzzle"],
}

PRICE_RE = re.compile(r"(\d{1,4}(?:[.,]\d{1,2})?)\s?€")
DISCOUNT_RE = re.compile(r"-\s?(\d{1,2})\s?%")
HASHTAG_RE = re.compile(r"#(\w+)")
CODE_RE = re.compile(r"(?:c[oó]digo|cup[oó]n|code)[\s:]+([A-Z0-9]{4,15})", re.IGNORECASE)
URL_RE = re.compile(r"https?://\S+")

# Líneas "de plantilla" que no son el título real del producto y deben ignorarse
# al buscar la línea con el nombre del artículo.
SKIP_TITLE_RE = re.compile(
    r"^(oferta cazada|chollo|flash sale|enlace|link|c[oó]digo|cup[oó]n)\b", re.IGNORECASE
)

# Quita emojis y símbolos decorativos para detectar la línea que es un
# título real (no solo un icono suelto tipo "‼️" o "🔥").
EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF"
    "\u2190-\u21FF\u2300-\u23FF\u2700-\u27BF\uFE0F]+"
)


def fetch_index_ids(channel: str, session: requests.Session) -> list[str]:
    """Devuelve los IDs de los posts recientes listados en la vista general del canal."""
    url = INDEX_URL.format(channel=channel)
    resp = session.get(url, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    ids = []
    for msg in soup.select(".tgme_widget_message[data-post]"):
        post_id = msg["data-post"].split("/")[-1]
        if post_id.isdigit():
            ids.append(post_id)
    # quita duplicados conservando el orden
    return list(dict.fromkeys(ids))


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
        if SKIP_TITLE_RE.match(clean):
            continue
        return clean
    fallback = EMOJI_RE.sub("", text).strip()
    return fallback[:120] if fallback else (text.split("\n")[0] if text else "")


def extract_coupon_code(text: str) -> str | None:
    match = CODE_RE.search(text)
    return match.group(1).upper() if match else None


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
        if SKIP_TITLE_RE.match(clean):
            continue
        if re.fullmatch(r"(#\w+\s*)+", line.strip()):
            continue
        parts.append(clean)
    description = " ".join(parts).strip()
    return (description[:max_chars] + "…") if len(description) > max_chars else description


def guess_category(text: str, hashtags: list[str]) -> str:
    lowered_tags = [h.lower() for h in hashtags]
    for cat in CATEGORY_KEYWORDS:
        if cat.lower() in lowered_tags:
            return cat
    lowered_text = text.lower()
    for cat, keywords in CATEGORY_KEYWORDS.items():
        if any(kw in lowered_text for kw in keywords):
            return cat
    return "Otros"


def build_oferta(channel: str, post_id: str, detail: dict, session: requests.Session, resolve_links: bool) -> dict | None:
    text = detail.get("text", "")
    if not text:
        return None  # posts sin texto (solo imagen/reenvío sin caption) se descartan

    link = extract_link(text)
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


def scrape(channel: str, resolve_links: bool = True, delay: float = 0.3) -> list[dict]:
    with requests.Session() as session:
        session.headers.update(HEADERS)
        ids = fetch_index_ids(channel, session)
        ofertas = []
        for post_id in ids:
            detail = fetch_post_detail(channel, post_id, session)
            oferta = build_oferta(channel, post_id, detail, session, resolve_links)
            if oferta:
                ofertas.append(oferta)
            time.sleep(delay)  # ser considerados con el ritmo de peticiones
    ofertas.reverse()  # más recientes primero
    return ofertas


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel", required=True, help="Nombre del canal, sin @ (ej. ofertasychollosesp)")
    parser.add_argument("--out", default="ofertas.json")
    args = parser.parse_args()

    try:
        ofertas = scrape(args.channel)
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

    print(f"Guardadas {len(ofertas)} ofertas en {args.out}")


if __name__ == "__main__":
    main()
