/* artlog: toda a interação da página.
 *
 * Organização (procure pelos títulos):
 *   Básico ........ constantes, cor de destaque, avisos, chamadas à API
 *   Rotas ......... /  /seguindo  /pessoas  /u/usuario  /c/numero
 *   Cards ......... card de obra e card de curadoria
 *   Detalhes ...... janela da obra (nota e data quando aberta de uma curadoria)
 *   Salvar ........ o ♡: escolher em quais curadorias a obra entra
 *   Feed .......... Seguindo + curadorias da comunidade
 *   Pessoas ....... busca de pessoas e sugestões de quem seguir
 *   Perfil ........ meu perfil ou de outra pessoa, seguir, seguidores
 *   Top 5 ......... montar e ordenar
 *   Curadoria ..... página da curadoria, estrelas, ordem, exportar
 *   Busca ......... Explorar
 *   Conta ......... entrar, criar conta, sair, editar perfil, foto
 *
 * Tudo que vem do servidor ou dos museus entra na página com textContent,
 * nunca com innerHTML: um título com "<script>" aparece como texto.
 */
(() => {
    "use strict";

    // ======================================================================
    // Básico
    // ======================================================================
    const $ = (id) => document.getElementById(id);
    const REQUEST_TIMEOUT_MS = 30000;
    const TOP_SIZE = 5;

    const MUSEUM_NAMES = {
        met: "The Met", artic: "Art Institute of Chicago", cleveland: "Cleveland Museum of Art",
        smk: "SMK (Dinamarca)", vam: "Victoria and Albert Museum", louvre: "Musée du Louvre",
        smithsonian: "Smithsonian", whitney: "Whitney Museum", getty: "Getty Museum", guggenheim: "Guggenheim Museum",
    };

    // Imagem local: não depende de nenhum serviço externo, então nunca falha.
    const PLACEHOLDER = "data:image/svg+xml;utf8," + encodeURIComponent(
        '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="500" viewBox="0 0 400 500">' +
        '<rect width="400" height="500" fill="#f9f9f9"/>' +
        '<text x="200" y="256" font-family="Helvetica, Arial, sans-serif" font-size="20" ' +
        'text-anchor="middle" fill="#6e6b7b">Imagem indisponível</text></svg>'
    );

    // Estado da página
    let currentUser = null;      // quem está logado (ou null)
    let myCurations = [];        // minhas curadorias (resumo + IDs das obras)
    let myArtIds = new Set();    // todas as obras que estão em alguma curadoria minha (pinta o ♡)
    let top5 = [];               // meu Top 5
    let profileData = null;      // perfil aberto agora
    let curationData = null;     // curadoria aberta agora
    let view = "explore";

    // --- Cor de destaque ---------------------------------------------------
    const ACCENTS = {
        red:    { cssVar: "--artlog-red",    onAccent: "#ffffff" },
        yellow: { cssVar: "--artlog-yellow", onAccent: "#14121f" },
        blue:   { cssVar: "--artlog-blue",   onAccent: "#ffffff" },
    };
    const dots = document.querySelectorAll(".dot[data-accent]");
    function setAccent(name, save = true) {
        const accent = ACCENTS[name];
        if (!accent) return;
        document.documentElement.style.setProperty("--brand-accent", `var(${accent.cssVar})`);
        document.documentElement.style.setProperty("--on-accent", accent.onAccent);
        dots.forEach((dot) => dot.setAttribute("aria-pressed", String(dot.dataset.accent === name)));
        if (save) { try { localStorage.setItem("artlog-accent", name); } catch (_) { /* modo privado */ } }
    }
    dots.forEach((dot) => dot.addEventListener("click", () => setAccent(dot.dataset.accent)));
    let savedAccent = null;
    try { savedAccent = localStorage.getItem("artlog-accent"); } catch (_) { /* ignora */ }
    setAccent(ACCENTS[savedAccent] ? savedAccent : "red", false);

    // --- Pequenos ajudantes ------------------------------------------------
    function el(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined && text !== null) node.textContent = text;
        return node;
    }
    function plural(n, one, many) { return `${n} ${n === 1 ? one : many}`; }
    function displayName(user) { return user.display_name || `@${user.username}`; }
    function formatDate(seconds) {
        return new Date(seconds * 1000).toLocaleDateString("pt-BR", { day: "numeric", month: "short", year: "numeric" });
    }
    function formatSeen(iso) {
        const [y, m, d] = iso.split("-").map(Number);
        return new Date(y, m - 1, d).toLocaleDateString("pt-BR", { day: "numeric", month: "long", year: "numeric" });
    }
    function formatAvg(avg) { return avg.toLocaleString("pt-BR", { minimumFractionDigits: 1, maximumFractionDigits: 1 }); }

    function artImage(art, className) {
        const img = el("img", className);
        img.alt = "";
        img.loading = "lazy";
        img.referrerPolicy = "no-referrer";
        img.addEventListener("error", () => { img.src = PLACEHOLDER; }, { once: true });
        img.src = art.image || PLACEHOLDER;
        return img;
    }

    // Foto redonda; sem foto, mostra a inicial do nome.
    function paintAvatar(node, user) {
        node.replaceChildren();
        if (user.avatar_url) {
            const img = el("img");
            img.src = user.avatar_url;
            img.alt = "";
            node.append(img);
        } else {
            node.textContent = (user.display_name || user.username || "?").trim().charAt(0).toUpperCase();
        }
    }

    // Só os campos de uma obra que o servidor aceita.
    function cleanArt(art) {
        const s = (v, max) => (typeof v === "string" ? v.slice(0, max) : "");
        return {
            id: art.id, title: s(art.title, 500), artist: s(art.artist, 500), year: s(art.year, 200),
            museum: s(art.museum, 200), image: s(art.image, 1000),
            url: s(art.url, 1000) || null, credit: s(art.credit, 500) || null,
        };
    }

    // --- Aviso rápido no rodapé ---------------------------------------------
    const toastBox = $("toast");
    let toastTimer = null;
    function toast(text) {
        toastBox.textContent = text;
        toastBox.hidden = false;
        clearTimeout(toastTimer);
        toastTimer = setTimeout(() => { toastBox.hidden = true; }, 4500);
    }

    // --- Chamadas à API (JSON, mensagens de erro em português) -------------
    async function readError(res) {
        try {
            const body = await res.json();
            if (typeof body.detail === "string") return body.detail;
        } catch (_) { /* corpo sem JSON */ }
        return `erro ${res.status}`;
    }

    async function api(method, url, body, headers) {
        const options = { method, headers: { ...(headers || {}) } };
        if (body !== undefined) {
            if (body instanceof Blob) options.body = body;
            else {
                options.body = JSON.stringify(body);
                options.headers["Content-Type"] = "application/json";
            }
        }
        let res;
        try { res = await fetch(url, options); }
        catch (_) { throw new Error("Sem conexão com o servidor. Tente de novo."); }
        if (res.status === 401 && currentUser) {
            signedOut();
            throw new Error("Sua sessão terminou. Entre de novo.");
        }
        if (!res.ok) {
            const message = await readError(res);
            const error = new Error(message.startsWith("erro ") ? "Não foi possível concluir. Tente de novo." : message);
            error.status = res.status;
            throw error;
        }
        return res.status === 204 ? null : res.json();
    }

    // --- Confirmação (no lugar do confirm() do navegador) ------------------
    const confirmDialog = $("confirm-dialog");
    function confirmAction({ title, text, ok }) {
        $("confirm-title").textContent = title;
        $("confirm-text").textContent = text;
        $("confirm-ok").textContent = ok;
        confirmDialog.showModal();
        $("confirm-cancel").focus();
        return new Promise((resolve) => {
            const finish = (answer) => {
                confirmDialog.close();
                $("confirm-ok").onclick = $("confirm-cancel").onclick = confirmDialog.oncancel = null;
                resolve(answer);
            };
            $("confirm-ok").onclick = () => finish(true);
            $("confirm-cancel").onclick = () => finish(false);
            confirmDialog.oncancel = (event) => { event.preventDefault(); finish(false); };
        });
    }

    // Fechar janelas clicando fora ou no ×.
    function wireDialog(dialog, closeButton) {
        if (closeButton) closeButton.addEventListener("click", () => dialog.close());
        dialog.addEventListener("click", (event) => { if (event.target === dialog) dialog.close(); });
    }

    // ======================================================================
    // Rotas: cada tela tem um endereço que dá para compartilhar
    // ======================================================================
    const VIEWS = ["explore", "feed", "people", "profile", "curation", "missing"];
    const tabs = { explore: $("tab-explore"), feed: $("tab-feed"), people: $("tab-people"), profile: $("tab-profile") };
    let routeSeq = 0;

    function showView(name) {
        view = name;
        VIEWS.forEach((v) => { $(`view-${v}`).hidden = v !== name; });
        const myProfile = name === "profile" && profileData && profileData.is_me;
        Object.entries(tabs).forEach(([key, tab]) => {
            const active = key === name && (key !== "profile" || myProfile);
            if (active) tab.setAttribute("aria-current", "page"); else tab.removeAttribute("aria-current");
        });
    }

    function showMissing(title, text) {
        $("missing-title").textContent = title || "Não encontramos esta página.";
        $("missing-text").textContent = text || "Ela pode ter sido apagada ou estar privada.";
        document.title = "artlog";
        showView("missing");
    }

    async function route() {
        const seq = ++routeSeq;
        const path = location.pathname;
        let match;
        if (path === "/" || path === "") {
            document.title = "artlog";
            showView("explore");
            syncFavButtons();
        } else if (path === "/pessoas") {
            document.title = "Pessoas · artlog";
            showView("people");
            const q = new URLSearchParams(location.search).get("q") || "";
            peopleInput.value = q;
            searchPeople(q);  // sempre de novo: o estado de "Seguindo" muda ao entrar ou sair da conta
        } else if (path === "/seguindo") {
            if (!currentUser) {
                showView("explore");
                openAuth("login", "Entre para ver as curadorias de quem você segue.");
                return;
            }
            document.title = "Seguindo · artlog";
            showView("feed");
            await loadFeed(seq);
        } else if ((match = path.match(/^\/u\/([A-Za-z0-9_]{1,40})\/?$/))) {
            await loadProfile(match[1], seq);
        } else if ((match = path.match(/^\/c\/(\d{1,12})\/?$/))) {
            await loadCuration(Number(match[1]), seq);
        } else {
            showMissing();
        }
    }

    function navigate(path) {
        if (path !== location.pathname) history.pushState({}, "", path);
        route();
        window.scrollTo(0, 0);
    }

    // Links internos (data-link) trocam de tela sem recarregar a página.
    document.addEventListener("click", (event) => {
        const link = event.target.closest("a[data-link]");
        if (!link || event.defaultPrevented || event.button !== 0) return;
        if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;  // nova aba: deixa o navegador
        const url = new URL(link.href);
        if (url.origin !== location.origin) return;
        event.preventDefault();
        const dialog = link.closest("dialog[open]");
        if (dialog) dialog.close();
        navigate(url.pathname);
    });
    window.addEventListener("popstate", route);

    // ======================================================================
    // Cards
    // ======================================================================
    function paintFav(button, saved) {
        button.setAttribute("aria-pressed", String(saved));
        button.textContent = saved ? "♥" : "♡";
        button.setAttribute("aria-label", saved ? "Salva em curadoria. Gerenciar" : "Salvar em curadoria");
    }
    function syncFavButtons() {
        document.querySelectorAll(".fav").forEach((b) => paintFav(b, myArtIds.has(b.dataset.id)));
    }

    function iconButton(className, label, text, onClick, disabled = false) {
        const button = el("button", className, text);
        button.type = "button";
        button.setAttribute("aria-label", label);
        button.title = label;
        button.disabled = disabled;
        button.addEventListener("click", onClick);
        return button;
    }

    // Card de obra. ctx (opcional): { curation, item, index } quando a obra está numa curadoria.
    function createCard(art, ctx) {
        const card = el("article", "card");
        const open = () => openDetail(art, ctx);

        const wrapper = el("div", "card-image-wrapper");
        const img = artImage(art);
        img.alt = art.title || "Obra sem título";
        wrapper.append(img);

        const fav = el("button", "fav");
        fav.type = "button";
        fav.dataset.id = art.id;
        paintFav(fav, myArtIds.has(art.id));
        fav.addEventListener("click", (event) => { event.stopPropagation(); openSave(art); });
        wrapper.append(fav);
        wrapper.addEventListener("click", open);

        const title = el("h3", "card-title");
        const titleButton = el("button", "card-open", art.title || "Sem título");
        titleButton.type = "button";
        titleButton.addEventListener("click", open);
        title.append(titleButton);

        const meta = el("div", "card-meta");
        meta.append(el("span", "", art.year || ""), el("span", "", art.museum || ""));

        card.append(wrapper, title, el("p", "card-artist", art.artist || "Artista desconhecido"), meta);

        // Alguns museus (Louvre, Whitney...) pedem crédito da foto e link para a ficha da obra.
        if (art.credit) card.append(el("p", "card-credit", art.credit));
        if (typeof art.url === "string" && art.url.startsWith("https://")) {
            const link = el("a", "card-link", "Ver no site do museu");
            link.href = art.url;
            link.target = "_blank";
            link.rel = "noopener noreferrer";
            card.append(link);
        }

        if (ctx && ctx.item) {
            if (ctx.item.seen) card.append(el("p", "card-seen", `Visto em ${formatSeen(ctx.item.seen)}`));
            if (ctx.item.note) {
                const note = ctx.item.note;
                card.append(el("p", "card-note", note.length > 160 ? note.slice(0, 160) + "…" : note));
            }
            if (ctx.curation.is_owner) {
                const total = ctx.curation.items.length;
                const controls = el("div", "top-controls item-controls");
                controls.append(
                    iconButton("top-left", "Mover para antes", "←", () => moveItem(ctx.index, -1), ctx.index === 0),
                    iconButton("top-right", "Mover para depois", "→", () => moveItem(ctx.index, 1), ctx.index === total - 1),
                    iconButton("top-remove", "Tirar da curadoria", "×", () => removeItem(art)),
                );
                card.append(controls);
            }
        }
        return card;
    }

    // Card de curadoria: capa em mosaico com até 4 obras.
    function curationCard(curation, { showOwner = false } = {}) {
        const card = el("article", "cur-card");
        const link = el("a", "cur-link");
        link.href = `/c/${curation.id}`;
        link.dataset.link = "";

        const cover = el("div", `cur-cover cover-${Math.min(curation.covers.length, 4)}`);
        if (curation.covers.length === 0) cover.append(el("span", "cur-cover-empty", "Sem obras ainda"));
        curation.covers.slice(0, 4).forEach((image) => cover.append(artImage({ image })));
        link.append(cover, el("h3", "cur-title", curation.title));
        card.append(link);

        const meta = el("p", "cur-meta");
        meta.append(el("span", "", plural(curation.item_count, "obra", "obras")));
        if (curation.rating && curation.rating.count) {
            const stars = el("span", "cur-stars", `★ ${formatAvg(curation.rating.avg)}`);
            stars.setAttribute("aria-label", `nota média ${formatAvg(curation.rating.avg)} de 5, ${plural(curation.rating.count, "avaliação", "avaliações")}`);
            meta.append(stars);
        }
        if (!curation.is_public) meta.append(el("span", "badge", "Privada"));
        card.append(meta);

        if (showOwner) {
            const owner = el("a", "cur-owner small");
            owner.href = `/u/${curation.owner.username}`;
            owner.dataset.link = "";
            const avatar = el("span", "avatar avatar-xs");
            avatar.setAttribute("aria-hidden", "true");
            paintAvatar(avatar, curation.owner);
            owner.append(avatar, el("span", "", displayName(curation.owner)));
            card.append(owner);
        }
        return card;
    }

    // ======================================================================
    // Detalhes da obra
    // ======================================================================
    const detail = $("detail");
    const detailSave = $("detail-save");
    const detailTop = $("detail-top");
    const detailNote = $("detail-note");
    const detailSeen = $("detail-seen");
    const detailSaved = $("detail-saved");
    let openArt = null;
    let openCtx = null;

    function refreshDetail() {
        if (!openArt) return;
        const count = myCurations.filter((c) => c.artwork_ids.includes(openArt.id)).length;
        detailSave.textContent = count ? `♥ Em ${plural(count, "curadoria", "curadorias")}` : "♡ Salvar em curadoria";
        refreshDetailTop();
    }

    function openDetail(art, ctx) {
        openArt = art;
        openCtx = ctx || null;
        const img = $("detail-img");
        img.referrerPolicy = "no-referrer";
        img.alt = art.title || "Obra sem título";
        img.src = art.image || PLACEHOLDER;
        $("detail-title").textContent = art.title || "Sem título";
        $("detail-artist").textContent = art.artist || "";
        $("detail-meta").textContent = [art.year, art.museum].filter(Boolean).join(" · ");
        $("detail-credit").textContent = art.credit || "";
        const link = $("detail-link");
        const ok = typeof art.url === "string" && art.url.startsWith("https://");
        link.hidden = !ok;
        if (ok) link.href = art.url;

        // Nota e data da obra nesta curadoria.
        const box = $("detail-item");
        if (openCtx && openCtx.item) {
            const { item, curation } = openCtx;
            const mine = curation.is_owner;
            box.hidden = !mine && !item.note && !item.seen;
            $("detail-item-edit").hidden = !mine;
            $("detail-item-read").hidden = mine;
            $("detail-item-context").textContent = mine
                ? `Na curadoria “${curation.title}”`
                : `Nota de ${displayName(curation.owner)} em “${curation.title}”`;
            if (mine) {
                detailNote.value = item.note || "";
                detailSeen.value = item.seen || "";
                detailSaved.textContent = "";
            } else {
                $("detail-read-seen").textContent = item.seen ? `Visto em ${formatSeen(item.seen)}` : "";
                $("detail-read-note").textContent = item.note || "";
                $("detail-read-note").hidden = !item.note;
            }
        } else {
            box.hidden = true;
        }
        refreshDetail();
        detail.showModal();
    }

    let noteTimer = null;
    async function saveItemField(fields) {
        if (!openCtx || !openCtx.curation.is_owner) return;
        const { curation, item } = openCtx;
        Object.assign(item, fields);
        detailSaved.textContent = "Salvando…";
        try {
            await api("PATCH", `/curations/${curation.id}/items`, { artwork_id: item.artwork.id, ...fields });
            detailSaved.textContent = "Salvo.";
        } catch (err) {
            detailSaved.textContent = err.message;
        }
    }
    detailNote.addEventListener("input", () => {
        clearTimeout(noteTimer);
        detailSaved.textContent = "";
        noteTimer = setTimeout(() => saveItemField({ note: detailNote.value }), 700);
    });
    detailSeen.addEventListener("change", () => saveItemField({ seen: detailSeen.value }));
    detailSave.addEventListener("click", () => openSave(openArt));
    wireDialog(detail, $("detail-close"));
    detail.addEventListener("close", () => {
        if (noteTimer) { clearTimeout(noteTimer); saveItemField({ note: detailNote.value }); noteTimer = null; }
        const wasCurationItem = openCtx && openCtx.item;
        openArt = null;
        openCtx = null;
        if (wasCurationItem && view === "curation") renderCurationItems();  // mostra a nota atualizada
    });

    // ======================================================================
    // Salvar (o ♡): em quais curadorias a obra está
    // ======================================================================
    const saveDialog = $("save-dialog");
    const saveList = $("save-list");
    const saveError = $("save-error");
    let saveArt = null;

    function rebuildArtIds() {
        myArtIds = new Set(myCurations.flatMap((c) => c.artwork_ids));
        syncFavButtons();
        refreshDetail();
    }

    async function loadMine() {
        if (!currentUser) { myCurations = []; rebuildArtIds(); return; }
        try {
            myCurations = (await api("GET", "/curations/mine")).curations;
        } catch (_) { myCurations = []; }
        rebuildArtIds();
    }

    function renderSaveList() {
        $("save-empty").hidden = myCurations.length > 0;
        saveList.replaceChildren(...myCurations.map((curation) => {
            const li = el("li");
            const label = el("label", "save-item");
            const box = el("input");
            box.type = "checkbox";
            box.checked = curation.artwork_ids.includes(saveArt.id);
            box.addEventListener("change", () => toggleInCuration(curation, box));
            const text = el("span", "save-item-text");
            text.append(el("strong", "", curation.title));
            const meta = [plural(curation.item_count, "obra", "obras")];
            if (!curation.is_public) meta.push("privada");
            text.append(el("span", "", meta.join(" · ")));
            label.append(box, text);
            li.append(label);
            return li;
        }));
    }

    function openSave(art) {
        if (!currentUser) {
            openAuth("register", "Crie uma conta (ou entre) para guardar obras nas suas curadorias.");
            return;
        }
        saveArt = art;
        saveError.textContent = "";
        $("save-art-img").src = art.image || PLACEHOLDER;
        $("save-art-img").referrerPolicy = "no-referrer";
        $("save-art-title").textContent = art.title || "Sem título";
        $("save-art-artist").textContent = art.artist || "";
        $("save-new").reset();
        renderSaveList();
        saveDialog.showModal();
    }

    async function toggleInCuration(curation, box) {
        const art = saveArt;
        const adding = box.checked;
        box.disabled = true;
        saveError.textContent = "";
        try {
            if (adding) {
                await api("POST", `/curations/${curation.id}/items`, { artwork: cleanArt(art) });
                curation.artwork_ids.push(art.id);
                curation.item_count++;
            } else {
                await api("DELETE", `/curations/${curation.id}/items?artwork_id=${encodeURIComponent(art.id)}`);
                curation.artwork_ids = curation.artwork_ids.filter((id) => id !== art.id);
                curation.item_count--;
            }
            rebuildArtIds();
            renderSaveList();
            toast(adding ? `Salva em “${curation.title}”.` : `Tirada de “${curation.title}”.`);
            if (view === "curation" && curationData && curationData.id === curation.id) reloadCuration();
        } catch (err) {
            box.checked = !adding;
            saveError.textContent = err.message;
        } finally {
            box.disabled = false;
        }
    }

    $("save-new").addEventListener("submit", async (event) => {
        event.preventDefault();
        const form = event.currentTarget;
        const submit = form.querySelector('button[type="submit"]');
        submit.disabled = true;
        saveError.textContent = "";
        try {
            const data = await api("POST", "/curations", {
                title: $("save-new-title").value,
                is_public: !$("save-new-private").checked,
                items: [{ artwork: cleanArt(saveArt) }],
            });
            await loadMine();
            form.reset();
            renderSaveList();
            toast(`Curadoria “${data.curation.title}” criada.`);
        } catch (err) {
            saveError.textContent = err.message;
        } finally {
            submit.disabled = false;
        }
    });
    wireDialog(saveDialog, $("save-close"));

    // ======================================================================
    // Feed: Seguindo + comunidade
    // ======================================================================
    async function loadFeed(seq) {
        $("feed-list").replaceChildren();
        $("discover-list").replaceChildren();
        $("feed-empty").hidden = $("discover-empty").hidden = true;
        let feed, discover;
        try {
            [feed, discover] = await Promise.all([api("GET", "/feed"), api("GET", "/curations/discover")]);
        } catch (err) {
            if (seq !== routeSeq) return;
            $("feed-empty-text").textContent = err.message;
            $("feed-find-people").hidden = true;
            $("feed-empty").hidden = false;
            return;
        }
        if (seq !== routeSeq) return;
        $("feed-list").replaceChildren(...feed.curations.map((c) => curationCard(c, { showOwner: true })));
        $("feed-empty").hidden = feed.curations.length > 0;
        $("feed-empty-text").textContent = feed.following === 0
            ? "Você ainda não segue ninguém. Procure pessoas pelo nome, ou veja as curadorias da comunidade abaixo."
            : "Quem você segue ainda não publicou curadorias com obras.";
        $("feed-find-people").hidden = feed.following !== 0;
        $("discover-list").replaceChildren(...discover.curations.map((c) => curationCard(c, { showOwner: true })));
        $("discover-empty").hidden = discover.curations.length > 0;
    }

    // ======================================================================
    // Pessoas: busca pelo nome ou @ e sugestões de quem seguir
    // ======================================================================
    const peopleInput = $("people-input");
    const peopleList = $("people-results");
    const peopleStatus = $("people-status");
    const peopleHeading = $("people-heading");
    let peopleSeq = 0;
    let peopleTimer = null;

    // Botão Seguir que funciona em qualquer lista (busca, sugestões...).
    function followButton(person, onChange) {
        const button = el("button", "btn-follow small");
        button.type = "button";
        const paint = () => {
            button.textContent = person.is_following ? "Seguindo" : "Seguir";
            button.setAttribute("aria-pressed", String(person.is_following));
            button.setAttribute("aria-label", `${person.is_following ? "Deixar de seguir" : "Seguir"} ${displayName(person)}`);
            button.classList.toggle("is-following", person.is_following);
        };
        paint();
        button.addEventListener("click", async () => {
            if (!currentUser) {
                openAuth("register", `Crie uma conta (ou entre) para seguir ${displayName(person)}.`);
                return;
            }
            const was = person.is_following;
            button.disabled = true;
            try {
                const counts = await api(was ? "DELETE" : "POST", `/users/${person.username}/follow`);
                person.is_following = counts.is_following;
                person.followers = counts.followers;
                paint();
                if (onChange) onChange(person);
                toast(was ? `Você deixou de seguir ${displayName(person)}.` : `Agora você segue ${displayName(person)}.`);
            } catch (err) {
                toast(err.message);
            } finally {
                button.disabled = false;
            }
        });
        return button;
    }

    function personRow(person) {
        const li = el("li", "person-row");
        const link = el("a", "person person-main");
        link.href = `/u/${person.username}`;
        link.dataset.link = "";
        const avatar = el("span", "avatar avatar-md");
        avatar.setAttribute("aria-hidden", "true");
        paintAvatar(avatar, person);
        const text = el("span", "person-text");
        text.append(el("strong", "", displayName(person)), el("span", "", `@${person.username}`));
        const meta = el("span", "person-meta");
        const updateMeta = () => {
            meta.textContent = `${plural(person.public_curations, "curadoria", "curadorias")} · ${plural(person.followers, "seguidor", "seguidores")}`;
        };
        updateMeta();
        text.append(meta);
        if (person.bio) {
            const bio = person.bio.replace(/\s+/g, " ");
            text.append(el("span", "person-bio", bio.length > 140 ? bio.slice(0, 140) + "…" : bio));
        }
        link.append(avatar, text);
        li.append(link);
        if (person.is_me) li.append(el("span", "badge", "Você"));
        else li.append(followButton(person, updateMeta));
        return li;
    }

    async function searchPeople(query) {
        const seq = ++peopleSeq;
        const q = query.trim();
        peopleStatus.textContent = q.replace(/^@/, "").length >= 2 ? "Buscando…" : "";
        let data;
        try {
            data = await api("GET", `/users?${new URLSearchParams({ q })}`);
        } catch (err) {
            if (seq !== peopleSeq) return;
            peopleStatus.textContent = err.message;
            return;
        }
        if (seq !== peopleSeq) return;  // chegou uma busca mais nova
        peopleList.replaceChildren(...data.users.map(personRow));
        if (data.mode === "suggestions") {
            peopleHeading.textContent = "Sugestões para seguir";
            peopleHeading.hidden = data.users.length === 0;
            peopleStatus.textContent = data.users.length
                ? "Pessoas com curadorias públicas, das mais seguidas para as menos."
                : "Ainda não há sugestões. Quando mais pessoas publicarem curadorias, elas aparecem aqui.";
        } else {
            peopleHeading.hidden = true;
            peopleStatus.textContent = data.users.length
                ? `${plural(data.users.length, "pessoa", "pessoas")} para “${q}”${data.users.length === 30 ? " (mostrando as 30 primeiras)" : ""}:`
                : `Ninguém encontrado para “${q}”. Confira a grafia ou tente só o primeiro nome.`;
        }
    }

    // Busca enquanto digita (espera uma pausa de 300 ms) e guarda o termo no endereço.
    peopleInput.addEventListener("input", () => {
        clearTimeout(peopleTimer);
        peopleTimer = setTimeout(() => {
            const q = peopleInput.value.trim();
            history.replaceState({}, "", q ? `/pessoas?${new URLSearchParams({ q })}` : "/pessoas");
            searchPeople(q);
        }, 300);
    });
    $("people-form").addEventListener("submit", (event) => {
        event.preventDefault();
        clearTimeout(peopleTimer);
        const q = peopleInput.value.trim();
        history.replaceState({}, "", q ? `/pessoas?${new URLSearchParams({ q })}` : "/pessoas");
        searchPeople(q);
    });

    // ======================================================================
    // Perfil (o meu ou de outra pessoa)
    // ======================================================================
    async function loadProfile(username, seq) {
        let data;
        try {
            data = await api("GET", `/users/${encodeURIComponent(username)}`);
        } catch (err) {
            if (seq !== routeSeq) return;
            if (err.status === 404) showMissing("Não encontramos essa pessoa.", "O @ pode ter mudado ou a conta não existe.");
            else showMissing("Não foi possível abrir o perfil.", err.message);
            return;
        }
        if (seq !== routeSeq) return;
        profileData = data;
        if (data.is_me) top5 = data.top5;
        renderProfile();
        showView("profile");
    }

    function renderProfile() {
        const data = profileData;
        const user = data.user;
        document.title = `${displayName(user)} · artlog`;
        paintAvatar($("profile-avatar"), user);
        $("profile-name").textContent = displayName(user);
        $("profile-handle").textContent = `@${user.username}`;
        $("profile-handle").hidden = !user.display_name;
        $("profile-bio").textContent = user.bio || "";
        $("profile-bio").hidden = !user.bio;
        renderFollowStats();

        $("btn-edit-profile").hidden = !data.is_me;
        $("btn-follow").hidden = data.is_me;
        paintFollowButton();

        // Top 5: editável no meu perfil; nos outros, só aparece se tiver obras.
        $("top5-section").hidden = !data.is_me && data.top5.length === 0;
        $("top5-hint").textContent = data.is_me
            ? "As obras que dizem quem você é. Use as setas para mudar a ordem."
            : `As obras favoritas de ${displayName(user)}.`;
        renderTop5();

        // Curadorias
        $("btn-new-curation").hidden = !data.is_me;
        $("profile-curations").replaceChildren(...data.curations.map((c) => curationCard(c)));
        const empty = $("profile-curations-empty");
        empty.hidden = data.curations.length > 0;
        empty.textContent = data.is_me
            ? "Você ainda não tem curadorias. Crie uma aqui, ou toque no ♡ de qualquer obra em Explorar."
            : `${displayName(user)} ainda não publicou curadorias.`;
    }

    function renderFollowStats() {
        const data = profileData;
        const publicCount = data.curations.length;
        $("stat-curations").textContent = plural(publicCount, "curadoria", "curadorias");
        $("stat-followers").textContent = plural(data.followers, "seguidor", "seguidores");
        $("stat-following").textContent = `${data.following} seguindo`;
    }

    function paintFollowButton() {
        const button = $("btn-follow");
        const following = profileData.is_following;
        button.textContent = following ? "Seguindo" : "Seguir";
        button.setAttribute("aria-pressed", String(following));
        button.classList.toggle("is-following", following);
    }

    $("btn-follow").addEventListener("click", async () => {
        if (!currentUser) {
            openAuth("register", `Crie uma conta (ou entre) para seguir ${displayName(profileData.user)}.`);
            return;
        }
        const data = profileData;
        const wasFollowing = data.is_following;
        const button = $("btn-follow");
        button.disabled = true;
        try {
            const counts = await api(wasFollowing ? "DELETE" : "POST", `/users/${data.user.username}/follow`);
            Object.assign(data, counts);
            paintFollowButton();
            renderFollowStats();
            toast(wasFollowing ? `Você deixou de seguir ${displayName(data.user)}.` : `Agora você segue ${displayName(data.user)}.`);
        } catch (err) {
            toast(err.message);
        } finally {
            button.disabled = false;
        }
    });

    // Lista de seguidores / seguindo
    const peopleDialog = $("people-dialog");
    async function openPeople(kind) {
        const user = profileData.user;
        $("people-title").textContent = kind === "followers"
            ? `Quem segue ${displayName(user)}`
            : `Quem ${displayName(user)} segue`;
        $("people-list").replaceChildren();
        $("people-empty").hidden = true;
        peopleDialog.showModal();
        try {
            const { users } = await api("GET", `/users/${user.username}/${kind}`);
            $("people-list").replaceChildren(...users.map((person) => {
                const li = el("li");
                const link = el("a", "person");
                link.href = `/u/${person.username}`;
                link.dataset.link = "";
                const avatar = el("span", "avatar avatar-md");
                avatar.setAttribute("aria-hidden", "true");
                paintAvatar(avatar, person);
                const text = el("span", "person-text");
                text.append(el("strong", "", displayName(person)), el("span", "", `@${person.username}`));
                link.append(avatar, text);
                li.append(link);
                return li;
            }));
            $("people-empty").hidden = users.length > 0;
            $("people-empty").textContent = kind === "followers" ? "Ninguém segue ainda." : "Ainda não segue ninguém.";
        } catch (err) {
            $("people-empty").textContent = err.message;
            $("people-empty").hidden = false;
        }
    }
    $("stat-followers").addEventListener("click", () => openPeople("followers"));
    $("stat-following").addEventListener("click", () => openPeople("following"));
    wireDialog(peopleDialog, $("people-close"));

    // ======================================================================
    // Top 5
    // ======================================================================
    const top5List = $("top5-list");
    const picker = $("top-picker");
    const pickerList = $("top-picker-list");
    const pickerEmpty = $("top-picker-empty");

    function inTop(id) { return top5.some((art) => art.id === id); }

    async function saveTop5(next, message) {
        const previous = top5;
        top5 = next;
        if (profileData && profileData.is_me) profileData.top5 = next;
        if (view === "profile") renderTop5();
        refreshDetailTop();
        try {
            await api("PUT", "/profile/top5", { artworks: next });
            if (message) toast(message);
        } catch (err) {
            top5 = previous;
            if (profileData && profileData.is_me) profileData.top5 = previous;
            if (view === "profile") renderTop5();
            refreshDetailTop();
            toast(err.message);
        }
    }

    function addToTop(art) {
        if (inTop(art.id) || top5.length >= TOP_SIZE) return;
        const clean = cleanArt(art);
        saveTop5([...top5, clean], `“${clean.title || "Obra"}” entrou no seu Top 5.`);
    }
    function removeFromTop(id) { saveTop5(top5.filter((art) => art.id !== id), "Obra removida do Top 5."); }
    function moveInTop(index, delta) {
        const target = index + delta;
        if (target < 0 || target >= top5.length) return;
        const next = [...top5];
        [next[index], next[target]] = [next[target], next[index]];
        saveTop5(next);
        // Mantém o foco no mesmo botão de seta depois de redesenhar.
        requestAnimationFrame(() => {
            const slot = top5List.children[target];
            const button = slot && slot.querySelector(delta < 0 ? ".top-left" : ".top-right");
            (button && !button.disabled ? button : slot && slot.querySelector(".top-art"))?.focus();
        });
    }

    function renderTop5() {
        if (!profileData) return;
        const editable = profileData.is_me;
        const list = editable ? top5 : profileData.top5;
        const slots = [];
        const size = editable ? TOP_SIZE : list.length;
        for (let i = 0; i < size; i++) {
            const art = list[i];
            const li = el("li", art ? "top-slot" : "top-slot is-empty");
            const rank = el("span", "top-rank", String(i + 1));
            rank.setAttribute("aria-hidden", "true");

            if (!art) {
                const add = el("button", "top-add");
                add.type = "button";
                add.append(Object.assign(el("span", "", "+"), { ariaHidden: "true" }));
                add.setAttribute("aria-label", `Escolher a obra número ${i + 1}`);
                add.addEventListener("click", openPicker);
                li.append(rank, add);
                slots.push(li);
                continue;
            }

            const open = el("button", "top-art");
            open.type = "button";
            open.setAttribute("aria-label", `${i + 1}º: ${art.title || "Sem título"}, ${art.artist || ""}`);
            open.append(artImage(art));
            open.addEventListener("click", () => openDetail(art));
            li.append(rank, open, el("p", "top-title", art.title || "Sem título"), el("p", "top-artist", art.artist || ""));
            if (editable) {
                const controls = el("div", "top-controls");
                controls.append(
                    iconButton("top-left", "Subir uma posição", "←", () => moveInTop(i, -1), i === 0),
                    iconButton("top-right", "Descer uma posição", "→", () => moveInTop(i, 1), i === list.length - 1),
                    iconButton("top-remove", "Remover do Top 5", "×", () => removeFromTop(art.id)),
                );
                li.append(controls);
            }
            slots.push(li);
        }
        top5List.replaceChildren(...slots);
    }

    // O seletor mostra as obras de todas as minhas curadorias (sem repetir).
    async function openPicker() {
        pickerList.replaceChildren();
        pickerEmpty.hidden = false;
        pickerEmpty.textContent = "Carregando suas obras…";
        picker.showModal();
        let full = [];
        try {
            full = await Promise.all(myCurations.map((c) => api("GET", `/curations/${c.id}`)));
        } catch (err) {
            pickerEmpty.textContent = err.message;
            return;
        }
        const seen = new Set();
        const arts = [];
        full.forEach(({ curation }) => curation.items.forEach(({ artwork }) => {
            if (!seen.has(artwork.id) && !inTop(artwork.id)) { seen.add(artwork.id); arts.push(artwork); }
        }));
        pickerEmpty.hidden = arts.length > 0;
        pickerEmpty.textContent = seen.size || myArtIds.size
            ? "Todas as obras das suas curadorias já estão no Top 5."
            : "Suas curadorias ainda não têm obras. Toque no ♡ de uma obra em Explorar para salvar.";
        pickerList.replaceChildren(...arts.map((art) => {
            const li = el("li");
            const button = el("button", "picker-item");
            button.type = "button";
            const text = el("span");
            text.append(el("strong", "", art.title || "Sem título"), el("span", "", art.artist || ""));
            button.append(artImage(art), text);
            button.addEventListener("click", () => { picker.close(); addToTop(art); });
            li.append(button);
            return li;
        }));
    }
    wireDialog(picker, $("top-picker-close"));

    // Botão "Adicionar ao Top 5" na janela da obra.
    function refreshDetailTop() {
        if (!openArt || !currentUser) { detailTop.hidden = true; return; }
        detailTop.hidden = false;
        const already = inTop(openArt.id);
        const full = !already && top5.length >= TOP_SIZE;
        detailTop.textContent = already ? "Remover do Top 5" : full ? "Top 5 cheio" : "Adicionar ao Top 5";
        detailTop.disabled = full;
        detailTop.title = full ? "Remova uma obra no seu perfil para abrir espaço." : "";
    }
    detailTop.addEventListener("click", () => {
        if (!openArt) return;
        if (inTop(openArt.id)) removeFromTop(openArt.id); else addToTop(openArt);
    });

    // ======================================================================
    // Página da curadoria
    // ======================================================================
    async function loadCuration(id, seq) {
        let data;
        try {
            data = await api("GET", `/curations/${id}`);
        } catch (err) {
            if (seq !== undefined && seq !== routeSeq) return;
            if (err.status === 404) showMissing("Não encontramos esta curadoria.", "Ela pode ter sido apagada ou estar privada.");
            else showMissing("Não foi possível abrir a curadoria.", err.message);
            return;
        }
        if (seq !== undefined && seq !== routeSeq) return;
        curationData = data.curation;
        renderCuration();
        showView("curation");
    }
    function reloadCuration() { if (curationData) loadCuration(curationData.id); }

    function renderCuration() {
        const c = curationData;
        document.title = `${c.title} · artlog`;
        $("cur-title").textContent = c.title;
        $("cur-private").hidden = c.is_public;
        $("cur-owner").href = `/u/${c.owner.username}`;
        paintAvatar($("cur-owner-avatar"), c.owner);
        $("cur-owner-name").textContent = `por ${displayName(c.owner)}`;
        $("cur-description").textContent = c.description;
        $("cur-description").hidden = !c.description;
        $("cur-meta").textContent = `${plural(c.items.length, "obra", "obras")} · atualizada em ${formatDate(c.updated_at)}`;
        $("cur-owner-actions").hidden = !c.is_owner;
        renderRating();
        renderCurationItems();
    }

    function renderCurationItems() {
        const c = curationData;
        $("cur-items").replaceChildren(...c.items.map((item, index) =>
            createCard(item.artwork, { curation: c, item, index })));
        const empty = $("cur-empty");
        empty.hidden = c.items.length > 0;
        empty.textContent = c.is_owner
            ? "Esta curadoria ainda não tem obras. Em Explorar, toque no ♡ de uma obra e escolha esta curadoria."
            : "Esta curadoria ainda não tem obras.";
    }

    // --- Estrelas -----------------------------------------------------------
    function renderRating() {
        const c = curationData;
        const summary = $("cur-rating-summary");
        if (!c.is_public) {
            summary.textContent = "Curadorias privadas não recebem avaliações.";
        } else if (c.rating.count) {
            summary.replaceChildren(
                el("span", "rating-big", `★ ${formatAvg(c.rating.avg)}`),
                el("span", "", ` de média · ${plural(c.rating.count, "avaliação", "avaliações")}`),
            );
        } else {
            summary.textContent = c.is_owner ? "Ainda sem avaliações." : "Ainda sem avaliações. Seja a primeira pessoa a avaliar.";
        }

        const canRate = c.is_public && !c.is_owner && Boolean(currentUser);
        $("cur-rate").hidden = !canRate;
        $("rate-login").hidden = !(c.is_public && !c.is_owner && !currentUser);
        if (!canRate) return;

        const mine = c.my_rating || 0;
        const stars = [];
        for (let n = 1; n <= 5; n++) {
            const star = el("button", n <= mine ? "star is-on" : "star", n <= mine ? "★" : "☆");
            star.type = "button";
            star.setAttribute("role", "radio");
            star.setAttribute("aria-checked", String(n === mine));
            star.setAttribute("aria-label", plural(n, "estrela", "estrelas"));
            star.tabIndex = n === (mine || 1) ? 0 : -1;
            star.addEventListener("click", () => rate(n));
            star.addEventListener("keydown", (event) => {
                const next = { ArrowRight: n + 1, ArrowUp: n + 1, ArrowLeft: n - 1, ArrowDown: n - 1 }[event.key];
                if (!next || next < 1 || next > 5) return;
                event.preventDefault();
                rate(next, true);
            });
            // Prévia: passar o mouse acende as estrelas até ali.
            star.addEventListener("mouseenter", () => previewStars(n));
            stars.push(star);
        }
        const group = $("rate-stars");
        group.replaceChildren(...stars);
        group.onmouseleave = () => previewStars(0);
        $("rate-clear").hidden = !mine;
    }

    function previewStars(n) {
        const mine = curationData.my_rating || 0;
        $("rate-stars").querySelectorAll(".star").forEach((star, i) => {
            const on = n ? i < n : i < mine;
            star.textContent = on ? "★" : "☆";
            star.classList.toggle("is-on", on);
        });
    }

    async function rate(stars, keepFocus = false) {
        const c = curationData;
        try {
            const data = await api("PUT", `/curations/${c.id}/rating`, { stars });
            c.rating = data.rating;
            c.my_rating = data.my_rating;
            renderRating();
            if (keepFocus) $("rate-stars").children[stars - 1].focus();
            toast(`Você deu ${plural(stars, "estrela", "estrelas")} para “${c.title}”.`);
        } catch (err) {
            toast(err.message);
        }
    }
    $("rate-clear").addEventListener("click", async () => {
        const c = curationData;
        try {
            const data = await api("DELETE", `/curations/${c.id}/rating`);
            c.rating = data.rating;
            c.my_rating = data.my_rating;
            renderRating();
            toast("Sua nota foi retirada.");
        } catch (err) {
            toast(err.message);
        }
    });
    $("rate-login-btn").addEventListener("click", () => openAuth("login", "Entre para avaliar esta curadoria."));

    // --- Ordem e remoção (dono) -------------------------------------------
    async function moveItem(index, delta) {
        const c = curationData;
        const target = index + delta;
        if (target < 0 || target >= c.items.length) return;
        const previous = [...c.items];
        [c.items[index], c.items[target]] = [c.items[target], c.items[index]];
        renderCurationItems();
        requestAnimationFrame(() => {
            const card = $("cur-items").children[target];
            const button = card && card.querySelector(delta < 0 ? ".top-left" : ".top-right");
            (button && !button.disabled ? button : card && card.querySelector(".card-open"))?.focus();
        });
        try {
            await api("PUT", `/curations/${c.id}/order`, { artwork_ids: c.items.map((i) => i.artwork.id) });
        } catch (err) {
            c.items = previous;
            renderCurationItems();
            toast(err.message);
        }
    }

    async function removeItem(art) {
        const c = curationData;
        try {
            await api("DELETE", `/curations/${c.id}/items?artwork_id=${encodeURIComponent(art.id)}`);
            c.items = c.items.filter((i) => i.artwork.id !== art.id);
            renderCurationItems();
            $("cur-meta").textContent = `${plural(c.items.length, "obra", "obras")} · atualizada agora`;
            toast(`“${art.title || "Obra"}” saiu da curadoria.`);
            loadMine();
        } catch (err) {
            toast(err.message);
        }
    }

    // --- Exportar CSV ------------------------------------------------------
    function csvCell(value) {
        let text = String(value ?? "");
        if (/^[=+\-@]/.test(text)) text = "'" + text;  // evita fórmulas no Excel
        return `"${text.replace(/"/g, '""')}"`;
    }
    $("cur-export").addEventListener("click", () => {
        const c = curationData;
        const rows = [["posicao", "id", "titulo", "artista", "ano", "museu", "link", "visto_em", "nota"]];
        c.items.forEach(({ artwork: a, seen, note }, i) =>
            rows.push([i + 1, a.id, a.title, a.artist, a.year, a.museum, a.url, seen, note]));
        const blob = new Blob(["﻿" + rows.map((r) => r.map(csvCell).join(",")).join("\n")], { type: "text/csv" });
        const link = el("a");
        link.href = URL.createObjectURL(blob);
        link.download = `${c.title.replace(/[^\p{L}\p{N}]+/gu, "-").replace(/^-|-$/g, "").toLowerCase() || "curadoria"}.csv`;
        document.body.append(link);
        link.click();
        link.remove();
        setTimeout(() => URL.revokeObjectURL(link.href), 1000);
    });

    // --- Excluir -----------------------------------------------------------
    $("cur-delete").addEventListener("click", async () => {
        const c = curationData;
        const ok = await confirmAction({
            title: "Excluir esta curadoria?",
            text: `“${c.title}” e as notas das ${plural(c.items.length, "obra", "obras")} serão apagadas. Não dá para desfazer.`,
            ok: "Excluir",
        });
        if (!ok) return;
        try {
            await api("DELETE", `/curations/${c.id}`);
            await loadMine();
            toast(`Curadoria “${c.title}” excluída.`);
            navigate(`/u/${currentUser.username}`);
        } catch (err) {
            toast(err.message);
        }
    });

    // --- Criar / editar ----------------------------------------------------
    const curDialog = $("cur-dialog");
    const curForm = $("cur-form");
    let curFormMode = "create";

    function openCurationForm(mode) {
        curFormMode = mode;
        curForm.reset();
        curForm.querySelector(".auth-error").textContent = "";
        $("cf-private-warning").hidden = true;
        if (mode === "edit") {
            const c = curationData;
            $("cur-dialog-title").textContent = "Editar curadoria";
            $("cf-submit").textContent = "Salvar";
            curForm.title.value = c.title;
            curForm.description.value = c.description;
            curForm.visibility.value = c.is_public ? "public" : "private";
        } else {
            $("cur-dialog-title").textContent = "Nova curadoria";
            $("cf-submit").textContent = "Criar curadoria";
        }
        curDialog.showModal();
        curForm.title.focus();
    }
    curForm.addEventListener("change", () => {
        const losingRatings = curFormMode === "edit" && curationData.is_public && curationData.rating.count > 0;
        $("cf-private-warning").hidden = !(losingRatings && curForm.visibility.value === "private");
    });
    curForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        const errorBox = curForm.querySelector(".auth-error");
        const submit = $("cf-submit");
        submit.disabled = true;
        errorBox.textContent = "";
        const payload = {
            title: curForm.title.value,
            description: curForm.description.value,
            is_public: curForm.visibility.value === "public",
        };
        try {
            if (curFormMode === "edit") {
                const data = await api("PATCH", `/curations/${curationData.id}`, payload);
                curationData = data.curation;
                renderCuration();
                toast("Curadoria salva.");
            } else {
                const data = await api("POST", "/curations", payload);
                toast(`Curadoria “${data.curation.title}” criada. Agora toque no ♡ das obras para adicioná-las.`);
                navigate(`/c/${data.curation.id}`);
            }
            curDialog.close();
            loadMine();
        } catch (err) {
            errorBox.textContent = err.message;
        } finally {
            submit.disabled = false;
        }
    });
    $("cur-edit").addEventListener("click", () => openCurationForm("edit"));
    $("btn-new-curation").addEventListener("click", () => openCurationForm("create"));
    wireDialog(curDialog, $("cur-dialog-close"));

    // ======================================================================
    // Busca (Explorar)
    // ======================================================================
    const form = $("search-form");
    const queryInput = $("query-input");
    const museumSelect = $("museum-input");
    const searchButton = $("btn-search");
    const statusBox = $("status");
    const museumNote = $("museum-note");
    const grid = $("grid");
    const moreButton = $("btn-more");
    const STEP = 3;
    const MAX_LIMIT = 12;
    let results = [];
    let lastQuery = "";
    let canMore = false;
    let limit = STEP;
    let controller = null;
    let sequence = 0;

    function showStatus(text) { statusBox.textContent = text; }
    function showMuseumNote(text) { museumNote.textContent = text || ""; museumNote.hidden = !text; }
    function setLoading(isLoading) {
        searchButton.disabled = isLoading;
        grid.setAttribute("aria-busy", String(isLoading));
    }
    function failedMuseumsText(museums) {
        const failed = Object.entries(museums || {}).filter(([, info]) => info && info.error);
        if (failed.length === 0) return "";
        return `Sem resposta de: ${failed.map(([key, info]) => `${MUSEUM_NAMES[key] || key} (${info.error})`).join("; ")}.`;
    }

    function renderResults(data, query, more) {
        const incoming = Array.isArray(data.artworks) ? data.artworks : [];
        if (!more) { results = []; grid.replaceChildren(); }
        // Só "carregar mais" descarta o que já está na tela; uma busca nova mostra tudo.
        const fresh = incoming.filter((art) => !results.some((r) => r.id === art.id));
        const totalMuseums = Object.keys(data.museums || {}).length;
        const failedCount = Object.values(data.museums || {}).filter((m) => m && m.error).length;
        showMuseumNote(failedMuseumsText(data.museums));

        results.push(...fresh);
        grid.append(...fresh.map((art) => createCard(art)));
        canMore = fresh.length > 0 && limit < MAX_LIMIT;

        if (results.length === 0) {
            showStatus(totalMuseums > 0 && failedCount === totalMuseums
                ? "Nenhum museu respondeu agora. Tente novamente em instantes."
                : `Nenhum resultado para “${query}”. Tente termos em inglês (ex: Monet, Picasso).`);
        } else if (more && fresh.length === 0) {
            showStatus(`Não há mais obras para “${query}”.`);
        } else {
            showStatus(`${plural(results.length, "obra", "obras")} para “${query}”:`);
        }
        moreButton.hidden = !canMore;
    }

    async function runSearch(query, museum, more = false) {
        const mySequence = ++sequence;
        if (controller) controller.abort(); // descarta a busca anterior ainda em andamento
        const current = new AbortController();
        controller = current;
        const timer = setTimeout(() => current.abort(), REQUEST_TIMEOUT_MS);

        lastQuery = query;
        limit = more ? Math.min(limit + STEP, MAX_LIMIT) : STEP;
        setLoading(true);
        moreButton.disabled = true;
        if (!more) { grid.replaceChildren(); moreButton.hidden = true; showMuseumNote(""); }
        showStatus("Buscando nos acervos...");

        try {
            const params = new URLSearchParams({ q: query, museum, limit_per_museum: String(limit) });
            const res = await fetch(`/search?${params}`, { signal: current.signal });
            if (!res.ok) throw new Error(await readError(res));
            const data = await res.json();
            if (mySequence !== sequence) return; // chegou uma busca mais nova
            renderResults(data, query, more);
        } catch (err) {
            if (mySequence !== sequence) return;
            console.error("Erro na busca:", err);
            showStatus(err.name === "AbortError"
                ? "A busca demorou demais. Tente novamente."
                : `Não foi possível buscar agora (${err.message}).`);
            moreButton.hidden = !canMore;
        } finally {
            clearTimeout(timer);
            if (mySequence === sequence) { setLoading(false); moreButton.disabled = false; }
        }
    }

    form.addEventListener("submit", (event) => {
        event.preventDefault();
        const query = queryInput.value.trim();
        if (!query) { showStatus("Escreva algo para pesquisar."); queryInput.focus(); return; }
        runSearch(query, museumSelect.value);
    });
    moreButton.addEventListener("click", () => runSearch(lastQuery, museumSelect.value, true));

    // Termos amplos, em inglês (é o que os acervos entendem melhor).
    const DISCOVER_TERMS = [
        "impressionism", "still life", "portrait", "landscape", "sunflowers", "japanese woodblock",
        "armor", "sculpture", "porcelain", "ancient egypt", "self-portrait", "seascape", "textile",
        "horse", "mythology", "winter", "garden", "ukiyo-e",
    ];
    $("btn-random").addEventListener("click", () => {
        let term;
        do { term = DISCOVER_TERMS[Math.floor(Math.random() * DISCOVER_TERMS.length)]; }
        while (term === lastQuery && DISCOVER_TERMS.length > 1);
        queryInput.value = term;
        museumSelect.value = "all";
        runSearch(term, "all");
    });

    // ======================================================================
    // Conta: entrar, criar conta, sair
    // ======================================================================
    const authDialog = $("auth");
    const loginForm = $("login-form");
    const registerForm = $("register-form");
    const loginButton = $("btn-login");
    const logoutButton = $("btn-logout");
    const accountUser = $("account-user");
    const regPassword = $("reg-password");

    function renderAccount() {
        const loggedIn = Boolean(currentUser);
        $("account").classList.remove("is-loading");
        loginButton.hidden = loggedIn;
        accountUser.hidden = logoutButton.hidden = tabs.feed.hidden = tabs.profile.hidden = !loggedIn;
        $("account-name").textContent = loggedIn ? `@${currentUser.username}` : "";
        if (loggedIn) {
            const myPath = `/u/${currentUser.username}`;
            accountUser.href = tabs.profile.href = myPath;
            paintAvatar($("account-avatar"), currentUser);
        }
    }

    function setAuthMode(mode) {
        const isLogin = mode === "login";
        $("auth-title").textContent = isLogin ? "Que bom ver você de novo" : "Comece seu diário de arte";
        $("auth-tab-login").setAttribute("aria-pressed", String(isLogin));
        $("auth-tab-register").setAttribute("aria-pressed", String(!isLogin));
        loginForm.hidden = !isLogin;
        registerForm.hidden = isLogin;
        authDialog.querySelectorAll(".auth-error").forEach((p) => { p.textContent = ""; });
        if (authDialog.open) (isLogin ? loginForm : registerForm).querySelector("input").focus();
    }

    function openAuth(mode = "login", reason = "") {
        setAuthMode(mode);
        $("auth-reason").textContent = reason;
        $("auth-reason").hidden = !reason;
        if (!authDialog.open) authDialog.showModal();
        (mode === "login" ? loginForm : registerForm).querySelector("input").focus();
    }

    async function sendAuth(url, payload, authForm) {
        const errorBox = authForm.querySelector(".auth-error");
        const submit = authForm.querySelector('button[type="submit"]');
        errorBox.textContent = "";
        submit.disabled = true;
        try {
            return (await api("POST", url, payload)).user;
        } catch (err) {
            errorBox.textContent = err.message;
            return null;
        } finally {
            submit.disabled = false;
        }
    }

    async function signedIn(user, message) {
        currentUser = user;
        renderAccount();
        loginForm.reset();
        registerForm.reset();
        regPassword.type = "password";
        authDialog.close();
        toast(message);
        await afterLogin();
        route();  // redesenha a tela atual já como logado
    }

    async function afterLogin() {
        await loadMine();
        await migrateOldCollection();
        try { top5 = (await api("GET", "/profile")).top5 || []; } catch (_) { top5 = []; }
        refreshDetailTop();
    }

    function signedOut() {
        currentUser = null;
        myCurations = [];
        top5 = [];
        rebuildArtIds();
        renderAccount();
    }

    loginForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        const user = await sendAuth("/auth/login", {
            login: loginForm.login.value.trim(),
            password: loginForm.password.value,
        }, loginForm);
        if (user) signedIn(user, `Bem-vindo(a) de volta, @${user.username}.`);
        else loginForm.password.select();
    });
    registerForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        const user = await sendAuth("/auth/register", {
            username: registerForm.username.value.trim(),
            email: registerForm.email.value.trim(),
            password: registerForm.password.value,
        }, registerForm);
        if (user) signedIn(user, `Conta criada. Bem-vindo(a) ao artlog, @${user.username}!`);
    });
    $("reg-show").addEventListener("change", (event) => { regPassword.type = event.target.checked ? "text" : "password"; });
    $("auth-tab-login").addEventListener("click", () => setAuthMode("login"));
    $("auth-tab-register").addEventListener("click", () => setAuthMode("register"));
    loginButton.addEventListener("click", () => openAuth("login"));
    wireDialog(authDialog, $("auth-close"));

    logoutButton.addEventListener("click", async () => {
        try { await fetch("/auth/logout", { method: "POST" }); } catch (_) { /* sai localmente mesmo assim */ }
        signedOut();
        toast("Você saiu da sua conta.");
        if (location.pathname === "/seguindo") navigate("/"); else route();
    });

    // A "Minha coleção" antiga ficava só no navegador. No primeiro login depois
    // da mudança, ela vira uma curadoria privada (com as notas e datas) e sai do navegador.
    async function migrateOldCollection() {
        let raw = null;
        try { raw = localStorage.getItem("artlog-collection"); } catch (_) { return; }
        if (!raw) return;
        let data;
        try { data = JSON.parse(raw); } catch (_) { return; }
        if (!data || typeof data !== "object" || Array.isArray(data)) return;

        const validImage = (src) => typeof src === "string" &&
            (src.startsWith("https://") || /^\/artic-image\/[A-Za-z0-9_-]{1,64}$/.test(src));
        const entries = Object.values(data)
            .filter((e) => e && e.art && typeof e.art.id === "string" && /^[a-z]+_\S+$/.test(e.art.id) && validImage(e.art.image))
            .sort((a, b) => (b.savedAt || 0) - (a.savedAt || 0))
            .slice(0, 500);
        try {
            if (entries.length) {
                await api("POST", "/curations", {
                    title: "Minha coleção",
                    description: "Obras que salvei antes das curadorias existirem.",
                    is_public: false,
                    items: entries.map((e) => ({
                        artwork: cleanArt(e.art),
                        note: typeof e.note === "string" ? e.note.slice(0, 2000) : "",
                        seen: /^\d{4}-\d{2}-\d{2}$/.test(e.seen) ? e.seen : "",
                    })),
                });
                await loadMine();
                toast(`Sua coleção antiga (${plural(entries.length, "obra", "obras")}) virou a curadoria privada “Minha coleção”.`);
            }
            // Guarda uma cópia de segurança e tira do lugar antigo, para não migrar duas vezes.
            localStorage.setItem("artlog-collection-backup", raw);
            localStorage.removeItem("artlog-collection");
        } catch (err) {
            console.warn("Não foi possível migrar a coleção antiga:", err);
        }
    }

    // ======================================================================
    // Editar perfil, senha e foto
    // ======================================================================
    const editDialog = $("profile-edit");
    const profileForm = $("profile-form");
    const passwordForm = $("password-form");
    const bioInput = $("pf-bio");
    const bioCount = $("pf-bio-count");
    const avatarFile = $("avatar-file");
    const avatarMsg = $("avatar-msg");
    const avatarRemove = $("btn-avatar-remove");

    function updateUser(user) {
        const oldName = currentUser.username;
        currentUser = user;
        renderAccount();
        paintAvatar($("edit-avatar"), user);
        avatarRemove.hidden = !user.avatar_url;
        if (profileData && profileData.is_me) {
            profileData.user = { ...profileData.user, ...user };
            delete profileData.user.email;
            renderProfile();
            // Mudou o @: o endereço do perfil muda junto.
            if (oldName !== user.username && location.pathname === `/u/${oldName}`) {
                history.replaceState({}, "", `/u/${user.username}`);
            }
        }
    }

    function openEdit() {
        profileForm.display_name.value = currentUser.display_name || "";
        profileForm.username.value = currentUser.username;
        profileForm.email.value = currentUser.email;
        bioInput.value = currentUser.bio || "";
        bioCount.textContent = `${bioInput.value.length}/300`;
        passwordForm.reset();
        passwordForm.username.value = currentUser.username;
        editDialog.querySelectorAll(".auth-error, .auth-ok").forEach((p) => { p.textContent = ""; });
        paintAvatar($("edit-avatar"), currentUser);
        avatarRemove.hidden = !currentUser.avatar_url;
        editDialog.showModal();
    }
    bioInput.addEventListener("input", () => { bioCount.textContent = `${bioInput.value.length}/300`; });
    $("btn-edit-profile").addEventListener("click", openEdit);
    wireDialog(editDialog, $("profile-edit-close"));

    profileForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        const errorBox = profileForm.querySelector(".auth-error");
        const submit = profileForm.querySelector('button[type="submit"]');
        errorBox.textContent = "";
        submit.disabled = true;
        try {
            const data = await api("PATCH", "/profile", {
                display_name: profileForm.display_name.value,
                username: profileForm.username.value.trim(),
                email: profileForm.email.value.trim(),
                bio: bioInput.value,
            });
            updateUser(data.user);
            editDialog.close();
            toast("Perfil salvo.");
        } catch (err) {
            errorBox.textContent = err.message;
        } finally {
            submit.disabled = false;
        }
    });

    passwordForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        const errorBox = passwordForm.querySelector(".auth-error");
        const okBox = passwordForm.querySelector(".auth-ok");
        const submit = passwordForm.querySelector('button[type="submit"]');
        errorBox.textContent = okBox.textContent = "";
        submit.disabled = true;
        try {
            await api("POST", "/profile/password", {
                current_password: passwordForm.current_password.value,
                new_password: passwordForm.new_password.value,
            });
            passwordForm.current_password.value = passwordForm.new_password.value = "";
            okBox.textContent = "Senha alterada.";
        } catch (err) {
            errorBox.textContent = err.message;
        } finally {
            submit.disabled = false;
        }
    });

    // A foto é cortada em quadrado e reduzida para 400x400 aqui no navegador:
    // o envio fica leve e os dados escondidos da foto (como a localização GPS) não vão junto.
    async function squareJpeg(file, size = 400) {
        let source;
        try { source = await createImageBitmap(file); }
        catch (_) { throw new Error("Não consegui abrir essa imagem. Use uma foto JPG ou PNG."); }
        const side = Math.min(source.width, source.height);
        const canvas = document.createElement("canvas");
        canvas.width = canvas.height = size;
        const ctx = canvas.getContext("2d");
        ctx.fillStyle = "#ffffff";  // fundo branco para PNG com transparência
        ctx.fillRect(0, 0, size, size);
        ctx.drawImage(source, (source.width - side) / 2, (source.height - side) / 2, side, side, 0, 0, size, size);
        if (source.close) source.close();
        const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.88));
        if (!blob) throw new Error("Não consegui processar essa imagem.");
        return blob;
    }

    $("btn-avatar-pick").addEventListener("click", () => avatarFile.click());
    avatarFile.addEventListener("change", async () => {
        const file = avatarFile.files[0];
        avatarFile.value = "";
        if (!file) return;
        avatarMsg.textContent = "";
        try {
            const blob = await squareJpeg(file);
            const data = await api("PUT", "/profile/avatar", blob, { "Content-Type": "image/jpeg" });
            updateUser(data.user);
            toast("Foto atualizada.");
        } catch (err) {
            avatarMsg.textContent = err.message;
        }
    });
    avatarRemove.addEventListener("click", async () => {
        avatarMsg.textContent = "";
        try {
            const data = await api("DELETE", "/profile/avatar");
            updateUser(data.user);
            toast("Foto removida.");
        } catch (err) {
            avatarMsg.textContent = err.message;
        }
    });

    // ======================================================================
    // Início
    // ======================================================================
    (async () => {
        try {
            const res = await fetch("/auth/me");
            if (res.ok) currentUser = (await res.json()).user;
        } catch (_) { currentUser = null; }
        renderAccount();
        if (currentUser) await afterLogin();
        route();
    })();
})();
