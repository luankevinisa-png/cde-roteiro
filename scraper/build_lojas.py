"""Monta dados/lojas.csv com as lojas de Ciudad del Este do OpenStreetMap (grátis, sem chave).

Uso:  python scraper/build_lojas.py
Consulta a API Overpass; cada execução pega os dados mais novos do OSM.
"""
import csv
import json
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from math import asin, cos, radians, sin, sqrt
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "dados"
SERVERS = ["https://overpass-api.de/api/interpreter", "https://overpass.private.coffee/api/interpreter",
           "https://overpass.kumi.systems/api/interpreter"]
AREA = 'area["name"="Ciudad del Este"]["boundary"="administrative"]->.a;'
COLS = ["Nome", "Nicho", "Galeria", "Andar", "Box", "Endereço", "Latitude", "Longitude",
        "Horário", "Telefone", "WhatsApp", "Observação"]
MERGE_M = 150  # mesmo nome a até 150 m = mesmo lugar marcado mais de uma vez (ex.: Shopping China)

NICHO = {
    "yes": "Loja", "clothes": "Roupas", "mall": "Shopping/Galeria", "convenience": "Conveniência",
    "car_repair": "Oficina", "car": "Carros", "hardware": "Ferragens", "supermarket": "Supermercado",
    "hairdresser": "Cabeleireiro", "electronics": "Eletrônicos", "car_parts": "Autopeças", "bakery": "Padaria",
    "beauty": "Beleza", "butcher": "Açougue", "books": "Livraria", "tyres": "Pneus", "optician": "Ótica",
    "shoes": "Calçados", "beverages": "Bebidas", "mobile_phone": "Celulares", "motorcycle": "Motos",
    "jewelry": "Joias", "furniture": "Móveis", "bicycle": "Bicicletas", "travel_agency": "Agência de viagem",
    "stationery": "Papelaria", "houseware": "Utilidades domésticas", "sports": "Esportes", "general": "Variedades",
    "copyshop": "Copiadora", "greengrocer": "Hortifruti", "laundry": "Lavanderia", "trade": "Atacado",
    "department_store": "Departamentos", "pet": "Pet shop", "agrarian": "Agropecuária", "alcohol": "Bebidas",
    "marketplace": "Mercado", "paint": "Tintas", "computer": "Informática", "cosmetics": "Cosméticos",
    "perfumery": "Perfumaria", "seafood": "Peixaria", "toys": "Brinquedos", "building_materials": "Materiais de construção",
    "tiles": "Pisos", "appliance": "Eletrodomésticos", "chemist": "Farmácia", "ticket": "Passagens",
    "wholesale": "Atacado", "bag": "Bolsas", "watches": "Relógios", "variety_store": "Variedades",
    "gift": "Presentes", "music": "Música", "musical_instrument": "Instrumentos musicais", "photo": "Fotografia",
    "video_games": "Video games", "fabric": "Tecidos", "boutique": "Roupas", "tobacco": "Tabacaria",
    "kiosk": "Banca", "money_lender": "Câmbio", "pharmacy": "Farmácia", "medical_supply": "Material médico",
    "florist": "Floricultura", "fishing": "Pesca", "outdoor": "Aventura", "electrical": "Material elétrico",
    "baby_goods": "Bebê", "e-cigarette": "Cigarro eletrônico", "hifi": "Áudio", "bed": "Cama, mesa e banho",
}
DAYS = {"Mo": "Seg", "Tu": "Ter", "We": "Qua", "Th": "Qui", "Fr": "Sex", "Sa": "Sáb", "Su": "Dom", "PH": "Feriado"}


def overpass(query):
    """Servidores públicos do Overpass às vezes respondem 502/504 quando lotados: tenta de novo e troca de servidor."""
    erro = None
    for tentativa in range(3):
        for url in SERVERS:
            req = urllib.request.Request(url, data=urllib.parse.urlencode({"data": query}).encode(),
                                         headers={"User-Agent": "cde-roteiro/1.0 (uso pessoal)"})
            try:
                with urllib.request.urlopen(req, timeout=180) as r:
                    return json.load(r)
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
                erro = e
                print(f"  {url}: {e} — tentando de novo", file=sys.stderr)
        time.sleep(15 * (tentativa + 1))
    raise SystemExit(f"Overpass fora do ar agora ({erro}). Tente de novo em alguns minutos.")


def meters(a, b):
    la1, lo1, la2, lo2 = map(radians, (a[0], a[1], b[0], b[1]))
    h = sin((la2 - la1) / 2) ** 2 + cos(la1) * cos(la2) * sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371000 * asin(sqrt(h))


def inside(pt, poly):
    """Ponto (lat, lon) dentro do polígono [(lat, lon), ...] — ray casting."""
    y, x = pt
    res = False
    for (y1, x1), (y2, x2) in zip(poly, poly[1:] + poly[:1]):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            res = not res
    return res


def norm(s):
    return " ".join(re.findall(r"[a-z0-9]+", "".join(
        c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")))


def andar(t):
    v = (t.get("level") or t.get("addr:floor") or "").split(";")[0].strip()
    if not v:
        return ""
    if v in ("0", "PB", "pb") or norm(v) in ("planta baja", "terreo"):
        return "Térreo"
    if re.fullmatch(r"-\d+", v):
        return "Subsolo"
    m = re.match(r"(\d+)", v)
    return f"{int(m.group(1))}º andar" if m else v


def horario(oh):
    s = re.sub(r"\b(Mo|Tu|We|Th|Fr|Sa|Su|PH)\b", lambda m: DAYS[m.group(1)], oh or "")
    return re.sub(r"\boff\b", "fechado", s)


def pos(e):
    return (e["lat"], e["lon"]) if "lat" in e else (e["center"]["lat"], e["center"]["lon"])


def self_check():
    sq = [(0, 0), (0, 1), (1, 1), (1, 0)]
    assert inside((0.5, 0.5), sq) and not inside((1.5, 0.5), sq)
    assert andar({"level": "0"}) == "Térreo" and andar({"level": "2"}) == "2º andar" and andar({}) == ""
    assert horario("Mo-Fr 07:00-16:00; Sa 07:00-12:00; Su off") == "Seg-Sex 07:00-16:00; Sáb 07:00-12:00; Dom fechado"
    assert 1000 < meters((-25.5106, -54.6093), (-25.5106, -54.6193)) < 1010


def main():
    self_check()
    OUT.mkdir(exist_ok=True)
    shops = overpass(f'[out:json][timeout:120];{AREA}(nwr["shop"]["name"](area.a);'
                     f'nwr["amenity"="marketplace"]["name"](area.a););out tags center;')["elements"]
    malls = overpass(f'[out:json][timeout:120];{AREA}way["shop"="mall"]["name"](area.a);out geom tags;')["elements"]
    polys = [(m["tags"]["name"], [(p["lat"], p["lon"]) for p in m["geometry"]]) for m in malls if m.get("geometry")]
    print(f"{len(shops)} lojas com nome no OSM, {len(polys)} contornos de shopping/galeria", file=sys.stderr)

    rows = []
    for e in shops:
        t = e["tags"]
        lat, lon = pos(e)
        tipo = t.get("shop") or t.get("amenity")
        if tipo == "mall":
            galeria = t["name"]
        else:  # loja dentro do contorno de um shopping = loja daquela galeria
            galeria = next((n for n, p in polys if norm(n) != norm(t["name"]) and inside((lat, lon), p)), "")
        rua = ", ".join(x for x in (t.get("addr:street"), t.get("addr:housenumber")) if x)
        rows.append({
            "Nome": t["name"], "Nicho": NICHO.get(tipo, tipo.replace("_", " ").capitalize()),
            "Galeria": galeria, "Andar": andar(t), "Box": t.get("addr:unit", ""), "Endereço": rua,
            "Latitude": f"{lat:.6f}", "Longitude": f"{lon:.6f}", "Horário": horario(t.get("opening_hours")),
            "Telefone": t.get("phone") or t.get("contact:phone", ""),
            "WhatsApp": re.sub(r"\D", "", t.get("contact:whatsapp", "")),
            "Observação": f"osm.org/{e['type']}/{e['id']}", "_score": len(t),
        })

    # junta marcações repetidas do mesmo lugar; fica a mais completa e herda o que faltar
    rows.sort(key=lambda r: -r["_score"])
    kept, merged = [], []
    for r in rows:
        twin = next((k for k in kept if norm(k["Nome"]) == norm(r["Nome"]) and
                     meters((float(k["Latitude"]), float(k["Longitude"])),
                            (float(r["Latitude"]), float(r["Longitude"]))) <= MERGE_M), None)
        if twin:
            for c in COLS:
                twin[c] = twin[c] or r[c]
            merged.append((twin["Nome"], r["Observação"], twin["Observação"]))
        else:
            kept.append(r)

    kept.sort(key=lambda r: (r["Galeria"] or "~", r["Nome"].lower()))
    with open(OUT / "lojas.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, COLS, delimiter=";", extrasaction="ignore")
        w.writeheader()
        w.writerows(kept)

    rep = [f"# Lojas do OpenStreetMap em Ciudad del Este ({len(kept)})", "",
           f"## Marcações repetidas que foram juntadas ({len(merged)})", "",
           "Mesmo nome a até 150 m. A coluna Observação do CSV tem o link da marcação mantida.", ""]
    rep += [f"- {n}: {a} juntada em {b}" for n, a, b in merged] or ["- nenhuma"]
    (OUT / "relatorio_osm.md").write_text("\n".join(rep) + "\n", encoding="utf-8")

    c = lambda col: sum(1 for r in kept if r[col])
    print(json.dumps({"lojas": len(kept), "juntadas": len(merged), "com_galeria": c("Galeria"), "com_andar": c("Andar"),
                      "com_horario": c("Horário"), "com_telefone": c("Telefone"), "com_whatsapp": c("WhatsApp"),
                      "com_rua": c("Endereço"),
                      "nichos_top": Counter(r["Nicho"] for r in kept).most_common(8)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
