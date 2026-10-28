"""Artlog API: busca unificada em acervos de museus.

Como rodar (de qualquer pasta):
    python main.py
ou, de dentro desta pasta:
    uvicorn main:app --reload
"""
from __future__ import annotations

import html
import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from itertools import zip_longest
from pathlib import Path
from typing import Any, Callable, Literal
from urllib.parse import quote

import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).resolve().parent

def _load_env_file(path: Path) -> None:
    """Lê linhas CHAVE=valor de um .env simples, sem sobrescrever o que já está no ambiente."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        os.environ.setdefault(name.strip(), value.strip().strip('"').strip("'"))


_load_env_file(BASE_DIR / ".env")

# Importado depois do .env, porque lê ARTLOG_DB e COOKIE_SECURE de lá.
from auth import router as auth_router  # noqa: E402
from user_profile import router as profile_router  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s [%(name)s] %(message)s")
log = logging.getLogger("artlog")

app = FastAPI(
    title="Artlog API",
    description="API unificada para busca de acervos em múltiplos museus.",
    version="1.1.0",
)

if os.path.exists("assets"):
    app.mount("/assets", StaticFiles(directory="assets"), name="assets")

# CORS aberto para desenvolvimento. Sem credenciais, porque a combinação
# "*" + credenciais não é válida no navegador.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Contas: /auth/register, /auth/login, /auth/logout, /auth/me (ver auth.py).
app.include_router(auth_router)
# Perfil e Top 5: /profile, /profile/password, /profile/avatar, /profile/top5 (ver user_profile.py).
app.include_router(profile_router)

# Pasta de assets (imagens, logos, ícones), se existir.
ASSETS_DIR = BASE_DIR / "assets"
if ASSETS_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=ASSETS_DIR), name="assets")


# ---------------------------------------------------------------------------
# Frontend
# Os caminhos partem da pasta deste arquivo, então funcionam de qualquer pasta.
# ---------------------------------------------------------------------------
def _send_file(filename: str) -> FileResponse:
    path = BASE_DIR / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"{filename} não encontrado")
    return FileResponse(path)


@app.get("/", include_in_schema=False)
def serve_index() -> FileResponse:
    return _send_file("index.html")


@app.get("/style.css", include_in_schema=False)
def serve_css() -> FileResponse:
    return _send_file("style.css")


# ---------------------------------------------------------------------------
# Acesso HTTP às APIs dos museus
# ---------------------------------------------------------------------------
TIMEOUT = (3.05, 8)  # (conexão, leitura) em segundos

# Importante: cabeçalhos HTTP só em ASCII (sem acentos). Alguns firewalls
# recusam requisições com caracteres fora do ASCII no User-Agent.
APP_USER_AGENT = "Artlog/1.1 (personal art catalog project)"
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
# Se a API recusar o User-Agent do app (403), tenta uma vez com o de navegador.
# 410 ("removido de vez") não entra: repetir não adianta.
RETRY_WITH_BROWSER_STATUSES = {403}


class MuseumError(Exception):
    """Falha ao consultar a API de um museu."""


def _get(
    url: str,
    params: dict[str, Any] | None,
    headers: dict[str, str] | None,
    user_agent: str,
    timeout: tuple[float, float] = TIMEOUT,
) -> requests.Response:
    try:
        return requests.get(
            url,
            params=params,
            headers={"Accept": "application/json", **(headers or {}), "User-Agent": user_agent},
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise MuseumError(f"falha de rede ({exc.__class__.__name__})") from exc


def fetch_json(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: tuple[float, float] = TIMEOUT,
) -> Any:
    """GET com tratamento de erro. Levanta MuseumError com um motivo claro."""
    res = _get(url, params, headers, APP_USER_AGENT, timeout)

    if res.status_code in RETRY_WITH_BROWSER_STATUSES:
        log.info("HTTP %s em %s; tentando de novo com User-Agent de navegador", res.status_code, url)
        res = _get(url, params, headers, BROWSER_USER_AGENT, timeout)

    if res.status_code != 200:
        # O começo do corpo costuma dizer quem bloqueou (firewall, aviso da API...).
        snippet = " ".join(res.text[:200].split())
        log.warning("HTTP %s em %s -> %s", res.status_code, res.url, snippet)
        raise MuseumError(f"HTTP {res.status_code}")

    try:
        return res.json()
    except ValueError as exc:
        raise MuseumError("a resposta não é JSON válido") from exc


def make_artwork(
    *,
    prefix: str,
    raw_id: Any,
    title: str | None,
    artist: str | None,
    image: str | None,
    year: str | None,
    museum: str,
    url: str | None = None,
    credit: str | None = None,
) -> dict[str, Any]:
    """Formato único de obra, igual para todos os museus.

    url e credit são opcionais: só os museus cujos termos exigem crédito da foto
    e link para a ficha da obra (como o Louvre) preenchem.
    """
    return {
        "id": f"{prefix}_{raw_id}",
        "title": title or "Sem título",
        "artist": artist or "Artista desconhecido",
        "image": image or None,
        "year": year or "Data desconhecida",
        "museum": museum,
        "url": url or None,
        "credit": credit or None,
    }


# ---------------------------------------------------------------------------
# Museus
# ---------------------------------------------------------------------------
# Quantas candidatas pedir por obra desejada, já que parte delas vem sem imagem.
OVERFETCH = 4

MET_API = "https://collectionapi.metmuseum.org/public/collection/v1"
MET_API_SEARCH = "https://collectionapi.metmuseum.org/public/collection/v1.1"
MET_NAME = "The Metropolitan Museum of Art"


def _met_object(obj_id: int) -> dict[str, Any] | None:
    try:
        d = fetch_json(f"{MET_API}/objects/{obj_id}")
    except MuseumError as exc:
        log.warning("[met] objeto %s ignorado: %s", obj_id, exc)
        return None
    return make_artwork(
        prefix="met",
        raw_id=d.get("objectID", obj_id),
        title=d.get("title"),
        artist=d.get("artistDisplayName"),
        image=d.get("primaryImageSmall") or d.get("primaryImage"),
        year=d.get("objectDate"),
        museum=MET_NAME,
    )


def search_met(q: str, limit: int) -> list[dict[str, Any]]:
    # A busca v1 foi aposentada em 1/10/2026 (devolve HTTP 410). A v1.1 é paginada
    # e devolve só os IDs. O detalhe de cada obra (/objects/{id}) continua na v1.
    #
    # Muitas obras do Met têm imagem no site, mas a API só entrega o arquivo das que
    # são de domínio público. Por isso pedimos mais candidatas e ficamos com as que
    # realmente trazem imagem.
    candidates = min(limit * OVERFETCH, 24)  # teto: cada candidata é 1 pedido
    data = fetch_json(
        f"{MET_API_SEARCH}/search",
        params={"q": q, "hasImages": "true", "limit": candidates},
    )
    object_ids = (data.get("objectIDs") or [])[:candidates]
    if not object_ids:
        return []
    # Um pedido por obra, vários ao mesmo tempo.
    with ThreadPoolExecutor(max_workers=min(len(object_ids), 10)) as pool:
        details = list(pool.map(_met_object, object_ids))
    with_image = [art for art in details if art and art["image"]]
    log.info("[met] %d candidatas, %d com imagem", len(object_ids), len(with_image))
    return with_image[:limit]


ARTIC_API = "https://api.artic.edu/api/v1"
ARTIC_IIIF = "https://www.artic.edu/iiif/2"
ARTIC_NAME = "Art Institute of Chicago"


def search_artic(q: str, limit: int) -> list[dict[str, Any]]:
    data = fetch_json(
        f"{ARTIC_API}/artworks/search",
        params={
            "q": q,
            "fields": "id,title,artist_title,image_id,date_display",
            "limit": limit * OVERFETCH,
        },
        # A API do Art Institute pede que o cliente se identifique.
        headers={"AIC-User-Agent": "Artlog (projeto pessoal)"},
    )
    artworks = []
    for item in data.get("data") or []:
        image_id = item.get("image_id")
        # O servidor de imagens do Art Institute recusa pedidos vindos de outro
        # site (403). Por isso as imagens passam pelo nosso servidor (rota abaixo).
        image = f"/artic-image/{image_id}" if image_id else None
        artworks.append(
            make_artwork(
                prefix="artic",
                raw_id=item.get("id"),
                title=item.get("title"),
                artist=item.get("artist_title"),
                image=image,
                year=item.get("date_display"),
                museum=ARTIC_NAME,
            )
        )
    with_image = [art for art in artworks if art["image"]]
    log.info("[artic] %d candidatas, %d com imagem", len(artworks), len(with_image))
    return with_image[:limit]


ARTIC_IMAGE_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


@lru_cache(maxsize=256)
def _fetch_artic_image(image_id: str) -> tuple[bytes, str]:
    """Baixa a imagem identificando o site de origem. Falhas não ficam em cache."""
    try:
        res = requests.get(
            f"{ARTIC_IIIF}/{image_id}/full/843,/0/default.jpg",
            headers={
                "User-Agent": BROWSER_USER_AGENT,
                "Referer": "https://www.artic.edu/",
                "Accept": "image/avif,image/webp,image/*,*/*;q=0.8",
            },
            timeout=TIMEOUT,
        )
    except requests.RequestException as exc:
        raise MuseumError(f"falha de rede ({exc.__class__.__name__})") from exc
    if res.status_code != 200:
        log.warning("imagem artic %s -> HTTP %s", image_id, res.status_code)
        raise MuseumError(f"HTTP {res.status_code}")
    ctype = res.headers.get("content-type", "")
    if not ctype.startswith("image/"):
        # Um "200" com HTML (página de bloqueio) não pode ser repassado como se fosse imagem.
        log.warning("imagem artic %s -> tipo inesperado: %s", image_id, ctype or "sem tipo")
        raise MuseumError(f"a resposta não é imagem ({ctype or 'sem tipo'})")
    return res.content, ctype


@app.get("/artic-image/{image_id}", include_in_schema=False)
def artic_image(image_id: str) -> Response:
    # Só aceita IDs no formato esperado: a rota não vira um proxy aberto.
    if not ARTIC_IMAGE_ID.fullmatch(image_id):
        raise HTTPException(status_code=404, detail="imagem inválida")
    try:
        content, content_type = _fetch_artic_image(image_id)
    except MuseumError as exc:
        raise HTTPException(status_code=502, detail=f"imagem indisponível: {exc}") from exc
    return Response(
        content=content,
        media_type=content_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )


CLEVELAND_API = "https://openaccess-api.clevelandart.org/api/artworks/"
CLEVELAND_NAME = "Cleveland Museum of Art"


def search_cleveland(q: str, limit: int) -> list[dict[str, Any]]:
    data = fetch_json(
        CLEVELAND_API,
        params={"q": q, "has_image": 1, "limit": limit * OVERFETCH},
    )
    artworks = []
    for item in data.get("data") or []:
        images = item.get("images")
        web = images.get("web") if isinstance(images, dict) else None
        image = web.get("url") if isinstance(web, dict) else None

        creators = item.get("creators") or []
        artist = creators[0].get("description") if creators else None

        artworks.append(
            make_artwork(
                prefix="cleveland",
                raw_id=item.get("id"),
                title=item.get("title"),
                artist=artist,
                image=image,
                year=item.get("creation_date"),
                museum=CLEVELAND_NAME,
            )
        )
    with_image = [art for art in artworks if art["image"]]
    log.info("[cleveland] %d candidatas, %d com imagem", len(artworks), len(with_image))
    return with_image[:limit]


SMK_API = "https://api.smk.dk/api/v1/art/search/"
SMK_NAME = "SMK, National Gallery of Denmark"


def _first(entries: Any, key: str) -> str | None:
    """Primeiro valor preenchido de `key` numa lista de dicionários."""
    if isinstance(entries, list):
        for entry in entries:
            if isinstance(entry, dict) and entry.get(key):
                return str(entry[key])
    return None


def search_smk(q: str, limit: int) -> list[dict[str, Any]]:
    data = fetch_json(
        SMK_API,
        params={
            "keys": q,
            "rows": limit,
            "lang": "en",
            # Só obras com imagem e de domínio público (uso livre, inclusive a foto).
            "filters": "[has_image:true],[public_domain:true]",
        },
    )
    artworks = []
    for item in data.get("items") or []:
        artworks.append(
            make_artwork(
                prefix="smk",
                raw_id=item.get("object_number"),
                title=_first(item.get("titles"), "title"),
                artist=_first(item.get("production"), "creator"),
                image=item.get("image_thumbnail"),
                year=_first(item.get("production_date"), "period"),
                museum=SMK_NAME,
            )
        )
    with_image = [art for art in artworks if art["image"]]
    log.info("[smk] %d candidatas, %d com imagem", len(artworks), len(with_image))
    return with_image[:limit]


VAM_API = "https://api.vam.ac.uk/v2/objects/search"
VAM_IMAGES = "https://framemark.vam.ac.uk/collections"
VAM_NAME = "Victoria and Albert Museum"


def search_vam(q: str, limit: int) -> list[dict[str, Any]]:
    data = fetch_json(
        VAM_API,
        params={"q": q, "page_size": limit * OVERFETCH, "images_exist": 1},
    )
    artworks = []
    for item in data.get("records") or []:
        image_id = item.get("_primaryImageId")
        if image_id:
            image = f"{VAM_IMAGES}/{quote(str(image_id), safe='')}/full/!600,600/0/default.jpg"
        else:
            images = item.get("_images")
            image = images.get("_primary_thumbnail") if isinstance(images, dict) else None

        maker = item.get("_primaryMaker")
        artist = maker.get("name") if isinstance(maker, dict) else None
        if artist and artist.strip().lower() == "unknown":
            artist = None

        artworks.append(
            make_artwork(
                prefix="vam",
                raw_id=item.get("systemNumber"),
                title=item.get("_primaryTitle") or item.get("objectType"),
                artist=artist,
                image=image,
                year=item.get("_primaryDate"),
                museum=VAM_NAME,
            )
        )
    with_image = [art for art in artworks if art["image"]]
    log.info("[vam] %d candidatas, %d com imagem", len(artworks), len(with_image))
    return with_image[:limit]


LOUVRE_SITE = "https://collections.louvre.fr"
LOUVRE_NAME = "Musée du Louvre"
# Identificador permanente (ARK) de cada obra, presente nos links da página de busca.
LOUVRE_ARK_LINK = re.compile(r"/ark:/53355/(cl\d{6,})")
# Cada obra é um pedido extra a um site sem API pública de busca: pedimos poucas sobras.
LOUVRE_OVERFETCH = 2


def _louvre_search_ids(q: str, count: int) -> list[str]:
    """O Louvre não tem API de busca: lê a página de resultados e pega os ARKs."""
    params = {"q": q}
    headers = {"Accept": "text/html"}
    res = _get(f"{LOUVRE_SITE}/en/recherche", params, headers, APP_USER_AGENT)
    if res.status_code in RETRY_WITH_BROWSER_STATUSES:
        res = _get(f"{LOUVRE_SITE}/en/recherche", params, headers, BROWSER_USER_AGENT)
    if res.status_code != 200:
        log.warning("HTTP %s em %s", res.status_code, res.url)
        raise MuseumError(f"HTTP {res.status_code}")

    ids = list(dict.fromkeys(LOUVRE_ARK_LINK.findall(res.text)))  # sem repetir, em ordem
    if not ids:
        # Sem resultado de verdade ou o layout do site mudou: o log ajuda a distinguir.
        log.warning("[louvre] nenhum link de obra na página de busca (%d bytes)", len(res.text))
    return ids[:count]


def _louvre_artist(label: str | None) -> str | None:
    """'VAN GOGH Vincent' -> 'Vincent Van Gogh' (o Louvre escreve o sobrenome em maiúsculas)."""
    if not label:
        return None
    words = label.split()
    surname: list[str] = []
    while words and words[0].isupper():
        surname.append(words.pop(0))
    if not surname:
        return label
    return " ".join(words + [" ".join(surname).title()])


def _louvre_year(d: dict[str, Any]) -> str | None:
    shown = d.get("displayDateCreated")
    if isinstance(shown, str) and shown.strip():
        # "Date de création/fabrication : 2e quart du XVIIIe siècle (1737)"
        return shown.split(" : ", 1)[-1].strip()
    dates = d.get("dateCreated")
    if isinstance(dates, list) and dates and isinstance(dates[0], dict):
        return dates[0].get("text")
    return None


def _louvre_object(ark: str) -> dict[str, Any] | None:
    try:
        d = fetch_json(f"{LOUVRE_SITE}/ark:/53355/{ark}.json")
    except MuseumError as exc:
        log.warning("[louvre] obra %s ignorada: %s", ark, exc)
        return None
    if not isinstance(d, dict):
        return None

    image = credit = None
    for entry in d.get("image") or []:
        if isinstance(entry, dict) and (entry.get("urlThumbnail") or entry.get("urlImage")):
            image = entry.get("urlThumbnail") or entry.get("urlImage")
            credit = entry.get("copyright")
            break

    creators = d.get("creator")
    first = creators[0] if isinstance(creators, list) and creators else None
    label = first.get("label") if isinstance(first, dict) else None

    return make_artwork(
        prefix="louvre",
        raw_id=d.get("arkId") or ark,
        title=d.get("title"),
        artist=_louvre_artist(label),
        image=image,
        year=_louvre_year(d),
        museum=LOUVRE_NAME,
        # Os termos de uso exigem crédito da foto e link para a ficha da obra.
        url=d.get("url") or f"{LOUVRE_SITE}/ark:/53355/{ark}",
        credit=credit,
    )


def search_louvre(q: str, limit: int) -> list[dict[str, Any]]:
    ids = _louvre_search_ids(q, min(limit * LOUVRE_OVERFETCH, 12))
    if not ids:
        return []
    # Poucos pedidos ao mesmo tempo: o site não foi feito para ser consultado como API.
    with ThreadPoolExecutor(max_workers=min(len(ids), 4)) as pool:
        details = list(pool.map(_louvre_object, ids))
    with_image = [art for art in details if art and art["image"]]
    log.info("[louvre] %d candidatas, %d com imagem", len(ids), len(with_image))
    return with_image[:limit]


# ---------------------------------------------------------------------------
# Smithsonian Institution (Open Access, CC0) - exige chave gratuita da api.data.gov
# ---------------------------------------------------------------------------
SI_API = "https://api.si.edu/openaccess/api/v1.0/search"
SI_NAME = "Smithsonian Institution"
# O Smithsonian reúne história natural, ciência, arquivos etc. Só os museus de arte:
# American Art, National Portrait Gallery, Freer|Sackler, Hirshhorn, African Art, Cooper Hewitt.
SI_ART_UNITS = ("SAAM", "NPG", "FSG", "HMSG", "NMAfA", "CHSDM")
SI_OVERFETCH = 4


def _https(url: str | None) -> str | None:
    return "https://" + url[len("http://"):] if url and url.startswith("http://") else url


def _si_first(entries: Any, label: str | None = None) -> str | None:
    """Primeiro 'content' de uma lista [{label, content}], opcionalmente de um rótulo."""
    for entry in entries or []:
        if isinstance(entry, dict) and entry.get("content") and (label is None or entry.get("label") == label):
            return str(entry["content"])
    return None


def _si_person(name: str | None) -> str | None:
    """'Wood, Grant' -> 'Grant Wood'."""
    if name and name.count(",") == 1:
        last, first = (part.strip() for part in name.split(","))
        return f"{first} {last}"
    return name


def search_smithsonian(q: str, limit: int) -> list[dict[str, Any]]:
    key = os.environ.get("SI_API_KEY", "").strip()
    if not key:
        raise MuseumError("defina a variável SI_API_KEY (chave gratuita em api.data.gov/signup)")
    # O campo q aceita sintaxe de busca avançada: deixa só letras, números, espaço, ' e -.
    clean = " ".join(re.sub(r"[^\w\s'-]", " ", q).split())
    if not clean:
        return []
    units = " OR ".join(f"unit_code:{unit}" for unit in SI_ART_UNITS)
    data = fetch_json(
        SI_API,
        params={
            "q": f"{clean} AND ({units})",
            "rows": min(limit * SI_OVERFETCH, 40),
            "media_type": "Images",
        },
        headers={"X-Api-Key": key},  # no cabeçalho, para a chave não aparecer em logs de URL
    )
    rows = (data.get("response") or {}).get("rows") or []

    artworks = []
    for row in rows:
        content = row.get("content") or {}
        dnr = content.get("descriptiveNonRepeating") or {}
        free = content.get("freetext") or {}
        indexed = content.get("indexedStructured") or {}

        # Só imagens explicitamente CC0: "Not determined" não dá direito de reuso.
        image = None
        for media in (dnr.get("online_media") or {}).get("media") or []:
            usage = media.get("usage") or {}
            if media.get("type") == "Images" and usage.get("access") == "CC0" and media.get("content"):
                image = _https(media["content"])
                if "deliveryService" in image and "max=" not in image:
                    image += "&max=800"  # versão média; sem isso o servidor entrega o original
                break
        if not image:
            continue

        names = indexed.get("name") or []
        dates = indexed.get("date") or []
        source = dnr.get("data_source")
        artworks.append(
            make_artwork(
                prefix="si",
                raw_id=dnr.get("record_ID") or row.get("id"),
                title=row.get("title") or (dnr.get("title") or {}).get("content"),
                artist=_si_person(_si_first(free.get("name")) or (names[0] if names else None)),
                image=image,
                year=_si_first(free.get("date")) or (dates[0] if dates else None),
                museum=f"Smithsonian · {source}" if source else SI_NAME,
                url=_https(dnr.get("record_link")),
            )
        )
    log.info("[smithsonian] %d linhas, %d com imagem CC0", len(rows), len(artworks))
    return artworks[:limit]


# ---------------------------------------------------------------------------
# Whitney Museum of American Art
# Os termos da API permitem uso pessoal/educativo não comercial, mantendo os avisos
# de copyright e citando a fonte. Por isso toda obra leva crédito e link.
# ---------------------------------------------------------------------------
WHITNEY_API = "https://whitney.org/api/artworks"
WHITNEY_SITE = "https://whitney.org/collection/works"
WHITNEY_NAME = "Whitney Museum of American Art"
WHITNEY_IMAGE = re.compile(r"https://whitneymedia\.org/[^\s\"']+?\.(?:jpe?g|png|webp)", re.IGNORECASE)


def _find_whitney_image(value: Any) -> str | None:
    """A doc. da API não mostra o formato da resposta; procura a 1ª URL de imagem em qualquer lugar."""
    if isinstance(value, str):
        match = WHITNEY_IMAGE.search(value)
        return match.group(0) if match else None
    if isinstance(value, dict):
        value = list(value.values())
    if isinstance(value, list):
        for item in value:
            found = _find_whitney_image(item)
            if found:
                return found
    return None


def _whitney_names(value: Any) -> str | None:
    """Artista pode vir como texto, lista de textos ou lista de objetos com 'name'."""
    if isinstance(value, str):
        return value or None
    if isinstance(value, dict):
        return _whitney_names(value.get("name") or value.get("display_name"))
    if isinstance(value, list):
        names = [n for n in (_whitney_names(v) for v in value) if n]
        return ", ".join(names[:3]) or None
    return None


def search_whitney(q: str, limit: int) -> list[dict[str, Any]]:
    tokens = q.lower().split()
    found: dict[str, dict[str, Any]] = {}
    # Dois pedidos: título e artista. O nome exato do campo do artista não está na doc.;
    # se for inválido o servidor pode ignorar o filtro, então conferimos o termo abaixo.
    for field in ("title_cont", "artists_cont"):
        try:
            data = fetch_json(WHITNEY_API, params={f"q[{field}]": q})
        except MuseumError as exc:
            if not found:
                raise  # nada deu certo: mostra o motivo
            log.warning("[whitney] consulta por %s falhou: %s", field, exc)
            continue
        items = data.get("data") if isinstance(data, dict) else data
        if not isinstance(items, list):
            items = (data.get("artworks") if isinstance(data, dict) else None) or []
        for item in items:
            if not isinstance(item, dict):
                continue
            attrs = item.get("attributes") if isinstance(item.get("attributes"), dict) else item
            raw_id = item.get("id") or attrs.get("id") or attrs.get("tms_id")
            if raw_id is None or str(raw_id) in found:
                continue
            # Garante que o termo realmente aparece na obra (defesa contra filtro ignorado).
            haystack = json.dumps(attrs, ensure_ascii=False).lower()
            if not all(token in haystack for token in tokens):
                continue
            found[str(raw_id)] = attrs | {"_id": raw_id}

    artworks = []
    for attrs in found.values():
        image = _find_whitney_image(attrs)
        if not image:
            continue
        credit = attrs.get("credit_line_repro")
        artworks.append(
            make_artwork(
                prefix="whitney",
                raw_id=attrs["_id"],
                title=attrs.get("title"),
                artist=_whitney_names(attrs.get("artists") or attrs.get("artist")),
                image=image,
                year=attrs.get("display_date") or attrs.get("date"),
                museum=WHITNEY_NAME,
                url=f"{WHITNEY_SITE}/{attrs['_id']}",
                credit=credit if isinstance(credit, str) and credit else f"{WHITNEY_NAME}, New York",
            )
        )
    if found and not artworks:
        sample = next(iter(found.values()))
        log.warning("[whitney] %d obras, nenhuma com imagem reconhecida. Campos: %s", len(found), sorted(sample))
    log.info("[whitney] %d obras, %d com imagem", len(found), len(artworks))
    return artworks[:limit]


# ---------------------------------------------------------------------------
# J. Paul Getty Museum (Linked Art + SPARQL, sem chave)
# Os dados são CC0, mas as IMAGENS nem sempre: só as do programa Open Content são.
# Cada imagem traz o aviso de direitos no próprio registro, e só ficamos com as CC0.
# ---------------------------------------------------------------------------
GETTY_SPARQL = "https://data.getty.edu/museum/collection/sparql"
GETTY_OBJECT = "https://data.getty.edu/museum/collection/object"
GETTY_IIIF = "https://media.getty.edu/iiif/image"
GETTY_NAME = "J. Paul Getty Museum"
# Texto de crédito preferido pelo Getty (pedido, não obrigação, mas custa pouco).
GETTY_CREDIT = "Courtesy of the J. Paul Getty Museum, Los Angeles"
GETTY_OVERFETCH = 3
# A busca de texto no SPARQL varre o acervo todo e pode levar vários segundos.
GETTY_SPARQL_TIMEOUT = (3.05, 30)
GETTY_OBJECT_URI = re.compile(r"/museum/collection/object/([0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12})$")
GETTY_IMAGE_UUID = re.compile(r"media\.getty\.edu/iiif/image/([0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12})")
GETTY_PAGE = re.compile(r"https://www\.getty\.edu/art/collection/object/[A-Za-z0-9]+")


def _getty_sparql(tokens: list[str], count: int) -> str:
    # Os tokens já vêm só com letras/números/espaço/'/-, então não quebram a string SPARQL.
    # Dois ramos (título OU artista) são bem mais leves que juntar tudo num texto só.
    # Contrapartida: todos os termos precisam estar no título ou todos no nome do artista.
    cond = " && ".join(f'CONTAINS(LCASE(STR(?text)), "{token}")' for token in tokens)
    return f"""
PREFIX crm: <http://www.cidoc-crm.org/cidoc-crm/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT DISTINCT ?obj WHERE {{
  {{ ?obj rdfs:label ?text . FILTER({cond}) }}
  UNION
  {{ ?obj crm:P108i_was_produced_by/crm:P14_carried_out_by/rdfs:label ?text . FILTER({cond}) }}
  ?obj a crm:E22_Human-Made_Object ;
       crm:P138i_has_representation ?rep .
}}
LIMIT {count}
"""


@lru_cache(maxsize=64)
def _getty_find_ids(query: str) -> tuple[str, ...]:
    """Roda o SPARQL (lento) e guarda o resultado. Falhas não ficam em cache."""
    data = fetch_json(
        GETTY_SPARQL,
        params={"query": query},
        headers={"Accept": "application/sparql-results+json"},
        timeout=GETTY_SPARQL_TIMEOUT,
    )
    bindings = (data.get("results") or {}).get("bindings") or []
    ids = []
    for row in bindings:
        match = GETTY_OBJECT_URI.search((row.get("obj") or {}).get("value", ""))
        if match:
            ids.append(match.group(1))
    return tuple(ids)


def _getty_artists(production: Any) -> str | None:
    """Nomes de quem fez a obra. Com vários autores, ficam em 'part' dentro da produção."""
    names: list[str] = []

    def collect(node: Any) -> None:
        if not isinstance(node, dict):
            return
        for person in node.get("carried_out_by") or []:
            if isinstance(person, dict) and person.get("_label"):
                names.append(str(person["_label"]))
        for part in node.get("part") or []:
            collect(part)

    collect(production)
    return ", ".join(list(dict.fromkeys(names))[:3]) or None


def _getty_year(production: Any) -> str | None:
    timespan = production.get("timespan") if isinstance(production, dict) else None
    if not isinstance(timespan, dict):
        return None
    for ident in timespan.get("identified_by") or []:
        if isinstance(ident, dict) and ident.get("content"):
            return str(ident["content"])
    return timespan.get("_label") or None


def _getty_image(representations: Any) -> str | None:
    """Primeira imagem CC0. O aviso de direitos fica no mesmo bloco da imagem."""
    for rep in representations if isinstance(representations, list) else []:
        if not isinstance(rep, dict):
            continue
        blob = json.dumps(rep)
        if "publicdomain/zero/1.0" not in blob:
            continue  # sob copyright ou outra licença: não usamos
        match = GETTY_IMAGE_UUID.search(blob)
        if match:
            return f"{GETTY_IIIF}/{match.group(1)}/full/600,/0/default.jpg"
    return None


def _getty_object(uuid: str) -> dict[str, Any] | None:
    try:
        d = fetch_json(
            f"{GETTY_OBJECT}/{uuid}",
            headers={"Accept": "application/ld+json, application/json;q=0.9"},
        )
    except MuseumError as exc:
        log.warning("[getty] objeto %s ignorado: %s", uuid, exc)
        return None
    if not isinstance(d, dict):
        return None

    image = _getty_image(d.get("representation"))
    if not image:
        return None
    production = d.get("produced_by")
    page = GETTY_PAGE.search(json.dumps(d.get("subject_of") or []))
    return make_artwork(
        prefix="getty",
        raw_id=uuid,
        title=d.get("_label"),
        artist=_getty_artists(production),
        image=image,
        year=_getty_year(production),
        museum=GETTY_NAME,
        url=page.group(0) if page else f"{GETTY_OBJECT}/{uuid}",
        credit=GETTY_CREDIT,
    )


def search_getty(q: str, limit: int) -> list[dict[str, Any]]:
    # A API não tem busca por texto: usamos o SPARQL para achar os IDs e depois
    # lemos cada registro (como no Met). O termo vale para título e artista.
    clean = " ".join(re.sub(r"[^\w\s'-]", " ", q).split())
    if not clean:
        return []
    tokens = clean.lower().split()[:6]
    candidates = min(limit * GETTY_OVERFETCH, 18)
    uuids = list(_getty_find_ids(_getty_sparql(tokens, candidates)))
    if not uuids:
        return []
    with ThreadPoolExecutor(max_workers=min(len(uuids), 6)) as pool:
        details = list(pool.map(_getty_object, uuids))
    with_image = [art for art in details if art]
    log.info("[getty] %d candidatas, %d com imagem CC0", len(uuids), len(with_image))
    return with_image[:limit]


GUGGENHEIM_API = "https://www.guggenheim.org/wp-json/wp/v2"
GUGGENHEIM_NAME = "Solomon R. Guggenheim Museum"
GUGGENHEIM_CREDIT = "Solomon R. Guggenheim Foundation, New York"
GUGGENHEIM_OVERFETCH = 3
GUGGENHEIM_IMAGE_SIZES = ("medium_large", "large", "guggenheim-870", "full")
GUGGENHEIM_TAGS = re.compile(r"<[^>]+>")


def _guggenheim_artist_ids(q: str) -> list[int]:
    """IDs das obras dos artistas cujo nome bate com o termo (busca mais precisa por artista)."""
    terms = fetch_json(
        f"{GUGGENHEIM_API}/artist",
        params={"search": q, "per_page": 3, "orderby": "count", "order": "desc", "_fields": "id,count"},
    )
    term_ids = [str(t["id"]) for t in terms or [] if isinstance(t, dict) and t.get("count")]
    if not term_ids:
        return []
    works = fetch_json(
        f"{GUGGENHEIM_API}/artwork",
        params={"artist": ",".join(term_ids), "per_page": 30, "_fields": "id"},
    )
    return [w["id"] for w in works or [] if isinstance(w, dict) and w.get("id")]


def _guggenheim_search_ids(q: str, count: int) -> list[int]:
    """Busca geral do site. O filtro subtype só dá prioridade às obras, então filtramos de novo."""
    data = fetch_json(
        f"{GUGGENHEIM_API}/search",
        params={"search": q, "subtype": "artwork", "per_page": count},
    )
    return [
        item["id"]
        for item in data or []
        if isinstance(item, dict) and item.get("subtype") == "artwork" and item.get("id")
    ]


def _guggenheim_year(caption: str | None) -> str | None:
    """'<p>Vasily Kandinsky, <i>Singer</i>, 1903</p>' -> '1903'."""
    if not caption or "</i>" not in caption:
        return None
    tail = html.unescape(GUGGENHEIM_TAGS.sub("", caption.rsplit("</i>", 1)[1])).strip(" ,.\n")
    return None if not tail or tail.lower() in {"n.d", "n.d."} else tail


def _guggenheim_artwork(item: dict[str, Any]) -> dict[str, Any] | None:
    embedded = item.get("_embedded") or {}
    media_list = embedded.get("wp:featuredmedia") or []
    media = media_list[0] if media_list and isinstance(media_list[0], dict) else {}
    sizes = (media.get("media_details") or {}).get("sizes") or {}
    image = next(
        (sizes[s]["source_url"] for s in GUGGENHEIM_IMAGE_SIZES if isinstance(sizes.get(s), dict) and sizes[s].get("source_url")),
        media.get("source_url"),
    )
    if not image:
        return None

    terms = [t for group in embedded.get("wp:term") or [] for t in group or [] if isinstance(t, dict)]
    artists = [html.unescape(t["name"]) for t in terms if t.get("taxonomy") == "artist" and t.get("name")]
    sites = [html.unescape(t["name"]) for t in terms if t.get("taxonomy") == "site" and t.get("name")]
    caption = (media.get("caption") or {}).get("rendered")

    return make_artwork(
        prefix="guggenheim",
        raw_id=item.get("id"),
        title=html.unescape((item.get("title") or {}).get("rendered") or ""),
        artist=", ".join(artists[:3]) or None,
        image=image,
        year=_guggenheim_year(caption),
        museum=sites[0] if sites else GUGGENHEIM_NAME,
        url=item.get("link"),
        credit=GUGGENHEIM_CREDIT,
    )


def search_guggenheim(q: str, limit: int) -> list[dict[str, Any]]:
    """Acervo do Guggenheim via API pública do site (WordPress), sem chave.

    A antiga api.guggenheim.org exigia chave e saiu do ar. As imagens podem ter
    direitos do artista, então cada obra leva crédito e link para a ficha oficial.
    """
    candidates = min(limit * GUGGENHEIM_OVERFETCH, 30)
    with ThreadPoolExecutor(max_workers=2) as pool:
        by_artist = pool.submit(_guggenheim_artist_ids, q)
        by_search = pool.submit(_guggenheim_search_ids, q, candidates)
        try:
            artist_ids = by_artist.result()
        except MuseumError as exc:
            log.warning("[guggenheim] busca por artista falhou: %s", exc)
            artist_ids = []
        try:
            search_ids = by_search.result()
        except MuseumError as exc:
            if not artist_ids:
                raise
            log.warning("[guggenheim] busca geral falhou: %s", exc)
            search_ids = []

    ids = list(dict.fromkeys(artist_ids[:candidates] + search_ids))[:candidates]
    if not ids:
        return []
    items = fetch_json(
        f"{GUGGENHEIM_API}/artwork",
        params={
            "include": ",".join(str(i) for i in ids),
            "orderby": "include",
            "per_page": len(ids),
            "_embed": "wp:featuredmedia,wp:term",
            "_fields": "id,link,title,_links,_embedded",
        },
    )
    artworks = [art for art in (_guggenheim_artwork(i) for i in items or [] if isinstance(i, dict)) if art]
    log.info("[guggenheim] %d candidatas, %d com imagem", len(ids), len(artworks))
    return artworks[:limit]


# Para adicionar um museu: escreva uma função search_xxx(q, limit) e registre aqui
# (e acrescente a opção no <select> do index.html).
MUSEUMS: dict[str, Callable[[str, int], list[dict[str, Any]]]] = {
    "met": search_met,
    "artic": search_artic,
    "cleveland": search_cleveland,
    "smk": search_smk,
    "vam": search_vam,
    "louvre": search_louvre,
    "smithsonian": search_smithsonian,
    "whitney": search_whitney,
    "getty": search_getty,
    "guggenheim": search_guggenheim,
}


def _run_museum(key: str, q: str, limit: int) -> dict[str, Any]:
    """Roda um museu isolado: um erro aqui nunca derruba os outros."""
    try:
        artworks = MUSEUMS[key](q, limit)
        return {"artworks": artworks, "error": None}
    except MuseumError as exc:
        log.warning("[%s] falhou: %s", key, exc)
        return {"artworks": [], "error": str(exc)}
    except Exception as exc:  # erro inesperado de parsing, por exemplo
        log.exception("[%s] erro inesperado", key)
        return {"artworks": [], "error": f"erro inesperado ({exc.__class__.__name__})"}


# ---------------------------------------------------------------------------
# Busca
# ---------------------------------------------------------------------------
@app.get("/search")
def search_artworks(
    q: str = Query(..., max_length=100, description="Termo (ex: monet, van gogh)"),
    museum: Literal["all", "met", "artic", "cleveland", "smk", "vam", "louvre", "smithsonian", "whitney", "getty", "guggenheim"] = Query(
        "all", description="Museu específico ou 'all'"
    ),
    limit_per_museum: int = Query(2, ge=1, le=12, description="Obras por museu"),
) -> dict[str, Any]:
    q = q.strip()
    if not q:
        raise HTTPException(status_code=422, detail="Informe um termo de busca.")

    selected = list(MUSEUMS) if museum == "all" else [museum]

    # Todos os museus consultados ao mesmo tempo.
    with ThreadPoolExecutor(max_workers=len(selected)) as pool:
        futures = {key: pool.submit(_run_museum, key, q, limit_per_museum) for key in selected}
        outcomes = {key: future.result() for key, future in futures.items()}

    # Intercala os resultados (1ª de cada museu, depois a 2ª...) para que
    # nenhum museu domine o começo da lista.
    groups = [outcomes[key]["artworks"] for key in selected]
    artworks = [art for row in zip_longest(*groups) for art in row if art is not None]

    return {
        "query": q,
        "selected_museum": museum,
        "total_results": len(artworks),
        "artworks": artworks,
        # Estado de cada museu: mostra quem respondeu e quem falhou (e por quê).
        "museums": {
            key: {"count": len(outcomes[key]["artworks"]), "error": outcomes[key]["error"]}
            for key in selected
        },
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", app_dir=str(BASE_DIR), host="127.0.0.1", port=8000, reload=True)