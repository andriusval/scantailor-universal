# st-prepare-books

Paketiniai skriptai: kiekvienam knygos katalogui po šakniniu katalogu sukuria ScanTailor projektą, o vėliau padaro output.

- `st-prepare-books.ps1`: Windows PowerShell. Dėti šalia portable buildo `windows` katalogo, nes jis naudoja `windows\scantailor-universal-cli.exe`.
- `st-prepare-books.sh`: bash 4 ar naujesnis, tos pačios parinktys ir failai. CLI randa taip pat kaip st-mcp (`ST_CLI`, šios repo build katalogas arba `PATH`). Reikia `python3` su Pillow.

## Žingsniai

1. Be parinkčių skriptas kiekvienam knygos katalogui be projekto sukuria `<book>/projektas.ScanTailor` su 1–5 filtrais. Output vaizdų nerašo.
2. Peržiūrėti ir pataisyti projektus (GUI arba st-mcp).
3. Su `-OutputOnly` (`--output-only`) skriptas kiekvienam projektui paleidžia 5–6 filtrus.
   - CLI perrašo tik pakeistus puslapius. Jis lygina output nustatymus, fill zonas ir output failo dydį su vertėmis, kurias projektas saugo iš paskutinio output.
   - CLI šias vertes įrašo į projektą (`--output-project`), todėl kitas paleidimas gali puslapį praleisti. Projektas be šių verčių vieną kartą gauna visus puslapius.
   - Su `-Force` (`--force`) skriptas pirma pašalina šias vertes ir pasilieka kopiją `projektas.ScanTailor.before-force-<laikas>`. Tada CLI perrašo visus puslapius.

Žurnalai: `<root>/st-prepare-logs`, po vieną kiekvienai knygai ir `summary.csv`. Kiekvienos knygos pastaboje parašyta, kiek output failų perrašyta.
