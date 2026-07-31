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


def fetch_html(channel: str) -> str:
    url = BASE_URL.format(channel=channel)
    headers = {"User-Agent": "Mozilla/5.0 (compatible; OfertasBot/1.0)"}
    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    return resp.text


def extract_marketplace(links: list[str]) -> str | None:
    for link in links:
        domain = urlparse(link).netloc.replace("www.", "")
        for known_domain, name in MARKETPLACE_DOMAINS.items():
            if known_domain in domain:
                return name
    return None


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


def parse_message(msg_div) -> dict | None:
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

    # Enlaces dentro del texto
    links = [a["href"] for a in text_div.select("a[href]")] if text_div else []
    marketplace = extract_marketplace(links)

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
        "image": image_url,
        "link": links[0] if links else None,
        "marketplace": marketplace or "Otro",
        "category": guess_category(text, hashtags),
        "hashtags": hashtags,
        "prices_found": price_match,  # ej. ["19,99", "29,99"] -> normalmente [precio_final, precio_original]
        "discount_percent": discount_match.group(1) if discount_match else None,
    }


def scrape(channel: str) -> list[dict]:
    html = fetch_html(channel)
    soup = BeautifulSoup(html, "html.parser")
    messages = soup.select(".tgme_widget_message")
    ofertas = []
    for msg in messages:
        parsed = parse_message(msg)
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
