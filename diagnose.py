"""Testa as APIs dos museus direto da sua máquina, sem o resto do projeto.

Uso:  python diagnose.py

Para cada museu mostra o código HTTP com três User-Agents diferentes. Para os
museus novos (SMK e V&A) também lista os campos do primeiro resultado, o que
confirma se os nomes de campos usados no main.py são os da API de verdade.
"""
import requests

TESTS = {
    # A busca v1 do Met foi aposentada em 1/10/2026 (HTTP 410); a v1.1 é a atual.
    "The Met (busca v1.1)": "https://collectionapi.metmuseum.org/public/collection/v1.1/search?q=monet&hasImages=true&limit=3",
    "Art Institute of Chicago": "https://api.artic.edu/api/v1/artworks/search?q=monet&limit=1&fields=id,title,image_id",
    "Cleveland": "https://openaccess-api.clevelandart.org/api/artworks/?q=monet&limit=1&has_image=1",
    "SMK (Dinamarca)": "https://api.smk.dk/api/v1/art/search/?keys=monet&rows=1&lang=en&filters=[has_image:true],[public_domain:true]",
    "Victoria and Albert": "https://api.vam.ac.uk/v2/objects/search?q=monet&page_size=1&images_exist=1",
}
# Onde fica a lista de resultados em cada resposta (para mostrar os campos).
RESULT_LIST = {"SMK (Dinamarca)": "items", "Victoria and Albert": "records"}

USER_AGENTS = {
    "padrão do requests": None,
    "Artlog (ASCII)": "Artlog/1.1 (personal art catalog project)",
    "navegador (Chrome)": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}

for museum, url in TESTS.items():
    print(f"\n=== {museum}")
    last_ok = None
    for label, agent in USER_AGENTS.items():
        headers = {"User-Agent": agent} if agent else {}
        if "artic.edu" in url:
            headers["AIC-User-Agent"] = "Artlog (personal project)"
        try:
            res = requests.get(url, headers=headers, timeout=10)
            body = " ".join(res.text[:100].split())
            print(f"  {label:<22} HTTP {res.status_code}  {res.headers.get('content-type', '?')[:28]:<28} {body}")
            if res.status_code == 200:
                last_ok = res
        except requests.RequestException as exc:
            print(f"  {label:<22} ERRO DE REDE: {exc.__class__.__name__}: {exc}")

    list_key = RESULT_LIST.get(museum)
    if list_key and last_ok is not None:
        try:
            items = last_ok.json().get(list_key) or []
        except ValueError:
            items = []
        if items:
            print(f"  campos do primeiro resultado ({list_key}[0]):")
            print("   ", ", ".join(sorted(items[0].keys())))
        else:
            print(f"  (200, mas sem '{list_key}' na resposta: a estrutura pode ser outra)")

# Imagem do Art Institute: precisa do Referer do site deles.
print("\n=== Imagem do Art Institute (com e sem Referer)")
try:
    art = requests.get(
        "https://api.artic.edu/api/v1/artworks/search?q=monet&limit=1&fields=image_id",
        headers={"AIC-User-Agent": "Artlog (personal project)"},
        timeout=10,
    ).json()
    image_id = art["data"][0]["image_id"]
    image_url = f"https://www.artic.edu/iiif/2/{image_id}/full/843,/0/default.jpg"
    for label, extra in {"sem Referer": {}, "com Referer": {"Referer": "https://www.artic.edu/"}}.items():
        res = requests.get(image_url, headers=extra, timeout=10)
        print(f"  {label:<14} HTTP {res.status_code}  {res.headers.get('content-type', '?')}")
except Exception as exc:  # diagnóstico: mostra qualquer falha
    print(f"  não foi possível testar: {exc.__class__.__name__}: {exc}")