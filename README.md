<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/banner-dark.png">
  <img src="docs/banner-light.png" alt="artlog: um diário de arte e museus. Busque em 10 acervos, salve o que viu, monte seu Top 5.">
</picture>

<br>

**artlog** é um Letterboxd para arte. Você busca obras nos acervos abertos de dez museus ao mesmo tempo, salva as que viu, anota onde e quando, e monta um Top 5 que fica no seu perfil.

<br>

## `01` &nbsp; O que dá para fazer

| Recurso | Como funciona |
|---|---|
| **Explorar** | Uma busca só, em dez museus ao mesmo tempo. Os resultados vêm intercalados, para nenhum acervo dominar o começo da lista. |
| **Surpreenda-me** | Escolhe um tema (*ukiyo-e*, *still life*, *seascape*…) e busca por você. |
| **Minha coleção** | Toque no ♡ para salvar a obra. Abra a obra para anotar a data em que a viu e as suas impressões. Dá para exportar em JSON ou CSV e importar de volta. |
| **Conta** | Cadastro e login com e-mail ou @usuário. |
| **Perfil** | Foto, nome, bio e @usuário editáveis, além da troca de senha. |
| **Top 5** | As cinco obras que dizem quem você é, na ordem que você escolher. Ficam salvas na conta. |
| **Cor de destaque** | Vermelho, amarelo ou azul. O site lembra a sua escolha. |

<br>

<p align="center">
  <img src="docs/tela-perfil.png" width="49%" alt="Aba Perfil, com foto, nome, bio e os cinco espaços do Top 5">
  <img src="docs/tela-cadastro.png" width="49%" alt="Janela de criar conta, com nome de usuário, e-mail e senha">
</p>

<br>

## `02` &nbsp; Acervos

| Museu | Origem dos dados | Observação |
|---|---|---|
| The Metropolitan Museum of Art | API pública | Só as obras de domínio público vêm com imagem |
| Art Institute of Chicago | API pública | As imagens passam pelo servidor do artlog (`/artic-image/…`) |
| Cleveland Museum of Art | Open Access API | |
| SMK, National Gallery of Denmark | API pública | Só domínio público |
| Victoria and Albert Museum | API v2 | |
| Musée du Louvre | Página de busca do site + JSON de cada obra | Exibe crédito da foto e link para a ficha da obra, como pedem os termos de uso |
| Smithsonian (museus de arte) | Open Access API | **Precisa de chave** (veja a seção `04`). Só imagens CC0 |
| Whitney Museum of American Art | API pública | Uso não comercial, com crédito e link |
| J. Paul Getty Museum | SPARQL + Linked Art | Só imagens CC0 (Open Content) |
| Solomon R. Guggenheim Museum | API do site (WordPress) | Com crédito e link |

Quando um museu falha, os outros continuam funcionando, e a página avisa qual não respondeu e por quê.

<br>

## `03` &nbsp; Como rodar

Você precisa do **Python 3.10** ou mais novo (o projeto foi feito com o 3.12). No Mac, abra o **Terminal**:

```bash
# 1. Entre na pasta do projeto (a que tem o main.py)
cd caminho/para/artlog

# 2. Só na primeira vez: crie o ambiente e instale as dependências
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 3. Rode o servidor
python main.py
```

Abra **http://127.0.0.1:8000** no navegador. Para desligar o servidor, aperte **Ctrl + C** no Terminal.

Da segunda vez em diante, basta rodar:

```bash
cd caminho/para/artlog
source venv/bin/activate
python main.py
```

> **`source: no such file or directory: venv/bin/activate`**: você está fora da pasta do projeto ou ainda não criou o `venv`. Use `ls` para ver onde está: o `main.py` tem que aparecer na lista.
>
> **VS Code sublinhando `import fastapi`**: é só o editor. Aperte **Cmd + Shift + P**, escolha **Python: Select Interpreter** e selecione o `venv`.

<br>

## `04` &nbsp; Configuração (`.env`)

Crie um arquivo `.env` na pasta do projeto. Todas as variáveis são opcionais.

```ini
# Chave gratuita do Smithsonian: https://api.data.gov/signup
SI_API_KEY=sua-chave-aqui

# Em produção, com HTTPS: o cookie da sessão só trafega por HTTPS
COOKIE_SECURE=1

# Onde fica o banco de dados (padrão: artlog.db na pasta do projeto)
ARTLOG_DB=/caminho/para/artlog.db
```

O `.env` e o `artlog.db` estão no `.gitignore` e nunca devem ir para o GitHub, porque têm a sua chave e as contas dos usuários.

<br>

## `05` &nbsp; Contas e segurança

- **Senhas:** o banco nunca guarda a senha, só um hash **scrypt** com sal aleatório. O scrypt vem com o Python, sem biblioteca extra.
- **Sessão:** fica num cookie `HttpOnly` e `SameSite=Lax`, que o JavaScript da página não consegue ler. O banco guarda só o hash do token. Sair encerra a sessão na hora.
- **Login:** depois de 8 senhas erradas, a conta fica bloqueada por 15 minutos. A mensagem de erro é a mesma quando a conta não existe e quando a senha está errada, então ninguém consegue descobrir quais e-mails têm cadastro.
- **Troca de senha:** os outros aparelhos onde a conta estava aberta saem dela.
- **Foto de perfil:** o navegador corta a foto e reduz para 400×400 antes de enviar, então dados como a localização GPS da foto não vão junto. O servidor confere os bytes do arquivo (não confia no tipo informado pelo navegador) e aceita até 1 MB.

> A **coleção** (♡ e notas) ainda fica só no navegador, no `localStorage`. O **Top 5** e o **perfil** ficam no servidor.

<br>

## `06` &nbsp; Estrutura

```
artlog/
├── main.py              servidor FastAPI, busca nos museus e entrega do site
├── auth.py              banco SQLite, cadastro, login, logout e sessão
├── user_profile.py      perfil, troca de senha, foto e Top 5
├── index.html           a página inteira (HTML + JavaScript)
├── style.css            visual: cores, tipografia e componentes
├── requirements.txt     dependências Python
├── docs/                imagens deste README
│
├── diagnose.py          testa as APIs dos museus direto da sua máquina
├── check_images.py      confere se as imagens de cada museu carregam
├── check_getty.py       diagnóstico da integração com o Getty
├── check_smithsonian.py diagnóstico da chave e dos filtros do Smithsonian
├── chech_louvre.py      diagnóstico da busca do Louvre
└── script.js            versão antiga do front-end (o index.html não usa mais)
```

<br>

## `07` &nbsp; API

| Método | Rota | O que faz |
|---|---|---|
| `GET` | `/search?q=monet&museum=all&limit_per_museum=3` | Busca nos museus (`museum` pode ser `all`, `met`, `artic`, `louvre`…) |
| `POST` | `/auth/register` | Cria a conta: `{username, email, password}` |
| `POST` | `/auth/login` | Entra: `{login, password}`, em que `login` é o e-mail ou o @usuário |
| `POST` | `/auth/logout` | Sai |
| `GET` | `/auth/me` | Quem está logado (`{"user": null}` para visitantes) |
| `GET` | `/profile` | Perfil e Top 5 |
| `PATCH` | `/profile` | Altera `display_name`, `bio`, `username` ou `email` |
| `POST` | `/profile/password` | Troca a senha: `{current_password, new_password}` |
| `PUT` · `DELETE` | `/profile/avatar` | Envia (o corpo é a imagem) ou remove a foto |
| `PUT` | `/profile/top5` | Salva o Top 5: `{artworks: [...]}`, na ordem |
| `GET` | `/avatars/{id}` | Mostra a foto de um usuário |

Com o servidor rodando, a documentação interativa fica em **http://127.0.0.1:8000/docs**.

<br>

## `08` &nbsp; Adicionar um museu

1. No `main.py`, escreva uma função `search_xxx(q, limit)` que devolve uma lista de obras montadas com `make_artwork(...)`.
2. Registre a função no dicionário `MUSEUMS` e acrescente a chave no `Literal` da rota `/search`.
3. No `index.html`, adicione uma `<option>` no seletor de museus e o nome em `MUSEUM_NAMES`.

Para não ter problemas com direitos de imagem, use só as imagens que o museu libera (domínio público ou CC0). Quando os termos de uso pedirem, preencha `credit` e `url`.

<br>

## `09` &nbsp; Próximos passos

- [ ] Coleção salva na conta (hoje fica no navegador)
- [ ] Perfil público em `/u/usuario`
- [ ] Seguir pessoas e ver o que elas registraram
- [ ] Listas temáticas e avaliações

<br>

---

<sub>As imagens das obras são dos museus de origem, cada uma sob a licença indicada pelo museu. O artlog só exibe e dá o crédito, quando o museu pede.</sub>