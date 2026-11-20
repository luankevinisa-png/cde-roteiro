"""Gera busca/lojas-cde.html (página de busca, dados embutidos) a partir de dados/lojas.csv.

Uso:  python busca/build_busca.py   (rode de novo depois de editar o CSV)
"""
import csv
import json
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

with open(ROOT / "dados" / "lojas.csv", encoding="utf-8-sig", newline="") as f:
    lojas = list(csv.DictReader(f, delimiter=";"))
assert lojas and "Nome" in lojas[0], "lojas.csv vazio ou sem a coluna Nome"

data = json.dumps(lojas, ensure_ascii=False).replace("</", "<\\/")  # nada no texto fecha o <script>
page = (ROOT / "busca" / "template.html").read_text(encoding="utf-8")
page = page.replace("__DADOS__", data).replace("__GERADO__", date.today().strftime("%d/%m/%Y"))
(ROOT / "busca" / "lojas-cde.html").write_text(page, encoding="utf-8")
(ROOT / "docs").mkdir(exist_ok=True)  # GitHub Pages publica a pasta docs/
(ROOT / "docs" / "index.html").write_text(
    '<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
    "<style>body{margin:0}[hidden]{display:none!important}</style></head><body>" + page + "</body></html>", encoding="utf-8")
print(f"{len(lojas)} lojas -> busca/lojas-cde.html e docs/index.html")
