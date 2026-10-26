function setAccent(colorHex) {
    document.documentElement.style.setProperty('--brand-accent', colorHex);
}

document.addEventListener("DOMContentLoaded", () => {
    const searchForm = document.getElementById("search-form");
    const searchInput = document.getElementById("search-input");
    const museumSelect = document.getElementById("museum-select");
    const statusMessage = document.getElementById("status-message");
    const artworksGrid = document.getElementById("artworks-grid");

    if (!searchForm) return;

    searchForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        
        const query = searchInput.value.trim();
        const museum = museumSelect.value;

        if (!query) return;

        artworksGrid.innerHTML = "";
        statusMessage.style.display = "block";
        statusMessage.textContent = "A pesquisar nos acervos dos museus...";

        try {
            const response = await fetch(`/search?q=${encodeURIComponent(query)}&museum=${museum}&limit_per_museum=3`);
            
            if (!response.ok) {
                throw new Error(`Erro na API: ${response.status}`);
            }

            const data = await response.json();

            if (!data.artworks || data.artworks.length === 0) {
                statusMessage.textContent = `Nenhum resultado encontrado para "${query}". Tente pesquisar por termos em inglês (ex: "Monet", "Picasso", "Sunflower").`;
                return;
            }

            statusMessage.textContent = `A exibir ${data.total_results} obras para "${query}":`;
            renderArtworks(data.artworks);

        } catch (error) {
            console.error("Erro na busca:", error);
            statusMessage.textContent = "Erro ao ligar ao servidor. Verifique a consola do navegador.";
        }
    });

    function renderArtworks(artworks) {
        artworksGrid.innerHTML = "";
        
        artworks.forEach((art) => {
            const card = document.createElement("article");
            card.className = "card";

            const imageUrl = art.image || "https://via.placeholder.com/400x500/f9f9f9/14121f?text=Sem+Imagem";

            card.innerHTML = `
                <div class="card-image-wrapper">
                    <img 
                        src="${imageUrl}" 
                        alt="${art.title}" 
                        loading="lazy" 
                        onerror="this.src='https://via.placeholder.com/400x500/f9f9f9/14121f?text=Imagem+Indispon%C3%ADvel'"
                    >
                </div>
                <h3 class="card-title">${art.title}</h3>
                <p class="card-artist">${art.artist}</p>
                <div class="card-meta">
                    <span>${art.year}</span>
                    <span>${art.museum}</span>
                </div>
            `;

            artworksGrid.appendChild(card);
        });
    }
});