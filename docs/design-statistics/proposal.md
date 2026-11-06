# Statistiky – návrh redesignu

Mockup: `mockup.html` (desktop, reálná data z testovací instance).

## Co se mění

- Jeden globální přepínač **Seskupit podle** (Skupina / Ořezový model / Rotační model / Měsíc) v lepicí liště; per‑sekční přepínače mizí.
- Lepicí lišta má dva řádky: období + filtry; kotvy sekcí + Seskupit podle + Export (menu).
- **Hrdinská řada**: Lidský čas na sken (zvýrazněná dlaždice), Strojový čas na sken, Člověk : stroj, Od nahrání po uložení. Delta vs. předchozí období a sparkline se zobrazí jen, když existují data.
- Počty (tituly, skeny, upravené, uživatelé, otevření, aktivní čas) v jednom kompaktním řádku spolu se stavy titulů.
- Nadpisy sekcí mimo karty, bez podtitulů. Definice metrik jen v tooltipu (`title`).
- Kvalita predikcí a Anomálie: dlaždice bez hodnotících štítků; jen „málo dat“ při n < 50. Prahy jen v tooltipu.
- Časová osa titulu: 5 řádků na logaritmické ose s dílky (10 s · 1 min · 10 min · 1 h · 1 den), barvy stroj / člověk / integrace.
- Vývoj po měsících: jeden sloupcový graf, dvě série (člověk, stroj), jedna osa s / sken.
- Tabulky: lepicí první sloupec, dvouúrovňová hlavička (Stroj · Člověk · Průchod titulu), jednotky v hlavičce, sloupec Trend, „málo dat“ u malých vzorků, vedlejší sloupce pod „Podrobnosti“.
- Používání editoru a Nastavení editoru: sbalitelné karty s číselným souhrnem v hlavičce. Nastavení sbalené výchozí.
- Export: jedno menu v liště + tichá ikona v hlavičce sekce.

## Pořadí sekcí

1. Přehled – hrdinská řada, kompaktní řádek, stavy titulů
2. Čas – časová osa, vývoj po měsících, tabulka
3. Kvalita predikcí – 4 dlaždice, tabulka (+ Podrobnosti)
4. Anomálie – 4 dlaždice, tabulka podle příznaku, tabulka podle seskupení
5. Používání editoru – sbalitelné
6. Nastavení editoru – sbalené

## Layout a styl

- Pozadí `--bg-bright`, obsah max. 1440 px, odsazení `--spacing-x4`; dlaždice `repeat(4, 1fr)` s mezerou `--spacing-x3`, karty `--spacing-x4`.
- Dlaždice: hrdinská 32 px/600, ostatní `--font-size-x4`/600; popisek `micro-label`; číselný podřádek 12 px `--text-light`.
- Pásma: `--tag-success-*`, `--tag-pending-*`, `--tag-error-*`, „málo dat“ `--bg-bright` + `--text-light`; vždy s textem.
- Série: `--primary` (člověk / jedna série), `--bg-contrast` (stroj), `#7367A8` (integrace). Status barvy jen pro delty.
- Delta: 12 px, zelená = zlepšení (čas dolů, IoU nahoru), červená = zhoršení.
- Výchozí prahy (tooltip): IoU 0,98 / 0,95; posun 0,5 % / 1,5 %; Δ úhel 0,3° / 1°; přesnost a záchyt 60 % / 30 %; upravené skeny 30 %.

## Responzivita

- ≥ 1200 px: 4 dlaždice v řadě, dvě karty vedle sebe.
- 900–1200 px: 2 × 2 dlaždice, karty pod sebou.
- < 900 px: dlaždice pod sebou, lišta na jeden řádek + výsuvné filtry, kotvy jako select, tabulky posuvné s lepicím prvním sloupcem.

## Komponenty v Angularu

| Komponenta | Změna |
| --- | --- |
| nová `app-sparkline` | SVG polyline, 28 px |
| nová `app-phase-chart` | log osa, 5 fází, legenda; nahrazuje `.phase-bar` |
| `app-bar-chart` | 2 série, legenda, tooltip, hodnota jen u posledního bodu |
| nová `app-stats-table` | lepicí první sloupec, dvouúrovňová hlavička, Trend, Celkem, Podrobnosti |
| nová `app-section-header` | nadpis, `collapsible` + `summary`, ikona exportu |
| `StatisticsService` | jeden signál `groupBy`; anomálie: souhrn + seskupení současně |
| lišta v `statistics.component` | kotvy (IntersectionObserver), globální Seskupit podle, menu Export |
| backend `/stats/*` | `compare=previous`, měsíční řady pro sparkline, prahy z konfigurace |
