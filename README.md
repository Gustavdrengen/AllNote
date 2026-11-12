# AllNote

Dansk gymnasie-factsheets som HTML-indhold plus et build-trin, der renderer
printklare A4-PDF'er i én fælles husstil.

```
build.py                 HTML -> PDF (indsætter CSS, sidefod, sidetal)
factsheet.css            husstilen (refereres aldrig fra et sheet)
tools/diagram.py         SVG-diagramgenerator (grafer og tegninger)
requirements.txt         playwright + pypdf
content/Matematik/…      én mappe per fag, kun indhold: Navn.html
content/Dansk/Sprog/…    valgfri gruppemappe ét niveau dybere
dist/…                  de færdige PDF'er, spejler content/ 1:1
```

## Installation

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

`build.py` bruger headless Chromium til at printe, så browseren skal installeres
én gang per maskine.

## Kompilér PDF'er

```bash
python build.py                                                # alle sheets, kun de ændrede
python build.py Matematik                                      # ét fag
python build.py Dansk/Sprog                                    # én gruppe
python build.py content/Matematik/Retvinklede_trekanter.html   # ét sheet
python build.py --force                                        # alt (fx efter en stilændring)
```

Hver PDF skrives til samme mappesti under `dist/` (`dist/<Fag>/` eller
`dist/<Fag>/<Gruppe>/`) med samme basisnavn som HTML-filen, så `content/`
spejles 1:1. Et sheet må højst ligge ét gruppeniveau under faget. Uden
argumenter bygges alle fag, men kun de
sheets, hvor PDF'en er ældre end HTML-filen, `factsheet.css` eller
`build.py` selv; `--force` ignorerer det og bygger alt igen.

## Læs outputtet

```
  ok   dist/Matematik/Retvinklede_trekanter.pdf  1 p  0.42s
       ! no <h1> title
  up   dist/Matematik/Ligninger.pdf
  FAIL content/Matematik/Keglesnit.html: Error: ...

2 PDF(s) rebuilt, 1 up to date, 3 page(s) total, 1.8s
1 sheet(s) FAILED
```

* `ok` — bygget; tallet er sidetallet, og tiden er per sheet.
* `!` — advarsel om manglende `<h1>`, egen CSS (flyt reglerne til
  `factsheet.css`), `<img>` i stedet for inline SVG eller et sheet over
  `MAX_PAGES` (3 sider). Målet er 1–2 sider; bliver et sheet for langt, skæres
  indholdet ned — typografi og marginer er fastlagt af designet.
* `up` — PDF'en er allerede nyere end kilderne, så den blev sprunget over.
* `FAIL` — sheetet kunne ikke printes; de øvrige bygges stadig.

Exitkoden er 1, hvis mindst et sheet fejlede, så kommandoen kan bruges i CI.

## Tilføj et sheet

1. Opret `content/Fag/Skema_navn.html` (eller `content/Fag/Gruppe/Skema_navn.html`)
   — kun indhold, ingen CSS.
2. Skriv dansk, tæt, uden fyld og uden kildehenvisninger.
3. Lav diagrammer med `python tools/diagram.py …` og indsæt snippet'en.
4. Kør `python build.py content/Fag/Skema_navn.html` og tjek PDF'en i `dist/`.

`AGENTS.md` er den fulde produktionskontrakt: struktur, klasser, MathML,
diagram-skema og kvalitetschecklisten.

## Byg alt om efter en designændring

HTML'en indeholder ingen styling, så hele biblioteket kan gengives med én
kommando, når husstilen er ændret:

```bash
# redigér factsheet.css (eller PRINT-marginerne i build.py)
python build.py --force
```

## Fejlfinding

* `ModuleNotFoundError: playwright` / `pypdf` → kør installationskommandoen igen.
* Chromium mangler → `python -m playwright install chromium`.
* Tom eller uændret PDF → tjek om `skip` blev udskrevet, og brug `--force`.
* Formler vises som rå tekst → fejl i MathML i HTML-filen; ret HTML'en og byg
  igen. Ret aldrig PDF'en direkte.
