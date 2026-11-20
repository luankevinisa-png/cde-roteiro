"""Monta dados/lojas.csv a partir do Compras Paraguai (lojas de Ciudad del Este).

Uso:  python scraper/build_lojas.py
Cache do HTML em scraper/cache/ (apague um arquivo para baixar de novo).
Só biblioteca padrão: o site entrega o HTML pronto (sem precisar de Playwright).
"""
import csv
import difflib
import html
import json
import random
import re
import sys
import time
import unicodedata
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

BASE = "https://www.comprasparaguai.com.br"
ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "scraper" / "cache"
OUT = ROOT / "dados"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
COLS = ["Nome", "Nicho", "Galeria", "Andar", "Box", "Endereço", "Latitude", "Longitude",
        "Horário", "Telefone", "WhatsApp", "Observação"]
CITY = "Ciudad del Este"
GALERIA_RE = re.compile(r"\b(shopping|galer[ií]a|edif[ií]cio|centro comercial|mall|plaza|torre|center)\b", re.I)
# palavras que não distinguem uma galeria da outra ("Lai Lai Center" = "Shopping Lai Lai")
GENERIC = {"shopping", "galeria", "edificio", "centro", "comercial", "center", "mall"}


def fetch(url, name):
    path = CACHE / name
    if path.exists():
        return path.read_text(encoding="utf-8")
    time.sleep(random.uniform(1, 2))
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "pt-BR"})
    with urllib.request.urlopen(req, timeout=30) as r:
        text = r.read().decode("utf-8")
    path.write_text(text, encoding="utf-8")
    return text


def text_of(fragment):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def strip_accents(s):
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def galeria_key(name):
    words = re.findall(r"[a-z0-9]+", strip_accents(name.lower()))
    return " ".join(w for w in words if w not in GENERIC)


def parse_andar(part):
    """'4° andar' -> '4º andar'; 'Térreo' -> 'Térreo'; '' se não for andar."""
    p = strip_accents(part.lower()).strip()
    m = re.search(r"(\d+)\s*(?:°|º|o|ª)?\s*(?:andar|piso)", p) or re.search(r"(?:andar|piso)\s*(\d+)", p)
    if m:
        return f"{int(m.group(1))}º andar"
    for word, label in (("terreo", "Térreo"), ("planta baja", "Térreo"), ("subsolo", "Subsolo"),
                        ("mezanino", "Mezanino"), ("sobreloja", "Sobreloja")):
        if word in p:
            return label
    return ""


def list_slugs():
    slugs, page = [], 1
    while True:
        h = fetch(f"{BASE}/lojas/?city=ciudad-del-este&page={page}", f"lista_{page}.html")
        found = [s for s in dict.fromkeys(re.findall(r'href="/lojas/([a-z0-9-]+)/"', h)) if s not in slugs]
        if not found:
            return slugs
        slugs += found
        page += 1


def parse_store(slug, h):
    store = {}
    for m in re.finditer(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', h, re.S):
        data = json.loads(m.group(1))
        if data.get("@type") == "Store":
            store = data
    name = store.get("name") or text_of(re.search(r"<h1[^>]*>(.*?)</h1>", h, re.S).group(1))
    header = h[:h.find("<h1")]  # badges "Loja de ..." ficam antes do <h1>; depois vêm lojas relacionadas
    nichos = [text_of(n) for n in re.findall(r">Loja de ([^<]+)</span>", header)]
    wa = re.search(r"api\.whatsapp\.com/send\?phone=(\d+)", h)
    base = {"Nome": name, "Nicho": ", ".join(dict.fromkeys(nichos)), "Telefone": store.get("telephone", ""),
            "WhatsApp": wa.group(1) if wa else ""}

    sec = re.search(r'id="enderecos"(.*?)</section>', h, re.S)
    blocks = re.split(r"(?=<h3)", sec.group(1))[1:] if sec else []
    rows = []
    for i, b in enumerate(blocks):
        before = blocks[i - 1] if i else sec.group(1).split("<h3")[0]
        city = re.findall(r"<span[^>]*>([^<]+)</span></div>\s*$", before)  # cidade vem logo antes do <h3>
        if city and text_of(city[-1]) != CITY:
            continue
        title = text_of(re.search(r"<h3[^>]*>(.*?)</h3>", b, re.S).group(1))
        hours = "; ".join(f"{text_of(d)} {text_of(t).replace(' ', '')}" for d, t in
                          re.findall(r"<li[^>]*>.*?<span[^>]*>(.*?)</span><span[^>]*>(.*?)</span></li>", b, re.S))
        full = re.search(r'<span class="min-w-0 flex-1">([^<]*)</span></span><span[^>]*>Endereço completo', b)
        full = text_of(full.group(1)) if full else ""
        geo = re.search(r"query=(-?\d+\.\d+),(-?\d+\.\d+)", b)
        rows.append({**base, **split_address(title, full), "Horário": hours,
                     "Latitude": geo.group(1) if geo else "", "Longitude": geo.group(2) if geo else ""})
    if not rows:  # sem seção de endereço: usa o JSON-LD
        addr = store.get("address", {}).get("streetAddress", "")
        geo = store.get("geo", {})
        rows.append({**base, **split_address("", addr), "Horário": "; ".join(translate_days(h) for h in store.get("openingHours", [])),
                     "Latitude": str(geo.get("latitude", "")), "Longitude": str(geo.get("longitude", ""))})
    for i, r in enumerate(rows):
        r["Observação"] = f"Filial {i + 1} de {len(rows)}" if len(rows) > 1 else ""
        r["slug"] = slug
    return rows


DAYS = {"Mo": "Segunda", "Tu": "Terça", "We": "Quarta", "Th": "Quinta", "Fr": "Sexta", "Sa": "Sábado", "Su": "Domingo"}


def translate_days(spec):
    """'Mo-Fr 07:00-16:00' -> 'Segunda a Sexta 07:00-16:00' (formato schema.org do JSON-LD)."""
    days, _, hours = spec.partition(" ")
    return " a ".join(DAYS.get(d, d) for d in days.split("-")) + " " + hours


def split_address(title, full):
    parts = [p.strip() for p in full.split(",") if p.strip() and p.strip() != CITY]
    galeria = andar = ""
    rest = []
    for p in parts:
        a = parse_andar(p)
        if a and not andar and len(p) < 25:
            andar = a
        elif not galeria and not rest and GALERIA_RE.search(p):
            galeria = p
        else:
            rest.append(p)
    if not galeria and GALERIA_RE.search(title):
        galeria = title
    return {"Galeria": galeria, "Andar": andar, "Box": "", "Endereço": ", ".join(rest)}


def normalize_galerias(rows, reference):
    """Une variações pela chave sem palavras genéricas; nome final = referência do site ou variação mais comum."""
    ref = {}
    for n in reference:  # chave repetida na referência (Galeria X / Shopping X) = ambígua, não une sozinho
        ref[galeria_key(n)] = None if galeria_key(n) in ref else n
    variants = defaultdict(Counter)
    for r in rows:
        if r["Galeria"]:
            variants[galeria_key(r["Galeria"])][r["Galeria"]] += 1
    canon = {k: ref.get(k) or c.most_common(1)[0][0] for k, c in variants.items()}
    exact = set(reference)
    for r in rows:
        # nome idêntico ao da referência fica como está: "Galeria Antonio" e "Shopping Antonio" são lugares diferentes
        if r["Galeria"] and r["Galeria"] not in exact:
            r["Galeria"] = canon[galeria_key(r["Galeria"])]
    return variants, canon, ref


def reference_names():
    h = fetch(f"{BASE}/cidades/ciudad-del-este/shoppings-ruas/", "shoppings_ruas.html")
    sec = h[h.find(">Shoppings<"):h.find(">Ruas com comércios<")]
    return [text_of(x) for x in re.findall(r"<a[^>]*>(.*?)</a>", sec, re.S) if text_of(x)]


def self_check():
    assert parse_andar("4° andar") == "4º andar"
    assert parse_andar("Térreo") == "Térreo"
    assert parse_andar("Av. Adrian Jara") == ""
    assert translate_days("Mo-Fr 07:00-16:00") == "Segunda a Sexta 07:00-16:00"
    rows = [{"Galeria": g} for g in ("Galeria Antonio", "Shopping Antonio", "Lai Lai Center")]
    normalize_galerias(rows, ["Galeria Antonio", "Shopping Antonio", "Shopping Lai Lai Center"])
    assert [r["Galeria"] for r in rows] == ["Galeria Antonio", "Shopping Antonio", "Shopping Lai Lai Center"], rows
    assert galeria_key("Shopping Lai Lai") == galeria_key("Lai Lai Center") == "lai lai"
    a = split_address("Shopping Lai Lai Center", "Shopping Lai Lai Center, 4° andar, Av. Adrian Jara, Ciudad del Este")
    assert a == {"Galeria": "Shopping Lai Lai Center", "Andar": "4º andar", "Box": "", "Endereço": "Av. Adrian Jara"}, a
    a = split_address("Rua Nanawa", "Av. Nanawa, 123, Ciudad del Este")
    assert a["Galeria"] == "" and a["Endereço"] == "Av. Nanawa, 123", a


def main():
    self_check()
    CACHE.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(exist_ok=True)
    reference = reference_names()
    slugs = list_slugs()
    print(f"{len(slugs)} lojas na listagem", file=sys.stderr)
    rows = []
    for i, slug in enumerate(slugs, 1):
        try:
            rows += parse_store(slug, fetch(f"{BASE}/lojas/{slug}/", f"loja_{slug}.html"))
        except Exception as e:  # uma página quebrada não derruba o resto; vai para o relatório
            rows.append({c: "" for c in COLS} | {"Nome": slug, "slug": slug, "Observação": f"ERRO: {e}"})
        if i % 20 == 0:
            print(f"  {i}/{len(slugs)}", file=sys.stderr)

    variants, canon, ref = normalize_galerias(rows, reference)
    with open(OUT / "lojas.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, COLS, delimiter=";", extrasaction="ignore")
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: (r["Galeria"] or "~", r["Nome"].lower())))

    rep = ["# Variações de nome de galeria", "",
           "Grupos com mais de uma grafia. Cada grupo virou um nome só (o da página de shoppings do site,",
           "quando existe). Nomes idênticos aos da referência nunca são trocados.", ""]
    for k, c in sorted(variants.items(), key=lambda kv: canon[kv[0]]):
        if len(c) == 1 or all(v in reference for v in c):  # grafias oficiais distintas não foram unidas
            continue
        origem = "referência do site" if ref.get(k) else "não está na referência"
        rep.append(f"- **{canon[k]}** ({sum(c.values())} lojas, {origem}): "
                   + ", ".join(f"{v} ({n})" for v, n in c.most_common()))
    names = sorted({r["Galeria"] for r in rows if r["Galeria"]})
    close = [(a, b) for i, a in enumerate(names) for b in names[i + 1:]
             if difflib.SequenceMatcher(None, galeria_key(a), galeria_key(b)).ratio() > 0.8]
    if len(rep) == 5:
        rep.append("- nenhum")
    rep += ["", "## Nomes parecidos que NÃO foram unidos (confira)", ""]
    rep += [f"- {a}  ≈  {b}" for a, b in close] or ["- nenhum"]
    (OUT / "relatorio_galerias.md").write_text("\n".join(rep) + "\n", encoding="utf-8")

    sem_coord = [r for r in rows if not r["Latitude"]]
    sem_gal = [r for r in rows if not r["Galeria"]]
    rep = [f"# Lojas sem coordenada ({len(sem_coord)})", ""]
    rep += [f"- {r['Nome']} — {r['Endereço'] or '(sem endereço)'} — {BASE}/lojas/{r['slug']}/ {r['Observação']}"
            for r in sem_coord]
    rep += ["", f"# Lojas sem galeria ({len(sem_gal)}) — lojas de rua ou endereço incompleto", ""]
    rep += [f"- {r['Nome']} — {r['Endereço'] or '(sem endereço)'} — {BASE}/lojas/{r['slug']}/" for r in sem_gal]
    (OUT / "relatorio_pendencias.md").write_text("\n".join(rep) + "\n", encoding="utf-8")

    print(json.dumps({"lojas_listagem": len(slugs), "linhas_csv": len(rows),
                      "filiais_extras": len(rows) - len({r["slug"] for r in rows}),
                      "sem_coordenada": len(sem_coord), "sem_galeria": len(sem_gal),
                      "galerias": len(names), "grupos_com_variacao": sum(len(c) > 1 for c in variants.values()),
                      "erros": sum(r["Observação"].startswith("ERRO") for r in rows)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
