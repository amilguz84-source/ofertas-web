# Web de ofertas — El Cazador de Chollos

Web estática que se alimenta automáticamente de las publicaciones del canal
de Telegram `@ofertasychollosesp`, las clasifica por categoría/marketplace y
las muestra en tarjetas tipo "etiqueta de precio".

## Estructura

```
index.html          -> la web (autocontenida, sin build step)
ofertas.json         -> datos que consume la web (datos de ejemplo incluidos)
scraper.py           -> lee el canal público y regenera ofertas.json
requirements.txt     -> dependencias de scraper.py
.github/workflows/update.yml -> automatiza scraper.py cada 20 min
```

## Probarlo en local ahora mismo

Ya incluye `ofertas.json` de ejemplo, así que puedes abrir `index.html`
directamente en un navegador o servirlo con:

```bash
python -m http.server 8000
```

y visitar `http://localhost:8000`.

## Conectarlo a tu canal real

1. Instala dependencias:
   ```bash
   pip install -r requirements.txt
   ```
2. Ejecuta el scraper:
   ```bash
   python scraper.py --channel ofertasychollosesp --out ofertas.json
   ```
   Esto sobrescribe `ofertas.json` con las publicaciones reales más recientes
   del canal (la vista pública `t.me/s/...` muestra los últimos ~20 mensajes;
   cada ejecución trae los más nuevos).

## Publicarlo gratis (GitHub Pages)

1. Sube esta carpeta a un repositorio de GitHub.
2. En **Settings → Pages**, activa GitHub Pages sobre la rama `main`.
3. El workflow `.github/workflows/update.yml` ya está configurado para
   ejecutar el scraper cada 20 minutos y hacer commit de los cambios — cada
   commit dispara un redeploy automático de la página. No necesitas activarlo
   a mano, solo asegúrate de que las Actions estén habilitadas en el repo
   (Settings → Actions → General → "Allow all actions").

Alternativa igual de válida: desplegar en **Vercel** o **Netlify** conectando
el mismo repo (detectan que es HTML estático automáticamente) y dejar que
GitHub Actions siga siendo quien actualiza `ofertas.json`.

## Personalizar la categorización

Si prefieres control total sobre las categorías en vez de la detección por
palabras clave, la forma más fiable es que definas hashtags fijos al publicar
en el canal (`#tecnologia`, `#hogar`, `#moda`...) — `scraper.py` los detecta
automáticamente y los prioriza sobre la detección por palabras clave
(ver `guess_category()` en `scraper.py`).

## Limitaciones a tener en cuenta

- La vista pública de Telegram (`t.me/s/...`) solo expone los mensajes más
  recientes, no el historial completo. Para importar todo el histórico del
  canal necesitarías usar la Bot API oficial o una librería como Telethon
  con tu propia cuenta.
- Si Telegram cambia el HTML de la vista pública, los selectores CSS de
  `scraper.py` (`.tgme_widget_message`, etc.) podrían necesitar un ajuste.
- Revisa los Términos de Servicio de Telegram sobre scraping automatizado
  de contenido; al ser tu propio canal público no debería haber problema,
  pero conviene no exceder frecuencias razonables (por eso el workflow usa
  20 minutos, no segundos).
