"""Confere se a integração do Getty funciona no Artlog.

Uso (na pasta do projeto, com o venv ativo):
    python check_getty.py
    python check_getty.py "van gogh"

Passos:
  1. SPARQL simples (sem filtro de imagem): a API responde e o modelo de dados bate?
  2. SPARQL com filtro de imagem (P138i_has_representation): o filtro existe no grafo?
  3. Leitura de um registro: título, artista, ano e imagem CC0 são encontrados?
  4. A busca completa do app (search_getty).
"""
from __future__ import annotations

import sys
import time

import main  # carrega o app, igual ao servidor

QUERY = sys.argv[1] if len(sys.argv) > 1 else "monet"
TOKENS = QUERY.lower().split()


def sparql_ids(label: str, query: str) -> list[str]:
    print(f"\n[{label}]")
    start = time.time()
    try:
        data = main.fetch_json(
            main.GETTY_SPARQL,
            params={"query": query},
            headers={"Accept": "application/sparql-results+json"},
            timeout=main.GETTY_SPARQL_TIMEOUT,
        )
    except main.MuseumError as exc:
        print(f"  falhou após {time.time() - start:.1f}s: {exc}")
        return []
    rows = (data.get("results") or {}).get("bindings") or []
    ids = [r["obj"]["value"] for r in rows if "obj" in r]
    print(f"  {len(ids)} objeto(s) em {time.time() - start:.1f}s")
    return ids


def main_check() -> None:
    with_image = main._getty_sparql(TOKENS, 5)
    without_image = with_image.replace("\n       crm:P138i_has_representation ?rep .", " .")

    ids_any = sparql_ids("1. SPARQL sem exigir imagem", without_image)
    ids_img = sparql_ids("2. SPARQL exigindo imagem (o que o app usa)", with_image)

    print("\n[3. leitura de um registro]")
    sample = (ids_img or ids_any or [None])[0]
    if not sample:
        print("  sem objeto para testar (veja os passos 1 e 2)")
    else:
        uuid = main.GETTY_OBJECT_URI.search(sample)
        if not uuid:
            print(f"  URI inesperada: {sample}")
        else:
            d = main.fetch_json(
                f"{main.GETTY_OBJECT}/{uuid.group(1)}",
                headers={"Accept": "application/ld+json, application/json;q=0.9"},
            )
            print("  chaves do registro:", sorted(d))
            print("  título :", d.get("_label"))
            print("  artista:", main._getty_artists(d.get("produced_by")))
            print("  ano    :", main._getty_year(d.get("produced_by")))
            print("  imagem CC0:", main._getty_image(d.get("representation")) or "nenhuma (pode ser normal)")

    print("\n[4. busca completa do app]")
    try:
        found = main.search_getty(QUERY, 3)
    except main.MuseumError as exc:
        print(f"  falhou: {exc}")
        found = []
    for art in found:
        print(f"  - {art['title']} | {art['artist']} | {art['year']}\n    {art['image']}")
    if not found:
        print("  nenhuma obra com imagem CC0")

    print("\n=== Diagnóstico")
    if not ids_any and not ids_img:
        print("  O SPARQL não devolveu nada. Se o passo 1 deu erro, é rede/timeout ou a consulta;")
        print("  se deu 0 objetos, o modelo de dados difere do esperado. Cole esta saída aqui.")
    elif ids_any and not ids_img:
        print("  O filtro de imagem (P138i_has_representation) não bate com o grafo.")
        print("  Cole esta saída aqui que eu ajusto a consulta.")
    elif not found:
        print("  O SPARQL acha obras, mas nenhuma passou no filtro CC0/imagem.")
        print("  Veja o passo 3: se as chaves do registro forem outras, eu ajusto o parser.")
    else:
        print("  Tudo certo. Reinicie o servidor (Ctrl+C e `python main.py`) e escolha o Getty no seletor.")


if __name__ == "__main__":
    main_check()