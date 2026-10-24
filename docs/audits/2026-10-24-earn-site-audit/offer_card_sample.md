# Offer card — Truhlarstvi Fiktivni (fixture)

**Status:** internal offer card — owner only, no outreach

- Web: http://127.0.0.1:57558/bad/index.html (audit 2026-10-24T22:31:58+00:00)
- Tier: **Landing / jednoduchý web** — 14 900 Kč
- Sleva: 0 % (max 10 %) → 14 900 Kč
- Důvod tieru: internal pages: 5 (<=5), no clinic-scale/multilingual/e-shop signals
- První krok: free audit + first-screen mockup (owner decides whether/how to contact)

## Nejsilnější změřené problémy

1. `no_visible_cta` (high) — evidence: `{"tel_links": 0, "mailto_links": 0, "forms": 0, "cta_keyword_matches": 0, "keywords_checked": 23}`
   - Oprava: Přidat viditelné tlačítko „Zavolat“ / „Objednat“ / kontaktní formulář do první obrazovky.
2. `mobile_horizontal_overflow` (high) — evidence: `{"viewport_width_px": 390, "content_width_px": 1200}`
   - Oprava: Odstranit pevné šířky (width: 1200px, tabulkové layouty), použít max-width: 100 %.
3. `no_viewport_meta` (high) — evidence: `{"meta_viewport": "missing", "mobile_zoom_scale": 0.325}`
   - Oprava: Přidat <meta name="viewport" content="width=device-width, initial-scale=1"> a responzivní CSS.

Všechny nálezy: no_viewport_meta, mobile_horizontal_overflow, small_text_mobile, small_tap_targets, missing_meta_description, missing_h1, missing_lang, images_without_alt, no_visible_cta, outdated_copyright, console_errors, broken_links

_Žádný text pro oslovení se negeneruje (rozhodnutí vlastníka 24.10)._
