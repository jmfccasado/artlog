"""Descobre por que o Smithsonian não responde no Artlog.

Uso (na pasta do projeto, com o venv ativo):
    python check_smithsonian.py
    python check_smithsonian.py "grant wood"

Faz 4 pedidos diretos à API e mostra o código HTTP e o começo da resposta de cada um.
A chave nunca é impressa. Comparando os testes dá para separar:
  - problema na chave (o teste 1 falha);
  - problema no jeito de enviar a chave (o 1 falha e o 2 funciona);
  - problema no filtro por museus de arte que o app usa (o 1 funciona e o 3 falha ou vem vazio).
"""
from __future__ import annotations

import sys

import requests

import main  # carrega o .env do projeto, igual ao app

QUERY = sys.argv[1] if len(sys.argv) > 1 else "sunflowers"
KEY = main.os.environ.get("SI_API_KEY", "").strip()
BASE = "https://api.si.edu/openaccess/api/v1.0"
UNITS = " OR ".join(f"unit_code:{u}" for u in main.SI_ART_UNITS)


def show(label: str, url: str, *, params: dict, headers: dict | None = None) -> tuple[int | None, int | None]:
    print(f"\n[{label}]")
    try:
        res = requests.get(
            url, params=params, headers={"Accept": "application/json", **(headers or {})}, timeout=20
        )
    except requests.RequestException as exc:
        print(f"  erro de rede: {exc.__class__.__name__}")
        return None, None
    body = res.text.replace(KEY, "***") if KEY else res.text
    rows = None
    try:
        rows = len(res.json().get("response", {}).get("rows", []))
    except Exception:
        pass
    print(f"  HTTP {res.status_code}" + (f" | {rows} resultado(s)" if rows is not None else ""))
    if res.status_code != 200 or rows == 0:
        print("  resposta:", " ".join(body[:300].split()))
    return res.status_code, rows


def main_check() -> None:
    if not KEY:
        print("SI_API_KEY NÃO está definida. O app nem chega a consultar o Smithsonian.")
        print("Confira o arquivo .env (na mesma pasta do main.py) ou o export no terminal.")
        return
    print(f"SI_API_KEY definida ({len(KEY)} caracteres; uma chave do api.data.gov tem 40).")

    s1, r1 = show("1. chave no cabeçalho, busca simples", f"{BASE}/search",
                  params={"q": QUERY, "rows": 3}, headers={"X-Api-Key": KEY})
    s2, r2 = show("2. chave na URL (api_key), busca simples", f"{BASE}/search",
                  params={"q": QUERY, "rows": 3, "api_key": KEY})
    s3, r3 = show("3. a busca exata que o app faz (com filtro de museus de arte)", f"{BASE}/search",
                  params={"q": f"{QUERY} AND ({UNITS})", "rows": 3, "media_type": "Images"},
                  headers={"X-Api-Key": KEY})
    s4, r4 = show("4. endpoint de categoria 'art_design' (alternativa documentada)",
                  f"{BASE}/category/art_design/search", params={"q": QUERY, "rows": 3},
                  headers={"X-Api-Key": KEY})

    print("\n=== Diagnóstico")
    if s1 is None:
        print("  Sem conexão com api.si.edu agora. Tente de novo.")
    elif s1 != 200 and s2 == 200:
        print("  A chave vale, mas só funciona na URL: o app precisa enviar como api_key. Me avise.")
    elif s1 in (401, 403):
        print("  A chave foi recusada. Veja o texto da resposta acima:")
        print("  - API_KEY_UNVERIFIED: abra o e-mail do cadastro e confirme a chave.")
        print("  - API_KEY_INVALID: a chave foi copiada errada; confira o .env.")
        print("  - OVER_RATE_LIMIT: muitos pedidos; espere um pouco.")
    elif s1 != 200:
        print(f"  A busca simples falhou com HTTP {s1}. Cole aqui a resposta acima.")
    elif s3 != 200 or not r3:
        print("  A chave funciona, mas o filtro que o app usa falha ou vem vazio.")
        if s4 == 200 and r4:
            print("  O endpoint 'art_design' funciona: me avise que eu troco o app para ele.")
        else:
            print("  Cole esta saída aqui que eu ajusto o filtro.")
    else:
        print("  Tudo certo com a chave e com a busca do app.")
        print("  Se o site ainda mostra erro, o servidor está rodando com a configuração antiga:")
        print("  pare (Ctrl+C) e rode `python main.py` de novo.")


if __name__ == "__main__":
    main_check()