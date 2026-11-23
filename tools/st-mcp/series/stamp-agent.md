# Agento užduotis: dydžio vartai ir bibliotekos ženklai serijoje

Bendros taisyklės yra `docs/AGENTIC_WORKFLOW.md` 9 žingsnyje (dydžio vartai): snap taškai ant spausdinto teksto, fill zonos tik ant to, kas nespausdinta.
Šis failas aprašo serijos dalį: skriptus, kurie dirba visoms knygoms, ir puslapius, kur bibliotekos deda ženklus.
Šį darbą daryti po `series_fix.py` ir `review_sheets.py`, prieš output.

## 1 dalis: rėmai, didesni už knygos medianą

1. Paleisti `python3 series/size_gate.py <series>`.
   - Pirma kiekvienai knygai paleidžiamas `pages_fit_text_column`: šoniniai kraštai pastumiami prie teksto skilties be modelio.
   - Paskui į `st-review/size-gate.json` įrašomi puslapiai, kurie lieka didesni už medianą, ir puslapiai, kur nukirpta daugiau nei 3 mm.
   - Kiekvienas puslapis nupiešiamas su puslapio px tinkleliu į `st-review/size-sheets/`.
2. Kiekviename puslapyje rasti pusę, kur antspaudas, lipdukas ar ranka rašytas tekstas padidino rėmą.
   Tašką ant tos pusės kraštinio spausdinto teksto nuskaityti iš artimo vaizdo:
   `python3 series/stamp_sheet.py <series> --only <book> --label <label> --roi x0,y0,x1,y1`.
3. Tikrai didesnis puslapis (titulinio paveikslas, lentelė, turinys) lieka toks, koks yra. Tai parašyti ataskaitoje.
4. Taškus surašyti į vieną JSON failą: `[{"book": "...", "page": "<label>", "x": 1428, "y": 2033, "side": "right"}]`.
5. Paleisti `python3 series/size_gate.py <series> --snap <points.json>`.
   Jis pasilieka `projektas.before-snap.ScanTailor`, prilipdo kraštus, paleidžia 4–5 filtrus ir iš naujo nupiešia puslapius (`*_after.png`).
6. Peržiūrėti „po“ vaizdus: rėmo kraštas yra ant spausdinto teksto, ir už rėmo neliko spausdinto teksto.
   Jei kraštas prilipo prie ne to rašalo, pasirinkti geresnį tašką ir prilipdyti dar kartą.

`size_gate.py` kiekvieną pakeistą knygą įrašo į `st-review/fit-books.txt`, nes knygai reikia pilno output.

## 2 dalis: bibliotekos ženklai

Šios serijos bibliotekos ženklus deda šešiuose puslapiuose: priešlapyje (grąžinimo lapelis), spausdintuose 3, 4 ir 17 puslapiuose, leidybos duomenų puslapyje (ranka rašyta kaina, brūkšninio kodo lipdukas) ir galiniame viršelyje.

### Rasti puslapius

1. Paleisti `python3 series/stamp_sheet.py <series> --only <book>`.
   Jis nupiešia šiuos šešis puslapius su puslapio px tinkleliu. Leidybos duomenų puslapis – paskutinis puslapis su turiniu prieš galinį viršelį.
   Rėmas žalias, o projekto fill zonos mėlynos.
2. Vaizde perskaityti spausdintą puslapio numerį. Jei tai ne tas puslapis, paleisti įrankį dar kartą su `--around 2` ir rasti tinkamą.
   - Jei spausdintas numeris vienu mažesnis už projekto puslapį, gal trūksta priekinio viršelio skenuotės. Tai parašyti knygos ataskaitoje.
   - Viršelio vaizdą iš knygyno įdėti su st-mcp `project_add_front_cover`. Tada knygos poslinkį `st-review/page-offset.json` nustatyti į 0.
   - Rastam puslapiui naudoti `--label <page label>`.
3. Į output patenka tik ta puslapio dalis, kuri yra rėme. Ženklų už žalio rėmo nepaisyti.
4. Grąžinimo lapelis priešlapyje: puslapį padaryti tuščią (st-mcp `page_set_content(mode="empty")`), kaip iš spaustuvės.

### Pasirinkti, patikrinti ir įrašyti zonas

1. Kiekvieną zoną `[x0, y0, x1, y1]` (puslapio px) nuskaityti iš artimo vaizdo (`--roi`). Uždengti visą ženklą su 5–10 px atsarga.
   Koordinačių neskaityti iš sumažintos miniatiūros. Grubų rėmą prie rašalo priglaudžia st-mcp `page_add_fill_zone(fit=true, dry_run=true)`.
2. Visas serijos zonas surašyti į vieną JSON failą: `[{"book": "...", "label": "...", "zones": [[x0, y0, x1, y1]]}]`.
   Lipdukui ant spalvoto galinio viršelio pridėti `"color": "#rrggbb"` su aplinkine viršelio spalva.
3. Paleisti `python3 series/add_zones.py <series> <zones.json>`. Jis įrašo patikros lapus į `st-review/zones-check/` ir projektų nekeičia.
4. Peržiūrėti kiekvieną lapą: raudona zona dengia ženklą, ir jokia spausdinta raidė nepatenka į raudoną zoną. Pataisyti JSON ir patikrinti dar kartą.
   Skriptas išvardija kiekvieną crossing: rašalą abipus zonos krašto. Šiuos puslapius peržiūrėti iš arti, nes crossing gali kirsti spausdintą tekstą.
5. Paleisti `python3 series/add_zones.py <series> <zones.json> --apply`.
   Jis pasilieka `projektas.before-zones.ScanTailor`, įrašo zonas ir prideda puslapius į `st-review/zones-pages.txt`.
6. Output leisti tik tada, kai žmogus liepia. Tada pridėti `--output`: jis paleidžia 5–6 filtrus tik šiems puslapiams.

## 3 dalis: output ir patikra po jo

1. Visus sąrašus sujungti į vieną paleidimą: `python3 series/output_script.py <series> <name> --full <books.txt> --pages <pages.txt>`.
   Jis įrašo `<name>.sh` (Linux CLI) ir `<name>.ps1` (Windows CLI) į `st-review`. Abu paleidžia 5–6 filtrus su `--output-project`.
   Vien 6 filtras puslapiams palieka seną puslapio dydį, todėl skriptai visada pradeda nuo 5 filtro.
2. Po paleidimo: `python3 series/output_check.py <series> --zones <zones.json>`.
   Jis išvardija knygas su trūkstamu ar neatsidarančiu TIFF arba su kito dydžio puslapiu, ir zonas, kurių nėra projekte.
   Knygai su kito dydžio puslapiu reikia pilno 5–6 filtrų paleidimo.
3. Žiūrėti į output, ne į projektą: `python3 series/out_sheet.py <series>` nupiešia kiekvienos knygos šešis puslapius iš output TIFF.
   Su `--zones <zones.json>` jis nupiešia tik to sąrašo puslapius.

## Ataskaita

Kiekvienai knygai nurodyti:
- puslapius su prilipdytu rėmu ir didesnius puslapius, kurie lieka;
- puslapius su zonomis ir ką jos dengia;
- ženklus, kurie liečia spausdintą tekstą (žmogui);
- knygas, kur laukiamas puslapis buvo ne savo projekto numeriu (gal trūksta viršelio).
