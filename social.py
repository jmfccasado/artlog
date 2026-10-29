"""Parte social do Artlog: curadorias, avaliações, perfis públicos, seguir e feed.

Curadorias (listas de obras com nome; públicas ou privadas):
    GET    /curations/mine               minhas curadorias (com os IDs das obras, para o ♡)
    POST   /curations                    cria (pode já vir com obras: é assim que a coleção antiga migra)
    GET    /curations/discover           curadorias públicas recentes da comunidade
    GET    /curations/{id}               uma curadoria com as obras (privada: só o dono vê)
    PATCH  /curations/{id}               título, descrição, pública/privada
    DELETE /curations/{id}               apaga
    POST   /curations/{id}/items         adiciona uma obra
    PATCH  /curations/{id}/items         altera a nota / data de uma obra
    DELETE /curations/{id}/items?artwork_id=...   tira uma obra
    PUT    /curations/{id}/order         muda a ordem das obras
    PUT    /curations/{id}/rating        dá nota de 1 a 5 (só em curadoria pública de outra pessoa)
    DELETE /curations/{id}/rating        tira a nota

Pessoas:
    GET    /users?q=...                  busca pelo nome ou @ (sem q: sugestões de quem seguir)
    GET    /users/{username}             perfil público: dados, contagens, Top 5 e curadorias
    POST   /users/{username}/follow      seguir
    DELETE /users/{username}/follow      deixar de seguir
    GET    /users/{username}/followers   quem segue a pessoa
    GET    /users/{username}/following   quem a pessoa segue
    GET    /feed                         curadorias de quem você segue

Privacidade: o e-mail nunca sai num perfil público, e curadoria privada responde
404 para os outros (como se não existisse), para nem o título vazar.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from auth import current_user, get_db, optional_user, profile_card
from db import Database, Row, search_key
from user_profile import ArtworkIn, clean_artwork

router = APIRouter(tags=["curadorias e pessoas"])

TITLE_MAX = 80
DESCRIPTION_MAX = 1000
NOTE_MAX = 2000
MAX_ITEMS = 500
MAX_CURATIONS = 100
SEEN_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def _now() -> int:
    return int(time.time())


def _text(value: str, limit: int, label: str, *, multiline: bool = False, required: bool = False) -> str:
    value = _CONTROL.sub("", value.replace("\r\n", "\n"))
    value = re.sub(r"\n{3,}", "\n\n", value).strip() if multiline else " ".join(value.split())
    if required and not value:
        raise HTTPException(422, f"Dê um {label} para a curadoria.")
    if len(value) > limit:
        raise HTTPException(422, f"O campo {label} pode ter até {limit} caracteres.")
    return value


def _seen(value: str) -> str:
    value = value.strip()
    if value and not SEEN_RE.fullmatch(value):
        raise HTTPException(422, "Data inválida (use AAAA-MM-DD).")
    return value


# ---------------------------------------------------------------------------
# Leitura de curadorias
# ---------------------------------------------------------------------------
SUMMARY_SQL = """
SELECT c.*,
       (SELECT COUNT(*) FROM curation_items i WHERE i.curation_id = c.id)      AS item_count,
       (SELECT CAST(AVG(stars) AS DOUBLE PRECISION) FROM curation_ratings r WHERE r.curation_id = c.id) AS rating_avg,
       (SELECT COUNT(*) FROM curation_ratings r WHERE r.curation_id = c.id)    AS rating_count,
       u.username, u.display_name, u.bio, u.avatar_version, u.id AS owner_id
FROM curations c JOIN users u ON u.id = c.user_id
"""


def _covers(db: Database, ids: list[int]) -> dict[int, list[str]]:
    """As 4 primeiras imagens de cada curadoria (para a capa em mosaico)."""
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    rows = db.execute(
        f"""SELECT curation_id, artwork FROM (
                SELECT curation_id, artwork,
                       ROW_NUMBER() OVER (PARTITION BY curation_id ORDER BY position) AS n
                FROM curation_items WHERE curation_id IN ({marks})
            ) WHERE n <= 4 ORDER BY curation_id, n""",
        ids,
    ).fetchall()
    covers: dict[int, list[str]] = {}
    for row in rows:
        covers.setdefault(row["curation_id"], []).append(json.loads(row["artwork"]).get("image"))
    return covers


def _summary(row: Row, covers: dict[int, list[str]]) -> dict[str, Any]:
    owner = {
        "id": row["owner_id"],
        "username": row["username"],
        "display_name": row["display_name"],
        "bio": row["bio"],
        "avatar_version": row["avatar_version"],
    }
    return {
        "id": row["id"],
        "title": row["title"],
        "description": row["description"],
        "is_public": bool(row["is_public"]),
        "item_count": row["item_count"],
        "rating": {
            "avg": round(row["rating_avg"], 2) if row["rating_avg"] is not None else None,
            "count": row["rating_count"],
        },
        "covers": covers.get(row["id"], []),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "owner": profile_card(owner),
    }


def _summaries(db: Database, where: str, params: tuple, order: str = "c.updated_at DESC", limit: int = 100) -> list[dict[str, Any]]:
    rows = db.execute(f"{SUMMARY_SQL} WHERE {where} ORDER BY {order} LIMIT ?", (*params, limit)).fetchall()
    covers = _covers(db, [row["id"] for row in rows])
    return [_summary(row, covers) for row in rows]


def _get_curation(db: Database, curation_id: int, viewer: dict | None) -> Row:
    """A curadoria, se quem pede pode vê-la. Privada de outra pessoa = 404."""
    row = db.execute(f"{SUMMARY_SQL} WHERE c.id = ?", (curation_id,)).fetchone()
    if row is None or (not row["is_public"] and (viewer is None or viewer["id"] != row["user_id"])):
        raise HTTPException(404, "Curadoria não encontrada.")
    return row


def _own_curation(db: Database, curation_id: int, user: dict) -> Row:
    row = _get_curation(db, curation_id, user)
    if row["user_id"] != user["id"]:
        raise HTTPException(403, "Só quem criou a curadoria pode alterá-la.")
    return row


def _touch(db: Database, curation_id: int) -> None:
    db.execute("UPDATE curations SET updated_at = ? WHERE id = ?", (_now(), curation_id))


def _full(db: Database, curation_id: int, viewer: dict | None) -> dict[str, Any]:
    row = _get_curation(db, curation_id, viewer)
    data = _summary(row, _covers(db, [row["id"]]))
    items = db.execute(
        "SELECT artwork, note, seen, added_at FROM curation_items WHERE curation_id = ? ORDER BY position",
        (curation_id,),
    ).fetchall()
    data["items"] = [
        {"artwork": json.loads(i["artwork"]), "note": i["note"], "seen": i["seen"], "added_at": i["added_at"]}
        for i in items
    ]
    data["is_owner"] = bool(viewer and viewer["id"] == row["user_id"])
    my = None
    if viewer:
        r = db.execute(
            "SELECT stars FROM curation_ratings WHERE curation_id = ? AND user_id = ?", (curation_id, viewer["id"])
        ).fetchone()
        my = r["stars"] if r else None
    data["my_rating"] = my
    return data


# ---------------------------------------------------------------------------
# Curadorias: rotas
# ---------------------------------------------------------------------------
@router.get("/curations/mine")
def my_curations(user: dict = Depends(current_user), db: Database = Depends(get_db)) -> dict[str, Any]:
    curations = _summaries(db, "c.user_id = ?", (user["id"],), limit=MAX_CURATIONS)
    rows = db.execute(
        """SELECT i.curation_id, i.artwork_id FROM curation_items i
           JOIN curations c ON c.id = i.curation_id WHERE c.user_id = ?""",
        (user["id"],),
    ).fetchall()
    ids: dict[int, list[str]] = {}
    for row in rows:
        ids.setdefault(row["curation_id"], []).append(row["artwork_id"])
    for curation in curations:
        curation["artwork_ids"] = ids.get(curation["id"], [])
    return {"curations": curations}


@router.get("/curations/discover")
def discover(
    viewer: dict | None = Depends(optional_user), db: Database = Depends(get_db)
) -> dict[str, Any]:
    """Curadorias públicas recentes, com pelo menos uma obra (menos as suas)."""
    me = viewer["id"] if viewer else -1
    where = "c.is_public = 1 AND c.user_id != ? AND EXISTS (SELECT 1 FROM curation_items i WHERE i.curation_id = c.id)"
    return {"curations": _summaries(db, where, (me,), limit=24)}


class ItemIn(BaseModel):
    artwork: ArtworkIn
    note: str = Field("", max_length=4000)
    seen: str = Field("", max_length=20)


class CurationIn(BaseModel):
    title: str = Field(max_length=300)
    description: str = Field("", max_length=4000)
    is_public: bool = True
    items: list[ItemIn] = Field(default_factory=list, max_length=MAX_ITEMS)


@router.post("/curations", status_code=status.HTTP_201_CREATED)
def create_curation(
    data: CurationIn, user: dict = Depends(current_user), db: Database = Depends(get_db)
) -> dict[str, Any]:
    title = _text(data.title, TITLE_MAX, "título", required=True)
    description = _text(data.description, DESCRIPTION_MAX, "descrição", multiline=True)
    count = db.execute("SELECT COUNT(*) AS n FROM curations WHERE user_id = ?", (user["id"],)).fetchone()["n"]
    if count >= MAX_CURATIONS:
        raise HTTPException(422, f"Você chegou ao limite de {MAX_CURATIONS} curadorias.")

    items, seen_ids = [], set()
    for item in data.items:
        art = clean_artwork(item.artwork)
        if art["id"] in seen_ids:
            continue  # repetida: fica só a primeira
        seen_ids.add(art["id"])
        items.append((art, _text(item.note, NOTE_MAX, "nota", multiline=True), _seen(item.seen)))

    now = _now()
    with db:
        new_id = db.execute(
            "INSERT INTO curations (user_id, title, description, is_public, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) RETURNING id",
            (user["id"], title, description, int(data.is_public), now, now),
        ).fetchone()["id"]
        db.executemany(
            "INSERT INTO curation_items (curation_id, artwork_id, artwork, position, note, seen, added_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (new_id, art["id"], json.dumps(art, ensure_ascii=False), pos, note, seen, now)
                for pos, (art, note, seen) in enumerate(items, start=1)
            ],
        )
    return {"curation": _full(db, new_id, user)}


@router.get("/curations/{curation_id}")
def get_curation(
    curation_id: int, viewer: dict | None = Depends(optional_user), db: Database = Depends(get_db)
) -> dict[str, Any]:
    return {"curation": _full(db, curation_id, viewer)}


class CurationPatch(BaseModel):
    title: str | None = Field(None, max_length=300)
    description: str | None = Field(None, max_length=4000)
    is_public: bool | None = None


@router.patch("/curations/{curation_id}")
def update_curation(
    curation_id: int, data: CurationPatch, user: dict = Depends(current_user), db: Database = Depends(get_db)
) -> dict[str, Any]:
    _own_curation(db, curation_id, user)
    changes: dict[str, Any] = {}
    if data.title is not None:
        changes["title"] = _text(data.title, TITLE_MAX, "título", required=True)
    if data.description is not None:
        changes["description"] = _text(data.description, DESCRIPTION_MAX, "descrição", multiline=True)
    if data.is_public is not None:
        changes["is_public"] = int(data.is_public)
    if changes:
        changes["updated_at"] = _now()
        sets = ", ".join(f"{column} = ?" for column in changes)  # colunas da lista fixa acima
        with db:
            db.execute(f"UPDATE curations SET {sets} WHERE id = ?", (*changes.values(), curation_id))
            if changes.get("is_public") == 0:
                # Ficou privada: as notas dos outros deixam de fazer sentido.
                db.execute("DELETE FROM curation_ratings WHERE curation_id = ?", (curation_id,))
    return {"curation": _full(db, curation_id, user)}


@router.delete("/curations/{curation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_curation(
    curation_id: int, user: dict = Depends(current_user), db: Database = Depends(get_db)
) -> None:
    _own_curation(db, curation_id, user)
    with db:
        db.execute("DELETE FROM curations WHERE id = ?", (curation_id,))


@router.post("/curations/{curation_id}/items", status_code=status.HTTP_201_CREATED)
def add_item(
    curation_id: int, data: ItemIn, user: dict = Depends(current_user), db: Database = Depends(get_db)
) -> dict[str, Any]:
    row = _own_curation(db, curation_id, user)
    art = clean_artwork(data.artwork)
    if row["item_count"] >= MAX_ITEMS:
        raise HTTPException(422, f"Uma curadoria pode ter até {MAX_ITEMS} obras.")
    if db.execute(
        "SELECT 1 FROM curation_items WHERE curation_id = ? AND artwork_id = ?", (curation_id, art["id"])
    ).fetchone():
        raise HTTPException(409, "Essa obra já está nesta curadoria.")
    position = db.execute(
        "SELECT COALESCE(MAX(position), 0) + 1 FROM curation_items WHERE curation_id = ?", (curation_id,)
    ).fetchone()[0]
    with db:
        db.execute(
            "INSERT INTO curation_items (curation_id, artwork_id, artwork, position, note, seen, added_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (curation_id, art["id"], json.dumps(art, ensure_ascii=False), position,
             _text(data.note, NOTE_MAX, "nota", multiline=True), _seen(data.seen), _now()),
        )
        _touch(db, curation_id)
    return {"ok": True}


class ItemPatch(BaseModel):
    artwork_id: str = Field(max_length=200)
    note: str | None = Field(None, max_length=4000)
    seen: str | None = Field(None, max_length=20)


@router.patch("/curations/{curation_id}/items")
def update_item(
    curation_id: int, data: ItemPatch, user: dict = Depends(current_user), db: Database = Depends(get_db)
) -> dict[str, Any]:
    _own_curation(db, curation_id, user)
    changes: dict[str, str] = {}
    if data.note is not None:
        changes["note"] = _text(data.note, NOTE_MAX, "nota", multiline=True)
    if data.seen is not None:
        changes["seen"] = _seen(data.seen)
    if changes:
        sets = ", ".join(f"{column} = ?" for column in changes)
        with db:
            cur = db.execute(
                f"UPDATE curation_items SET {sets} WHERE curation_id = ? AND artwork_id = ?",
                (*changes.values(), curation_id, data.artwork_id),
            )
        if cur.rowcount == 0:
            raise HTTPException(404, "Essa obra não está nesta curadoria.")
    return {"ok": True}


@router.delete("/curations/{curation_id}/items", status_code=status.HTTP_204_NO_CONTENT)
def remove_item(
    curation_id: int,
    artwork_id: str = Query(max_length=200),
    user: dict = Depends(current_user),
    db: Database = Depends(get_db),
) -> None:
    _own_curation(db, curation_id, user)
    with db:
        db.execute("DELETE FROM curation_items WHERE curation_id = ? AND artwork_id = ?", (curation_id, artwork_id))
        _touch(db, curation_id)


class OrderIn(BaseModel):
    artwork_ids: list[str] = Field(max_length=MAX_ITEMS)


@router.put("/curations/{curation_id}/order")
def reorder(
    curation_id: int, data: OrderIn, user: dict = Depends(current_user), db: Database = Depends(get_db)
) -> dict[str, Any]:
    _own_curation(db, curation_id, user)
    current = {r[0] for r in db.execute("SELECT artwork_id FROM curation_items WHERE curation_id = ?", (curation_id,))}
    if set(data.artwork_ids) != current or len(data.artwork_ids) != len(current):
        raise HTTPException(409, "A curadoria mudou enquanto você reordenava. Recarregue a página.")
    with db:
        db.executemany(
            "UPDATE curation_items SET position = ? WHERE curation_id = ? AND artwork_id = ?",
            [(pos, curation_id, aid) for pos, aid in enumerate(data.artwork_ids, start=1)],
        )
    return {"ok": True}


class RatingIn(BaseModel):
    stars: int = Field(ge=1, le=5)


def _rating(db: Database, curation_id: int, user_id: int) -> dict[str, Any]:
    row = db.execute(
        "SELECT CAST(AVG(stars) AS DOUBLE PRECISION) AS avg, COUNT(*) AS n FROM curation_ratings WHERE curation_id = ?", (curation_id,)
    ).fetchone()
    mine = db.execute(
        "SELECT stars FROM curation_ratings WHERE curation_id = ? AND user_id = ?", (curation_id, user_id)
    ).fetchone()
    return {
        "rating": {"avg": round(row["avg"], 2) if row["avg"] is not None else None, "count": row["n"]},
        "my_rating": mine["stars"] if mine else None,
    }


@router.put("/curations/{curation_id}/rating")
def rate(
    curation_id: int, data: RatingIn, user: dict = Depends(current_user), db: Database = Depends(get_db)
) -> dict[str, Any]:
    row = _get_curation(db, curation_id, user)
    if row["user_id"] == user["id"]:
        raise HTTPException(403, "Você não pode avaliar a sua própria curadoria.")
    if not row["is_public"]:
        raise HTTPException(404, "Curadoria não encontrada.")
    with db:
        db.execute(
            "INSERT INTO curation_ratings (curation_id, user_id, stars, rated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(curation_id, user_id) DO UPDATE SET stars = excluded.stars, rated_at = excluded.rated_at",
            (curation_id, user["id"], data.stars, _now()),
        )
    return _rating(db, curation_id, user["id"])


@router.delete("/curations/{curation_id}/rating")
def unrate(
    curation_id: int, user: dict = Depends(current_user), db: Database = Depends(get_db)
) -> dict[str, Any]:
    _get_curation(db, curation_id, user)
    with db:
        db.execute("DELETE FROM curation_ratings WHERE curation_id = ? AND user_id = ?", (curation_id, user["id"]))
    return _rating(db, curation_id, user["id"])


# ---------------------------------------------------------------------------
# Pessoas
# ---------------------------------------------------------------------------
def _escape_like(text: str) -> str:
    """Num LIKE, % e _ são curingas. O _ é comum em @usuário, então vira caractere comum."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


PEOPLE_SQL = """
SELECT u.*,
       (SELECT COUNT(*) FROM follows f WHERE f.followee_id = u.id)                   AS followers,
       (SELECT COUNT(*) FROM curations c WHERE c.user_id = u.id AND c.is_public = 1) AS public_curations,
       EXISTS (SELECT 1 FROM follows f WHERE f.follower_id = ? AND f.followee_id = u.id) AS is_following
FROM users u
"""


@router.get("/users")
def search_people(
    q: str = Query("", max_length=60),
    viewer: dict | None = Depends(optional_user),
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    """Busca pelo nome ou @, sem diferenciar acentos e maiúsculas ("joao" acha "João").

    Sem termo (ou com menos de 2 letras), devolve sugestões: pessoas com curadorias
    públicas, das mais seguidas para as menos, sem você e sem quem você já segue.
    """
    me = viewer["id"] if viewer else -1
    tokens = [t.lstrip("@") for t in search_key(q).split()][:5]
    tokens = [t for t in tokens if t]

    if sum(len(t) for t in tokens) >= 2:
        like = " AND ".join("u.name_key LIKE ? ESCAPE '\\'" for _ in tokens)
        first = tokens[0]
        rows = db.execute(
            f"""{PEOPLE_SQL} WHERE {like}
                ORDER BY (u.username = ?) DESC, (u.username LIKE ? ESCAPE '\\') DESC, followers DESC, u.username
                LIMIT 30""",
            (me, *[f"%{_escape_like(t)}%" for t in tokens], first, f"{_escape_like(first)}%"),
        ).fetchall()
        mode = "search"
    else:
        rows = db.execute(
            f"""{PEOPLE_SQL} WHERE u.id != ?
                  AND NOT EXISTS (SELECT 1 FROM follows f WHERE f.follower_id = ? AND f.followee_id = u.id)
                  AND EXISTS (SELECT 1 FROM curations c WHERE c.user_id = u.id AND c.is_public = 1)
                ORDER BY followers DESC, public_curations DESC, u.created_at DESC
                LIMIT 12""",
            (me, me, me),
        ).fetchall()
        mode = "suggestions"

    people = [
        {
            **profile_card(row),
            "followers": row["followers"],
            "public_curations": row["public_curations"],
            "is_following": bool(row["is_following"]),
            "is_me": row["id"] == me,
        }
        for row in rows
    ]
    return {"mode": mode, "users": people}


def _user_by_name(db: Database, username: str) -> Row:
    row = db.execute("SELECT * FROM users WHERE username = ?", (username.lstrip("@").lower(),)).fetchone()
    if row is None:
        raise HTTPException(404, "Pessoa não encontrada.")
    return row


def _follow_counts(db: Database, user_id: int, viewer: dict | None) -> dict[str, Any]:
    followers = db.execute("SELECT COUNT(*) FROM follows WHERE followee_id = ?", (user_id,)).fetchone()[0]
    following = db.execute("SELECT COUNT(*) FROM follows WHERE follower_id = ?", (user_id,)).fetchone()[0]
    is_following = bool(viewer) and db.execute(
        "SELECT 1 FROM follows WHERE follower_id = ? AND followee_id = ?", (viewer["id"], user_id)
    ).fetchone() is not None
    return {"followers": followers, "following": following, "is_following": is_following}


@router.get("/users/{username}")
def public_profile(
    username: str, viewer: dict | None = Depends(optional_user), db: Database = Depends(get_db)
) -> dict[str, Any]:
    user = _user_by_name(db, username)
    is_me = bool(viewer and viewer["id"] == user["id"])
    where = "c.user_id = ?" if is_me else "c.user_id = ? AND c.is_public = 1"
    top = db.execute("SELECT artwork FROM top_artworks WHERE user_id = ? ORDER BY position", (user["id"],)).fetchall()
    return {
        "user": profile_card(user),
        "is_me": is_me,
        **_follow_counts(db, user["id"], viewer),
        "top5": [json.loads(row["artwork"]) for row in top],
        "curations": _summaries(db, where, (user["id"],), limit=MAX_CURATIONS),
    }


@router.post("/users/{username}/follow")
def follow(username: str, viewer: dict = Depends(current_user), db: Database = Depends(get_db)) -> dict[str, Any]:
    user = _user_by_name(db, username)
    if user["id"] == viewer["id"]:
        raise HTTPException(422, "Você não pode seguir a si mesmo.")
    with db:
        db.execute(
            "INSERT INTO follows (follower_id, followee_id, created_at) VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
            (viewer["id"], user["id"], _now()),
        )
    return _follow_counts(db, user["id"], viewer)


@router.delete("/users/{username}/follow")
def unfollow(username: str, viewer: dict = Depends(current_user), db: Database = Depends(get_db)) -> dict[str, Any]:
    user = _user_by_name(db, username)
    with db:
        db.execute("DELETE FROM follows WHERE follower_id = ? AND followee_id = ?", (viewer["id"], user["id"]))
    return _follow_counts(db, user["id"], viewer)


def _people(db: Database, sql: str, user_id: int) -> list[dict[str, Any]]:
    return [profile_card(row) for row in db.execute(sql, (user_id,)).fetchall()]


@router.get("/users/{username}/followers")
def followers(username: str, db: Database = Depends(get_db)) -> dict[str, Any]:
    user = _user_by_name(db, username)
    sql = """SELECT u.* FROM follows f JOIN users u ON u.id = f.follower_id
             WHERE f.followee_id = ? ORDER BY f.created_at DESC LIMIT 500"""
    return {"users": _people(db, sql, user["id"])}


@router.get("/users/{username}/following")
def following(username: str, db: Database = Depends(get_db)) -> dict[str, Any]:
    user = _user_by_name(db, username)
    sql = """SELECT u.* FROM follows f JOIN users u ON u.id = f.followee_id
             WHERE f.follower_id = ? ORDER BY f.created_at DESC LIMIT 500"""
    return {"users": _people(db, sql, user["id"])}


@router.get("/feed")
def feed(viewer: dict = Depends(current_user), db: Database = Depends(get_db)) -> dict[str, Any]:
    """Curadorias públicas (com obras) de quem você segue, das mais recentes para as mais antigas."""
    where = """c.is_public = 1
               AND c.user_id IN (SELECT followee_id FROM follows WHERE follower_id = ?)
               AND EXISTS (SELECT 1 FROM curation_items i WHERE i.curation_id = c.id)"""
    following_count = db.execute("SELECT COUNT(*) FROM follows WHERE follower_id = ?", (viewer["id"],)).fetchone()[0]
    return {"curations": _summaries(db, where, (viewer["id"],), limit=40), "following": following_count}
