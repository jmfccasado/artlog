"""Contas de usuário do Artlog: cadastro, login, logout e sessão.

Como funciona:
- Os dados ficam num arquivo SQLite (artlog.db, ao lado deste arquivo).
- A senha nunca é guardada: guardamos um hash scrypt (da biblioteca padrão do Python).
- Ao entrar, o servidor cria uma sessão e manda um cookie HttpOnly com um token
  aleatório. O JavaScript da página não consegue ler esse cookie, e o banco guarda
  só o hash do token (se o banco vazar, as sessões não podem ser reaproveitadas).
- Sair apaga a sessão no servidor, então o token deixa de valer na hora.

Para proteger outras rotas no futuro (ex.: a coleção no servidor):

    from auth import current_user

    @app.get("/minha-rota")
    def minha_rota(user: dict = Depends(current_user)):
        ...
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path
from typing import Any, Iterator

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("ARTLOG_DB", BASE_DIR / "artlog.db"))

SESSION_COOKIE = "artlog_session"
SESSION_DAYS = 30
# Em produção (HTTPS), defina COOKIE_SECURE=1 no .env para o cookie só trafegar por HTTPS.
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "").strip().lower() in {"1", "true", "yes"}

USERNAME_RE = re.compile(r"[a-z0-9_]{3,20}")
# Validação simples de propósito: confirma o formato básico; o resto só um e-mail de confirmação garante.
EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
PASSWORD_MIN = 8
PASSWORD_MAX = 128

router = APIRouter(prefix="/auth", tags=["contas"])


# ---------------------------------------------------------------------------
# Banco de dados
# ---------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at    INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);

-- Foto de perfil (uma por usuário), guardada no próprio banco.
CREATE TABLE IF NOT EXISTS avatars (
    user_id      INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    content_type TEXT NOT NULL,
    data         BLOB NOT NULL
);

-- Top 5 de obras. Guarda uma cópia da obra (título, imagem...) para o perfil
-- aparecer sem precisar consultar os museus de novo.
CREATE TABLE IF NOT EXISTS top_artworks (
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    position   INTEGER NOT NULL CHECK (position BETWEEN 1 AND 5),
    artwork_id TEXT NOT NULL,
    artwork    TEXT NOT NULL,  -- JSON
    PRIMARY KEY (user_id, position),
    UNIQUE (user_id, artwork_id)
);
"""

# Colunas que entraram depois da primeira versão. Bancos antigos ganham as colunas
# automaticamente ao iniciar, sem perder as contas que já existem.
USER_COLUMNS = {
    "display_name": "TEXT NOT NULL DEFAULT ''",
    "bio": "TEXT NOT NULL DEFAULT ''",
    "avatar_version": "INTEGER",  # muda a cada foto nova, para o navegador não usar a antiga do cache
}


def _connect() -> sqlite3.Connection:
    # check_same_thread=False: cada conexão é usada por uma só requisição, mas o FastAPI
    # pode abri-la numa thread e usá-la em outra (rotas async, como o envio da foto).
    conn = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with closing(_connect()) as conn:
        conn.executescript(SCHEMA)
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
        with conn:
            for name, definition in USER_COLUMNS.items():
                if name not in existing:
                    conn.execute(f"ALTER TABLE users ADD COLUMN {name} {definition}")


def get_db() -> Iterator[sqlite3.Connection]:
    """Uma conexão por requisição (as rotas rodam em várias threads)."""
    conn = _connect()
    try:
        yield conn
    finally:
        conn.close()


init_db()


# ---------------------------------------------------------------------------
# Senhas (scrypt, da biblioteca padrão: nenhuma dependência extra)
# ---------------------------------------------------------------------------
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32)
    b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${b64(salt)}${b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, digest = stored.split("$")
        if algo != "scrypt":
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(
            password.encode(), salt=base64.b64decode(salt),
            n=int(n), r=int(r), p=int(p), dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


# Usado quando o usuário não existe: o login leva o mesmo tempo nos dois casos,
# então medir a demora não revela quais e-mails têm conta.
_DUMMY_HASH = hash_password(secrets.token_hex(16))


# ---------------------------------------------------------------------------
# Limite de tentativas de login (em memória; zera quando o servidor reinicia)
# ---------------------------------------------------------------------------
MAX_FAILURES = 8
FAILURE_WINDOW = 15 * 60  # segundos
_failures: dict[str, list[float]] = {}
_failures_lock = threading.Lock()


def _recent_failures(key: str) -> list[float]:
    cutoff = time.time() - FAILURE_WINDOW
    recent = [t for t in _failures.get(key, []) if t > cutoff]
    if recent:
        _failures[key] = recent
    else:
        _failures.pop(key, None)
    return recent


def _check_throttle(key: str) -> None:
    with _failures_lock:
        if len(_recent_failures(key)) >= MAX_FAILURES:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Muitas tentativas. Espere alguns minutos e tente de novo.",
            )


def _record_failure(key: str) -> None:
    with _failures_lock:
        _recent_failures(key)
        _failures.setdefault(key, []).append(time.time())


def _clear_failures(key: str) -> None:
    with _failures_lock:
        _failures.pop(key, None)


# ---------------------------------------------------------------------------
# Sessões
# ---------------------------------------------------------------------------
def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _start_session(db: sqlite3.Connection, response: Response, user_id: int) -> None:
    token = secrets.token_urlsafe(32)
    now = int(time.time())
    expires = now + SESSION_DAYS * 86400
    with db:
        db.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))  # faxina das vencidas
        db.execute(
            "INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (_token_hash(token), user_id, now, expires),
        )
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_DAYS * 86400,
        httponly=True,       # o JavaScript da página não lê o cookie
        samesite="lax",      # outros sites não conseguem enviar o cookie num POST
        secure=COOKIE_SECURE,
        path="/",
    )


def avatar_url(user: sqlite3.Row) -> str | None:
    version = user["avatar_version"]
    return f"/avatars/{user['id']}?v={version}" if version else None


def public_user(user: sqlite3.Row) -> dict[str, Any]:
    """O que pode sair para o navegador (nunca o hash da senha)."""
    return {
        "id": user["id"],
        "username": user["username"],
        "email": user["email"],
        "display_name": user["display_name"],
        "bio": user["bio"],
        "avatar_url": avatar_url(user),
    }


_public = public_user


def optional_user(request: Request, db: sqlite3.Connection = Depends(get_db)) -> dict[str, Any] | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    row = db.execute(
        """SELECT u.* FROM sessions s
           JOIN users u ON u.id = s.user_id
           WHERE s.token_hash = ? AND s.expires_at > ?""",
        (_token_hash(token), int(time.time())),
    ).fetchone()
    return _public(row) if row else None


def current_user(user: dict[str, Any] | None = Depends(optional_user)) -> dict[str, Any]:
    """Dependência para rotas que exigem login."""
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Entre na sua conta para continuar.")
    return user


# ---------------------------------------------------------------------------
# Rotas
# ---------------------------------------------------------------------------
class RegisterIn(BaseModel):
    username: str = Field(max_length=100)
    email: str = Field(max_length=254)
    password: str = Field(max_length=1000)


class LoginIn(BaseModel):
    login: str = Field(max_length=254, description="E-mail ou nome de usuário")
    password: str = Field(max_length=1000)


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(data: RegisterIn, response: Response, db: sqlite3.Connection = Depends(get_db)) -> dict[str, Any]:
    username = data.username.strip().lstrip("@").lower()
    email = data.email.strip().lower()

    if not USERNAME_RE.fullmatch(username):
        raise HTTPException(422, "O nome de usuário deve ter de 3 a 20 caracteres: letras sem acento, números ou _.")
    if not EMAIL_RE.fullmatch(email):
        raise HTTPException(422, "Informe um e-mail válido.")
    if not PASSWORD_MIN <= len(data.password) <= PASSWORD_MAX:
        raise HTTPException(422, f"A senha deve ter entre {PASSWORD_MIN} e {PASSWORD_MAX} caracteres.")

    taken = db.execute(
        "SELECT username = ? AS same_user, email = ? AS same_email FROM users WHERE username = ? OR email = ?",
        (username, email, username, email),
    ).fetchall()
    if any(row["same_email"] for row in taken):
        raise HTTPException(409, "Já existe uma conta com esse e-mail.")
    if taken:
        raise HTTPException(409, "Esse nome de usuário já está em uso.")

    try:
        with db:
            cur = db.execute(
                "INSERT INTO users (username, email, password_hash, created_at) VALUES (?, ?, ?, ?)",
                (username, email, hash_password(data.password), int(time.time())),
            )
    except sqlite3.IntegrityError:  # duas pessoas criando a mesma conta ao mesmo tempo
        raise HTTPException(409, "Esse nome de usuário ou e-mail acabou de ser usado. Tente outro.") from None

    user = db.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone()
    _start_session(db, response, user["id"])  # já entra depois de criar a conta
    return {"user": _public(user)}


@router.post("/login")
def login(data: LoginIn, response: Response, db: sqlite3.Connection = Depends(get_db)) -> dict[str, Any]:
    ident = data.login.strip().lstrip("@").lower()
    _check_throttle(ident)

    user = db.execute("SELECT * FROM users WHERE email = ? OR username = ?", (ident, ident)).fetchone()
    ok = verify_password(data.password, user["password_hash"] if user else _DUMMY_HASH)
    if not (user and ok):
        _record_failure(ident)
        # Mesma mensagem nos dois casos: não revela se a conta existe.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "E-mail/usuário ou senha incorretos.")

    _clear_failures(ident)
    _start_session(db, response, user["id"])
    return {"user": _public(user)}


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, db: sqlite3.Connection = Depends(get_db)) -> Response:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        with db:
            db.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, samesite="lax", secure=COOKIE_SECURE)
    return response


@router.get("/me")
def me(user: dict[str, Any] | None = Depends(optional_user)) -> dict[str, Any]:
    """Quem está logado. Devolve {"user": null} para visitantes (sem erro no console)."""
    return {"user": user}