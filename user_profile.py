"""Perfil do usuário: dados, senha, foto e Top 5 de obras.

Rotas (todas exigem login, exceto a foto):
    GET    /profile           dados do perfil + Top 5
    PATCH  /profile           nome de exibição, bio, @usuário, e-mail
    POST   /profile/password  troca a senha (encerra as outras sessões)
    PUT    /profile/avatar    envia a foto (corpo = a imagem, até 1 MB)
    DELETE /profile/avatar    remove a foto
    PUT    /profile/top5      salva o Top 5 inteiro, na ordem
    GET    /avatars/{id}      mostra a foto de alguém (pública)
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from auth import (
    EMAIL_RE,
    PASSWORD_MAX,
    PASSWORD_MIN,
    SESSION_COOKIE,
    USERNAME_RE,
    _token_hash,
    current_user,
    get_db,
    hash_password,
    public_user,
    verify_password,
)

router = APIRouter(tags=["perfil"])

DISPLAY_NAME_MAX = 50
BIO_MAX = 300
TOP_SIZE = 5
AVATAR_MAX_BYTES = 1024 * 1024  # a página já reduz a foto para 400x400 antes de enviar

# Caracteres de controle (quebras de linha são permitidas só na bio).
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def _clean(text: str, *, multiline: bool = False) -> str:
    text = _CONTROL.sub("", text.replace("\r\n", "\n"))
    if not multiline:
        text = " ".join(text.split())
    return text.strip()


def _user_row(db: sqlite3.Connection, user_id: int) -> sqlite3.Row:
    return db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def _top5(db: sqlite3.Connection, user_id: int) -> list[dict[str, Any]]:
    rows = db.execute(
        "SELECT artwork FROM top_artworks WHERE user_id = ? ORDER BY position", (user_id,)
    ).fetchall()
    return [json.loads(row["artwork"]) for row in rows]


# ---------------------------------------------------------------------------
# Perfil
# ---------------------------------------------------------------------------
@router.get("/profile")
def get_profile(user: dict = Depends(current_user), db: sqlite3.Connection = Depends(get_db)) -> dict[str, Any]:
    return {"user": user, "top5": _top5(db, user["id"])}


class ProfileIn(BaseModel):
    # Todos opcionais: só o que vier é alterado.
    display_name: str | None = Field(None, max_length=200)
    bio: str | None = Field(None, max_length=2000)
    username: str | None = Field(None, max_length=100)
    email: str | None = Field(None, max_length=254)


@router.patch("/profile")
def update_profile(
    data: ProfileIn, user: dict = Depends(current_user), db: sqlite3.Connection = Depends(get_db)
) -> dict[str, Any]:
    changes: dict[str, str] = {}

    if data.display_name is not None:
        name = _clean(data.display_name)
        if len(name) > DISPLAY_NAME_MAX:
            raise HTTPException(422, f"O nome pode ter até {DISPLAY_NAME_MAX} caracteres.")
        changes["display_name"] = name

    if data.bio is not None:
        bio = _clean(data.bio, multiline=True)
        bio = re.sub(r"\n{3,}", "\n\n", bio)  # no máximo uma linha em branco seguida
        if len(bio) > BIO_MAX:
            raise HTTPException(422, f"A bio pode ter até {BIO_MAX} caracteres.")
        changes["bio"] = bio

    if data.username is not None:
        username = data.username.strip().lstrip("@").lower()
        if not USERNAME_RE.fullmatch(username):
            raise HTTPException(422, "O nome de usuário deve ter de 3 a 20 caracteres: letras sem acento, números ou _.")
        if username != user["username"]:
            if db.execute("SELECT 1 FROM users WHERE username = ? AND id != ?", (username, user["id"])).fetchone():
                raise HTTPException(409, "Esse nome de usuário já está em uso.")
            changes["username"] = username

    if data.email is not None:
        email = data.email.strip().lower()
        if not EMAIL_RE.fullmatch(email):
            raise HTTPException(422, "Informe um e-mail válido.")
        if email != user["email"]:
            if db.execute("SELECT 1 FROM users WHERE email = ? AND id != ?", (email, user["id"])).fetchone():
                raise HTTPException(409, "Já existe uma conta com esse e-mail.")
            changes["email"] = email

    if changes:
        # Os nomes das colunas vêm da lista fixa acima, nunca do usuário.
        assignments = ", ".join(f"{column} = ?" for column in changes)
        try:
            with db:
                db.execute(f"UPDATE users SET {assignments} WHERE id = ?", (*changes.values(), user["id"]))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Esse nome de usuário ou e-mail acabou de ser usado. Tente outro.") from None

    return {"user": public_user(_user_row(db, user["id"]))}


class PasswordIn(BaseModel):
    current_password: str = Field(max_length=1000)
    new_password: str = Field(max_length=1000)


@router.post("/profile/password")
def change_password(
    data: PasswordIn,
    request: Request,
    user: dict = Depends(current_user),
    db: sqlite3.Connection = Depends(get_db),
) -> dict[str, Any]:
    row = _user_row(db, user["id"])
    if not verify_password(data.current_password, row["password_hash"]):
        raise HTTPException(403, "A senha atual está incorreta.")
    if not PASSWORD_MIN <= len(data.new_password) <= PASSWORD_MAX:
        raise HTTPException(422, f"A nova senha deve ter entre {PASSWORD_MIN} e {PASSWORD_MAX} caracteres.")

    current_token = request.cookies.get(SESSION_COOKIE, "")
    with db:
        db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(data.new_password), user["id"]))
        # Se alguém tinha a senha antiga e estava logado em outro aparelho, perde o acesso.
        db.execute(
            "DELETE FROM sessions WHERE user_id = ? AND token_hash != ?",
            (user["id"], _token_hash(current_token)),
        )
    return {"ok": True}


# ---------------------------------------------------------------------------
# Foto de perfil
# ---------------------------------------------------------------------------
def _image_type(data: bytes) -> str | None:
    """Confere o tipo pelos primeiros bytes do arquivo, não pelo que o navegador diz."""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


@router.put("/profile/avatar")
async def upload_avatar(
    request: Request, user: dict = Depends(current_user), db: sqlite3.Connection = Depends(get_db)
) -> dict[str, Any]:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > AVATAR_MAX_BYTES:
        raise HTTPException(413, "A foto é grande demais (máximo de 1 MB).")

    # Lê aos poucos e para no limite, mesmo que o cabeçalho minta o tamanho.
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > AVATAR_MAX_BYTES:
            raise HTTPException(413, "A foto é grande demais (máximo de 1 MB).")

    content_type = _image_type(bytes(body))
    if content_type is None:
        raise HTTPException(415, "Formato não suportado. Use uma imagem JPG, PNG ou WebP.")

    version = int(time.time() * 1000)
    with db:
        db.execute(
            "INSERT INTO avatars (user_id, content_type, data) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET content_type = excluded.content_type, data = excluded.data",
            (user["id"], content_type, bytes(body)),
        )
        db.execute("UPDATE users SET avatar_version = ? WHERE id = ?", (version, user["id"]))
    return {"user": public_user(_user_row(db, user["id"]))}


@router.delete("/profile/avatar")
def delete_avatar(user: dict = Depends(current_user), db: sqlite3.Connection = Depends(get_db)) -> dict[str, Any]:
    with db:
        db.execute("DELETE FROM avatars WHERE user_id = ?", (user["id"],))
        db.execute("UPDATE users SET avatar_version = NULL WHERE id = ?", (user["id"],))
    return {"user": public_user(_user_row(db, user["id"]))}


@router.get("/avatars/{user_id}", include_in_schema=False)
def get_avatar(user_id: int, db: sqlite3.Connection = Depends(get_db)) -> Response:
    row = db.execute("SELECT content_type, data FROM avatars WHERE user_id = ?", (user_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Sem foto.")
    return Response(
        content=row["data"],
        media_type=row["content_type"],
        headers={
            # O endereço muda (?v=...) a cada foto nova, então pode ficar em cache por bastante tempo.
            "Cache-Control": "public, max-age=31536000, immutable",
            "X-Content-Type-Options": "nosniff",
        },
    )


# ---------------------------------------------------------------------------
# Top 5
# ---------------------------------------------------------------------------
class ArtworkIn(BaseModel):
    """Mesmo formato de obra que a busca devolve (make_artwork no main.py)."""

    id: str = Field(min_length=3, max_length=200, pattern=r"^[a-z]+_\S+$")
    title: str = Field("", max_length=500)
    artist: str = Field("", max_length=500)
    year: str = Field("", max_length=200)
    museum: str = Field("", max_length=200)
    image: str = Field(max_length=1000)
    url: str | None = Field(None, max_length=1000)
    credit: str | None = Field(None, max_length=500)


class Top5In(BaseModel):
    artworks: list[ArtworkIn] = Field(max_length=TOP_SIZE)


@router.put("/profile/top5")
def save_top5(
    data: Top5In, user: dict = Depends(current_user), db: sqlite3.Connection = Depends(get_db)
) -> dict[str, Any]:
    ids = [art.id for art in data.artworks]
    if len(set(ids)) != len(ids):
        raise HTTPException(422, "A mesma obra aparece duas vezes no Top 5.")
    for art in data.artworks:
        # Só imagens das origens que o próprio app usa (evita links estranhos no perfil).
        if not (art.image.startswith("https://") or re.fullmatch(r"/artic-image/[A-Za-z0-9_-]{1,64}", art.image)):
            raise HTTPException(422, "Imagem de obra inválida.")
        if art.url and not art.url.startswith("https://"):
            art.url = None

    with db:
        db.execute("DELETE FROM top_artworks WHERE user_id = ?", (user["id"],))
        db.executemany(
            "INSERT INTO top_artworks (user_id, position, artwork_id, artwork) VALUES (?, ?, ?, ?)",
            [
                (user["id"], position, art.id, json.dumps(art.model_dump(), ensure_ascii=False))
                for position, art in enumerate(data.artworks, start=1)
            ],
        )
    return {"top5": _top5(db, user["id"])}