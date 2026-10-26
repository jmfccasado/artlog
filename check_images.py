"""Testa se as imagens de cada museu carregam de verdade.

Uso (com o venv ativo; pode rodar de qualquer pasta):
    python check_images.py             # busca "monet"
    python check_images.py "van gogh"

Para cada museu, roda a mesma busca do app e baixa o começo de cada imagem.
Mostra o código HTTP, o tipo de conteúdo, o tamanho e se os primeiros bytes são
mesmo de uma imagem (um "200" que devolve uma página HTML também quebra o <img>).

As imagens externas são testadas de dois jeitos:
  - sem Referer: o que o app faz hoje (referrerPolicy = "no-referrer");
  - com o Referer que o navegador mandaria por padrão.
Se só um dos dois funcionar, o servidor da imagem filtra por Referer.
"""
from __future__ import annotations

import sys

import requests

import main  # mesma pasta: usa as buscas reais do app

QUERY = sys.argv[1] if len(sys.argv) > 1 else "monet"
LIMIT = 3
BROWSER_REFERER = "http://127.0.0.1:8000/"


def looks_like_image(head: bytes) -> bool:
    return (
        head.startswith((b"\xff\xd8\xff", b"\x89PNG", b"GIF8"))
        or (head[:4] == b"RIFF" and head[8:12] == b"WEBP")
        or head[4:8] == b"ftyp"  # AVIF / HEIC
    )


def probe_external(url: str, referer: str | None) -> tuple[bool, str]:
    headers = {
        "User-Agent": main.BROWSER_USER_AGENT,
        "Accept": "image/avif,image/webp,image/*,*/*;q=0.8",
    }
    if referer:
        headers["Referer"] = referer
    try:
        with requests.get(url, headers=headers, timeout=15, stream=True) as res:
            head = next(res.iter_content(2048), b"")
            ctype = res.headers.get("content-type", "?").split(";")[0]
            length = res.headers.get("content-length")
            size = f"{int(length) // 1024} KB" if length and length.isdigit() else "tamanho ?"
            if res.status_code != 200:
                return False, f"HTTP {res.status_code} {ctype}"
            if not looks_like_image(head):
                return False, f"HTTP 200 {ctype} mas NÃO é imagem (começa com {head[:30]!r})"
            return True, f"HTTP 200 {ctype} {size}"
    except requests.RequestException as exc:
        return False, f"erro de rede: {exc.__class__.__name__}"


def probe_artic_proxy(path: str) -> tuple[bool, str]:
    """Testa o que a rota /artic-image/ do app buscaria no servidor deles."""
    image_id = path.rsplit("/", 1)[-1]
    try:
        content, ctype = main._fetch_artic_image(image_id)
    except main.MuseumError as exc:
        return False, f"o proxy falhou: {exc}"
    if not looks_like_image(content[:16]):
        return False, f"o proxy recebeu 200 ({ctype}) mas NÃO é imagem"
    return True, f"proxy OK {ctype} {len(content) // 1024} KB"


def main_check() -> None:
    failures: list[str] = []
    print(f"Busca: {QUERY!r}, até {LIMIT} obras por museu")

    for key, search in main.MUSEUMS.items():
        print(f"\n=== {key}")
        try:
            artworks = search(QUERY, LIMIT)
        except main.MuseumError as exc:
            print(f"  a busca falhou: {exc}")
            failures.append(f"{key}: busca falhou ({exc})")
            continue
        except Exception as exc:  # diagnóstico: mostra qualquer falha
            print(f"  erro inesperado na busca: {exc.__class__.__name__}: {exc}")
            failures.append(f"{key}: erro inesperado na busca")
            continue
        if not artworks:
            print("  nenhuma obra com imagem")
            continue

        for art in artworks:
            url = art["image"]
            print(f"  {art['title'][:50]}\n    {url}")
            if url.startswith("/artic-image/"):
                ok, text = probe_artic_proxy(url)
                print(f"    {text}")
                if not ok:
                    failures.append(f"{key}: {text}")
                continue
            results = {}
            for label, referer in (("sem Referer", None), ("com Referer", BROWSER_REFERER)):
                ok, text = probe_external(url, referer)
                results[label] = ok
                print(f"    {label:<12} {text}")
            if not results["sem Referer"]:
                # É o caso do app (no-referrer): só isso quebra a imagem na tela.
                hint = " (funciona COM Referer)" if results["com Referer"] else ""
                failures.append(f"{key}: {url}{hint}")

    print("\n=== Resumo")
    if not failures:
        print("  Todas as imagens testadas carregaram.")
    for line in failures:
        print(f"  FALHA {line}")


if __name__ == "__main__":
    main_check()