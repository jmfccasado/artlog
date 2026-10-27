"""Descobre por que a busca do Louvre não funciona no Artlog.

Uso (com o venv ativo; pode rodar de qualquer pasta):
    python check_louvre.py

Faz três coisas e mostra o resultado de cada uma:
  1. Testa se a página de busca do site realmente usa o termo pesquisado. Se três
     termos diferentes devolvem a MESMA lista, o servidor está ignorando o termo
     (a busca é feita por JavaScript no navegador) e ler o HTML não adianta.
  2. Procura no HTML pistas de como a busca funciona de verdade: formulários,
     nomes de campos, endereços de scripts e chamadas fetch/ajax/api.
  3. Confere o JSON de uma obra conhecida (Vénus de Milo), que é a parte que o
     app usa depois de achar os identificadores.

Não usa nenhuma chave e não grava nada. Cole a saída inteira na conversa.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser

import requests

SITE = "https://collections.louvre.fr"
SEARCH_URL = f"{SITE}/en/recherche"
SAMPLE_ARK = "cl010277627"  # Vénus de Milo
ARK_LINK = re.compile(r"/ark:/53355/(cl\d{6,})")
HINTS = re.compile(r"""(fetch\s*\(|XMLHttpRequest|axios|\$\.ajax|\.getJSON|/api/|/ajax/|autocomplet|data-url|data-href|data-search)""", re.I)

BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
HEADERS = {"User-Agent": BROWSER_UA, "Accept": "text/html,application/json;q=0.9,*/*;q=0.8"}


class PageScan(HTMLParser):
    """Coleta formulários, campos, scripts e linhas de tabela do HTML."""

    def __init__(self) -> None:
        super().__init__()
        self.forms: list[dict[str, str]] = []
        self.fields: list[str] = []
        self.scripts: list[str] = []
        self.inline_scripts: list[str] = []
        self.rows = 0
        self._in_script = False
        self._buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: (v or "") for k, v in attrs}
        if tag == "form":
            self.forms.append({"action": a.get("action", "(vazio)"), "method": a.get("method", "get").lower()})
        elif tag in ("input", "select", "textarea") and a.get("name"):
            self.fields.append(f"{tag}[name={a['name']}]" + (f" type={a['type']}" if a.get("type") else ""))
        elif tag == "script":
            if a.get("src"):
                self.scripts.append(a["src"])
            else:
                self._in_script, self._buffer = True, []
        elif tag == "tr":
            self.rows += 1

    def handle_data(self, data: str) -> None:
        if self._in_script:
            self._buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._in_script:
            self.inline_scripts.append("".join(self._buffer))
            self._in_script = False


def fetch(url: str, params: dict | None = None) -> requests.Response | None:
    try:
        return requests.get(url, params=params, headers=HEADERS, timeout=20)
    except requests.RequestException as exc:
        print(f"  erro de rede: {exc.__class__.__name__}")
        return None


def search_page(label: str, params: dict) -> tuple[list[str], int, str] | None:
    res = fetch(SEARCH_URL, params)
    if res is None:
        return None
    arks = list(dict.fromkeys(ARK_LINK.findall(res.text)))
    print(f"  {label:<18} HTTP {res.status_code} | {len(res.text):>7} bytes | {len(arks):>2} links de obra | {arks[:3]}")
    return arks, res.status_code, res.text


def main_check() -> None:
    print("=== 1. A página de busca usa o termo pesquisado?")
    tests = {
        "q=monet": {"q": "monet"},
        "q=zzzzqqqq": {"q": "zzzzqqqq"},
        "terms=monet": {"terms": "monet"},
        "(sem termo)": {},
    }
    results = {name: search_page(name, params) for name, params in tests.items()}
    got = {name: r for name, r in results.items() if r}
    if not got:
        print("  Sem conexão com o site agora. Tente de novo.")
        return

    first_page_html = next(iter(got.values()))[2]
    blocked = [n for n, r in got.items() if r[1] != 200]
    distinct = {tuple(r[0]) for r in got.values()}

    print("\n=== 2. Pistas de como a busca funciona de verdade")
    scan = PageScan()
    scan.feed(first_page_html)
    print(f"  linhas de tabela no HTML: {scan.rows}")
    print(f"  formulários: {scan.forms or 'nenhum'}")
    print(f"  campos nomeados: {scan.fields[:12] or 'nenhum'}")
    print(f"  scripts externos: {scan.scripts[:8] or 'nenhum'}")
    hits = []
    for code in [first_page_html] + scan.inline_scripts:
        for m in HINTS.finditer(code):
            start = max(0, m.start() - 60)
            hits.append(" ".join(code[start : m.end() + 90].split()))
    for snippet in list(dict.fromkeys(hits))[:8]:
        print(f"  pista: ...{snippet}...")
    if not hits:
        print("  nenhuma chamada fetch/ajax/api encontrada no HTML da página.")

    print("\n=== 3. JSON de uma obra conhecida (Vénus de Milo)")
    obj = fetch(f"{SITE}/ark:/53355/{SAMPLE_ARK}.json")
    obj_ok = False
    if obj is not None:
        try:
            data = obj.json()
            images = data.get("image") or []
            first = images[0] if images else {}
            print(f"  HTTP {obj.status_code} | título: {data.get('title')!r} | {len(images)} imagem(ns)")
            print(f"  1ª imagem: {first.get('urlThumbnail')}")
            print(f"  crédito: {first.get('copyright')}")
            obj_ok = obj.status_code == 200 and bool(first.get("urlThumbnail"))
        except ValueError:
            print(f"  HTTP {obj.status_code} mas a resposta não é JSON: {' '.join(obj.text[:150].split())}")

    print("\n=== Diagnóstico")
    if blocked:
        print(f"  O site recusou pedidos ({', '.join(blocked)}). Veja o HTTP acima: 403 = bloqueio de robôs.")
    elif len(distinct) == 1 and not next(iter(distinct)):
        print("  A página não traz nenhum link de obra no HTML, para nenhum termo.")
        print("  O app lê links /ark:/53355/cl... do HTML, então nunca acha nada: é isso que faz o Louvre 'não rodar'.")
    elif len(distinct) == 1:
        print("  A página devolve os MESMOS links para termos diferentes: o servidor ignora o termo.")
        print("  O app acharia sempre as mesmas obras (que nada têm a ver com a busca).")
    else:
        print("  Os resultados mudam com o termo: a busca por HTML funciona. Cole esta saída aqui.")
    if obj_ok:
        print("  O JSON de cada obra funciona: só a etapa de ACHAR as obras precisa de outro caminho.")
    print("\n  Próximo passo se a busca é por JavaScript: no navegador, abra collections.louvre.fr,")
    print("  aperte F12 > aba Rede (Network) > filtro Fetch/XHR, pesquise 'Vénus' e copie o endereço")
    print("  do pedido que aparecer. Cole aqui junto com esta saída.")


if __name__ == "__main__":
    main_check()