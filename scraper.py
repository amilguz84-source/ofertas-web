"""
Scraper de canal público de Telegram -> ofertas.json

Lee la vista previa pública (t.me/s/<canal>), que no requiere login ni API key,
y extrae cada publicación como una oferta estructurada.

Uso:
    python scraper.py --channel ofertasychollosesp --out ofertas.json
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://t.me/s/{channel}"

# Mapea el dominio del primer enlace externo a un nombre de marketplace legible.
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


def fetch_html(channel: str) -> str:
    url = BASE_URL.format(channel=channel)
    headers = {"User-Agent": "Mozilla/5.0 (compatible; OfertasBot/1.0)"}
    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    return resp.text


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


def extract_marketplace(link: str | None) -> str:
    if not link:
        return "Otro"
    domain = urlparse(link).netloc.replace("www.", "")
    for known_domain, name in MARKETPLACE_DOMAINS.items():
        if known_domain in domain:
            return name
    base = domain.split(".")[0]
    return base.capitalize() if base else "Otro"


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
    return fallback[:120] if fallback else text.split("\n")[0]


def extract_coupon_code(text: str) -> str | None:
    match = CODE_RE.search(text)
    return match.group(1).upper() if match else None


def guess_category(text: str, hashtags: list[str]) -> str:
    # 1. Si el propio post usa un hashtag que coincide con una categoría conocida, úsalo.
    lowered_tags = [h.lower() for h in hashtags]
    for cat in CATEGORY_KEYWORDS:
        if cat.lower() in lowered_tags:
            return cat
    # 2. Si no, busca por palabras clave en el texto.
    lowered_text = text.lower()
    for cat, keywords in CATEGORY_KEYWORDS.items():
        if any(kw in lowered_text for kw in keywords):
            return cat
    return "Otros"


def parse_message(msg_div, session: requests.Session, resolve_links: bool) -> dict | None:
    text_div = msg_div.select_one(".tgme_widget_message_text")
    text = text_div.get_text("\n", strip=True) if text_div else ""
    if not text:
        return None  # posts sin texto (solo imagen/reenvío) se descartan por simplicidad

    # Imagen (viene como background-image en un <a>)
    image_url = None
    photo_wrap = msg_div.select_one(".tgme_widget_message_photo_wrap")
    if photo_wrap and photo_wrap.get("style"):
        match = re.search(r"url\(['\"]?(.*?)['\"]?\)", photo_wrap["style"])
        if match:
            image_url = match.group(1)

    # El enlace de compra puede venir de tres sitios, por orden de prioridad:
    # 1) un botón bajo el post ("Ver oferta")
    # 2) la tarjeta de vista previa que Telegram genera al compartir una URL
    #    (frecuente en posts de Amazon: el mensaje es básicamente el link)
    # 3) un enlace suelto dentro del texto (p.ej. "Enlace: https://...")
    button_links = [a["href"] for a in msg_div.select(".tgme_widget_message_reply_markup a[href]")]

    link_preview = msg_div.select_one("a.tgme_widget_message_link_preview")
    preview_href = link_preview.get("href") if link_preview else None

    text_links = [a["href"] for a in text_div.select("a[href]")] if text_div else []

    raw_link = button_links[0] if button_links else (preview_href or (text_links[0] if text_links else None))

    # Si el post no traía foto propia, usa la miniatura de la vista previa
    if image_url is None and link_preview:
        preview_img_el = link_preview.select_one("i.link_preview_right_image, i.link_preview_image")
        if preview_img_el and preview_img_el.get("style"):
            match = re.search(r"url\(['\"]?(.*?)['\"]?\)", preview_img_el["style"])
            if match:
                image_url = match.group(1)

    final_link = raw_link
    if raw_link and resolve_links:
        final_link = resolve_final_url(raw_link, session)

    preview_title_el = link_preview.select_one(".link_preview_title") if link_preview else None
    preview_title = preview_title_el.get_text(strip=True) if preview_title_el else None

    marketplace = extract_marketplace(final_link)

    # Fecha
    time_tag = msg_div.select_one("time")
    date_iso = time_tag["datetime"] if time_tag else None

    # ID del post (viene en data-post="canal/123")
    post_id = msg_div.get("data-post", "")

    hashtags = HASHTAG_RE.findall(text)
    price_match = PRICE_RE.findall(text)
    discount_match = DISCOUNT_RE.search(text)

    return {
        "id": post_id,
        "date": date_iso,
        "text": text,
        "title": preview_title or extract_title(text),
        "image": image_url,
        "link": final_link,
        "marketplace": marketplace,
        "category": guess_category(text, hashtags),
        "hashtags": hashtags,
        "coupon_code": extract_coupon_code(text),
        "prices_found": price_match,  # ej. ["19,99", "29,99"] -> normalmente [precio_final, precio_original]
        "discount_percent": discount_match.group(1) if discount_match else None,
    }


def scrape(channel: str, resolve_links: bool = True) -> list[dict]:
    html = fetch_html(channel)
    soup = BeautifulSoup(html, "html.parser")
    messages = soup.select(".tgme_widget_message")
    ofertas = []
    with requests.Session() as session:
        session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; OfertasBot/1.0)"})
        for msg in messages:
            parsed = parse_message(msg, session, resolve_links)
            if parsed:
                ofertas.append(parsed)
    # Más recientes primero
    ofertas.reverse()
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
