<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/banner-dark.png">
  <img src="docs/banner-light.png" alt="artlog: um diário de arte e museus. Busque em 10 acervos, monte curadorias, siga outras pessoas.">
</picture>

<br>

**artlog** é um Letterboxd para arte. Você busca obras nos acervos abertos de dez museus ao mesmo tempo, monta curadorias com as que viu (anotando quando e o que achou), segue pessoas com um olhar parecido com o seu e avalia as curadorias delas.

<br>

## `01` &nbsp; O que dá para fazer

| Recurso | Como funciona |
|---|---|
| **Explorar** | Uma busca só, em dez museus ao mesmo tempo. Os resultados vêm intercalados, para nenhum acervo dominar o começo da lista. |
| **Surpreenda-me** | Escolhe um tema (*ukiyo-e*, *still life*, *seascape*…) e busca por você. |
| **Curadorias** | Listas de obras com título e descrição, públicas ou privadas. O ♡ de qualquer obra abre a escolha das curadorias onde ela entra. Dentro de uma curadoria, você anota a data em que viu cada obra e o que achou, muda a ordem e exporta tudo em CSV. |
| **Avaliações** | De 1 a 5 estrelas nas curadorias públicas de outras pessoas. Cada curadoria mostra a média e quantas avaliações recebeu. |
| **Seguir** | Cada pessoa tem um perfil público em `/u/usuario`, com as curadorias públicas, o Top 5, os seguidores e quem ela segue. |
| **Pessoas** | Busca pelo nome ou pelo @, sem diferenciar acentos ("joao" acha "João"), e sugestões de quem seguir. Dá para seguir direto da lista. |
| **Seguindo** | O feed com as curadorias novas e atualizadas de quem você segue, mais as curadorias recentes da comunidade, para descobrir gente nova. |
| **Conta e perfil** | Cadastro, login com e-mail ou @usuário, foto, nome, bio e troca de senha. |
| **Top 5** | As cinco obras que dizem quem você é, na ordem que você escolher. Aparecem no seu perfil. |
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
| Musée du Louvre | Busca do site ou, quando ela está bloqueada, Wikidata + JSON de cada obra | Exibe crédito da foto e link para a ficha da obra, como pedem os termos de uso. Veja a nota abaixo |
| Smithsonian (museus de arte) | Open Access API | **Precisa de chave** (veja a seção `04`). Só imagens CC0 |
| Whitney Museum of American Art | API pública | Uso não comercial, com crédito e link |
| J. Paul Getty Museum | SPARQL + Linked Art | Só imagens CC0 (Open Content) |
| Solomon R. Guggenheim Museum | API do site (WordPress) | Com crédito e link |

Quando um museu falha, os outros continuam funcionando, e a página avisa qual não respondeu e por quê.

> **Sobre o Louvre:** o Louvre não tem API de busca, e desde 2026 a página de busca do site mostra uma verificação anti-robô ("I am not a robot") para programas como o artlog. Quando isso acontece, o artlog acha as obras pelo [Wikidata](https://www.wikidata.org), que guarda o identificador do Louvre (o ARK, propriedade [P9394](https://www.wikidata.org/wiki/Property:P9394)) de cada obra catalogada. Os detalhes e a foto continuam vindo do JSON oficial do Louvre; se ele falhar, a obra é montada com os dados do Wikidata e a imagem do Wikimedia Commons. O Wikidata cobre as obras mais conhecidas, não o acervo inteiro: para "monet", o site do Louvre mostra 216 resultados, e o Wikidata, 3. Depois de um bloqueio, o artlog espera 30 minutos antes de tentar o site de novo.

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

# Onde fica o banco SQLite (padrão: artlog.db na pasta do projeto)
ARTLOG_DB=/caminho/para/artlog.db

# PostgreSQL. Se existir, o artlog usa este banco no lugar do SQLite.
# No seu computador, deixe sem: o SQLite é mais simples para desenvolver.
DATABASE_URL=postgresql://usuario:senha@servidor/banco?sslmode=require
```

O `.env` e o `artlog.db` estão no `.gitignore` e nunca devem ir para o GitHub, porque têm a sua chave e as contas dos usuários.

**Qual banco é usado:** sem `DATABASE_URL`, o artlog usa o **SQLite** (um arquivo, nada para instalar). Com `DATABASE_URL`, usa o **PostgreSQL**. O código é o mesmo para os dois, e o `db.py` faz a ponte. Ao iniciar, o servidor mostra no log qual banco está em uso, sem mostrar a senha:

```
INFO [artlog] Banco de dados: PostgreSQL (postgresql://***@ep-....neon.tech/neondb)
```

As tabelas são criadas sozinhas na primeira vez, nos dois bancos.

<br>

## `05` &nbsp; Deploy (Render + Neon)

No plano gratuito do Render, os arquivos que o site cria **somem a cada deploy, reinício ou quando o serviço "dorme"** (15 minutos sem visitas). Por isso o `artlog.db` não serve em produção: as contas e curadorias seriam apagadas. A solução é guardar os dados num PostgreSQL gratuito e permanente, o [Neon](https://neon.com). O site continua no Render.

**1. Crie o banco no Neon**

1. Entre em [neon.com](https://neon.com) e crie uma conta (dá para entrar com o GitHub).
2. Crie um projeto chamado `artlog`. Em **Region**, escolha a região do seu serviço no Render, ou a mais próxima dela (no Render, ela aparece em **Settings › Region**). Banco e site perto um do outro deixam o artlog mais rápido.
3. No painel do projeto, clique em **Connect** e copie a *connection string*. Ela começa com `postgresql://` e termina com `sslmode=require`.

> A connection string contém a senha do banco. Não coloque no código nem no GitHub: ela vai só no painel do Render.

**2. Configure o serviço no Render**

1. Envie o código para o GitHub, com o `db.py` e o `requirements.txt` novo.
2. No Render, abra o serviço do artlog e vá em **Environment**. Adicione:

   | Variável | Valor |
   |---|---|
   | `DATABASE_URL` | a connection string do Neon |
   | `COOKIE_SECURE` | `1` |
   | `SI_API_KEY` | sua chave do Smithsonian |

3. Em **Settings**, confira os comandos:
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `uvicorn main:app --host 0.0.0.0 --port $PORT`
4. Salve. O Render faz um deploy novo.

**3. Confira**

1. Nos **Logs** do Render, procure a linha `Banco de dados: PostgreSQL (...neon.tech...)`. Se aparecer `SQLite`, a variável `DATABASE_URL` não foi salva.
2. Abra o site, crie uma conta e uma curadoria.
3. Em **Manual Deploy › Deploy latest commit**, faça um deploy novo. Quando terminar, entre de novo: a conta e a curadoria têm que continuar lá.

> **Primeiro acesso lento:** no plano gratuito, o Render dorme depois de 15 minutos sem visitas, e o Neon depois de 5. O primeiro acesso depois disso leva cerca de um minuto. Os seguintes são rápidos.

<br>

## `06` &nbsp; Contas e segurança

- **Senhas:** o banco nunca guarda a senha, só um hash **scrypt** com sal aleatório. O scrypt vem com o Python, sem biblioteca extra.
- **Sessão:** fica num cookie `HttpOnly` e `SameSite=Lax`, que o JavaScript da página não consegue ler. O banco guarda só o hash do token. Sair encerra a sessão na hora.
- **Login:** depois de 8 senhas erradas, a conta fica bloqueada por 15 minutos. A mensagem de erro é a mesma quando a conta não existe e quando a senha está errada, então ninguém consegue descobrir quais e-mails têm cadastro.
- **Troca de senha:** os outros aparelhos onde a conta estava aberta saem dela.
- **Foto de perfil:** o navegador corta a foto e reduz para 400×400 antes de enviar, então dados como a localização GPS da foto não vão junto. O servidor confere os bytes do arquivo (não confia no tipo informado pelo navegador) e aceita até 1 MB.

- **Privacidade:** o e-mail nunca aparece em perfis públicos. Uma curadoria privada responde "não encontrada" para outras pessoas, então nem o título vaza. Quando uma curadoria fica privada, as avaliações que ela recebeu são apagadas.

> **De onde veio a "Minha coleção":** antes das curadorias, o ♡ salvava as obras só no navegador. No primeiro login depois da mudança, essas obras (com notas e datas) viram uma curadoria **privada** chamada "Minha coleção". Uma cópia fica guardada no navegador, em `artlog-collection-backup`.

<br>

## `07` &nbsp; Estrutura

```
artlog/
├── main.py              servidor FastAPI, busca nos museus e entrega do site
├── db.py                banco de dados: SQLite no computador, PostgreSQL no Render
├── auth.py              cadastro, login, logout e sessão
├── user_profile.py      perfil, troca de senha, foto e Top 5
├── social.py            curadorias, avaliações, perfis públicos, seguir e feed
├── index.html           a estrutura da página e as janelas
├── app.js               todo o comportamento da página (rotas, busca, curadorias...)
├── style.css            visual: cores, tipografia e componentes
├── requirements.txt     dependências Python
├── docs/                imagens deste README
│
├── diagnose.py          testa as APIs dos museus direto da sua máquina
├── check_images.py      confere se as imagens de cada museu carregam
├── check_getty.py       diagnóstico da integração com o Getty
├── check_smithsonian.py diagnóstico da chave e dos filtros do Smithsonian
├── chech_louvre.py      diagnóstico da busca do Louvre
└── script.js            versão antiga do front-end (o site não usa mais)
```

<br>

## `08` &nbsp; API

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
| `GET` | `/curations/mine` | Minhas curadorias, com os IDs das obras de cada uma |
| `POST` | `/curations` | Cria: `{title, description, is_public, items?}` |
| `GET` · `PATCH` · `DELETE` | `/curations/{id}` | Abre, edita ou apaga (privada: só o dono vê) |
| `POST` · `PATCH` · `DELETE` | `/curations/{id}/items` | Adiciona obra, edita a nota/data ou tira (`?artwork_id=`) |
| `PUT` | `/curations/{id}/order` | Nova ordem: `{artwork_ids: [...]}` |
| `PUT` · `DELETE` | `/curations/{id}/rating` | Dá (`{stars: 1-5}`) ou tira a sua nota |
| `GET` | `/curations/discover` | Curadorias públicas recentes da comunidade |
| `GET` | `/users?q=termo` | Busca pessoas pelo nome ou @ (sem `q`: sugestões de quem seguir) |
| `GET` | `/users/{usuario}` | Perfil público: dados, contagens, Top 5 e curadorias |
| `POST` · `DELETE` | `/users/{usuario}/follow` | Seguir ou deixar de seguir |
| `GET` | `/users/{usuario}/followers` · `/following` | Quem segue a pessoa e quem ela segue |
| `GET` | `/feed` | Curadorias de quem você segue |

As páginas `/seguindo`, `/pessoas`, `/u/{usuario}` e `/c/{id}` têm endereço próprio, então dá para mandar o link de um perfil ou de uma curadoria para alguém.

Com o servidor rodando, a documentação interativa fica em **http://127.0.0.1:8000/docs**.

<br>

## `09` &nbsp; Adicionar um museu

1. No `main.py`, escreva uma função `search_xxx(q, limit)` que devolve uma lista de obras montadas com `make_artwork(...)`.
2. Registre a função no dicionário `MUSEUMS` e acrescente a chave no `Literal` da rota `/search`.
3. No `index.html`, adicione uma `<option>` no seletor de museus e o nome em `MUSEUM_NAMES`.

Para não ter problemas com direitos de imagem, use só as imagens que o museu libera (domínio público ou CC0). Quando os termos de uso pedirem, preencha `credit` e `url`.

<br>

## `10` &nbsp; Próximos passos

- [x] Curadorias públicas e privadas
- [x] Perfil público em `/u/usuario`, seguir pessoas e feed
- [x] Avaliações de 1 a 5 estrelas
- [x] Buscar pessoas pelo nome ou @
- [ ] Comentários nas curadorias
- [ ] Notificações (novos seguidores, avaliações recebidas)

<br>

---

<sub>As imagens das obras são dos museus de origem, cada uma sob a licença indicada pelo museu. O artlog só exibe e dá o crédito, quando o museu pede.</sub>
