document.addEventListener('DOMContentLoaded', () => {
    const container = document.getElementById('articles-container');
    const loading = document.getElementById('loading-spinner');
    const totalArticles = document.getElementById('total-articles');
    const searchInput = document.getElementById('search-input');
    const categoryFilters = document.getElementById('category-filters');
    
    let allData = [];
    let currentFilter = 'all';
    let currentSort = 'date';
    let searchQuery = '';

    // Kaynaklara gore ikon eslestirme
    const sourceIcons = {
        'arXiv': '📄',
        'EarthArXiv': '🌍',
        'Crossref': '🔬',
        'Reddit': '💬',
        'Perplexity': '🤖',
        'default': '📰'
    };

    function getSourceIcon(source) {
        for (const [key, icon] of Object.entries(sourceIcons)) {
            if (source.toLowerCase().includes(key.toLowerCase())) return icon;
        }
        return sourceIcons.default;
    }

    function getScoreClass(score) {
        if (score >= 8) return ''; // Green
        if (score >= 5) return 'medium'; // Yellow
        return 'low'; // Red
    }

    function formatDate(isoString) {
        if (!isoString) return 'Bilinmiyor';
        const date = new Date(isoString);
        return date.toLocaleDateString('tr-TR', { day: 'numeric', month: 'short', year: 'numeric' });
    }

    function renderArticles() {
        const heroSection = document.getElementById('hero-section');
        heroSection.style.display = 'none';
        heroSection.innerHTML = '';
        
        let filtered = allData.filter(item => {
            const matchesCat = currentFilter === 'all' || (item.category && item.category.toLowerCase() === currentFilter.toLowerCase());
            const searchLower = searchQuery.toLowerCase();
            const matchesSearch = !searchQuery || 
                (item.title && item.title.toLowerCase().includes(searchLower)) ||
                (item.source && item.source.toLowerCase().includes(searchLower)) ||
                (item.abstract && item.abstract.toLowerCase().includes(searchLower));
            
            return matchesCat && matchesSearch;
        });

        // Siralama
        filtered.sort((a, b) => {
            if (currentSort === 'score') {
                return (b.score || 0) - (a.score || 0);
            } else {
                const dateA = new Date(a.added_at || 0);
                const dateB = new Date(b.added_at || 0);
                return dateB - dateA; // En yeni en uste
            }
        });

        totalArticles.textContent = filtered.length;
        container.innerHTML = '';

        if (filtered.length === 0) {
            container.innerHTML = '<div style="grid-column: 1/-1; text-align: center; padding: 3rem; color: var(--text-secondary);">Sonuç bulunamadı.</div>';
            return;
        }

        // Hero karti var mi (İlk öğede image_url varsa ve arama yapılmıyorsa)
        if (filtered[0] && filtered[0].image_url && !searchQuery && currentFilter === 'all' && currentSort === 'date') {
            const top = filtered.shift();
            const stars = '⭐'.repeat(Math.min(5, Math.ceil((top.score || 0) / 2)));
            
            heroSection.style.display = 'block';
            heroSection.innerHTML = `
                <div class="hero-card" style="background-image: url('${top.image_url}')">
                    <div class="hero-overlay">
                        <span class="category-tag">Günün Manşeti: ${top.category || 'Genel'}</span>
                        <h2 class="article-title">
                            <a href="${top.link}" target="_blank" rel="noopener noreferrer">${top.title}</a>
                        </h2>
                        <p class="article-abstract">${top.abstract ? top.abstract.substring(0, 300) + '...' : ''}</p>
                        <div class="card-footer" style="border-top: none; padding-top: 0;">
                            <div class="source-tag">
                                <span class="source-icon">${getSourceIcon(top.source || '')}</span>
                                <span>${top.source || 'Bilinmeyen Kaynak'}</span>
                            </div>
                            <div class="date-tag">
                                <span class="score-badge" style="background: rgba(0,0,0,0.5);">${stars} ${top.score}/10</span>
                            </div>
                        </div>
                    </div>
                </div>
            `;
        }

        filtered.forEach(item => {
            const scoreClass = getScoreClass(item.score);
            const stars = '⭐'.repeat(Math.min(5, Math.ceil((item.score || 0) / 2)));
            
            const card = document.createElement('div');
            card.className = 'article-card';
            card.innerHTML = `
                <div class="card-header">
                    <span class="category-tag">${item.category || 'Genel'}</span>
                    <span class="score-badge ${scoreClass}">${stars} ${item.score}/10</span>
                </div>
                <h3 class="article-title">
                    <a href="${item.link}" target="_blank" rel="noopener noreferrer">${item.title}</a>
                </h3>
                <p class="article-abstract">${item.abstract ? item.abstract.substring(0, 150) + '...' : 'Özet bulunmuyor.'}</p>
                <div class="card-footer">
                    <div class="source-tag">
                        <span class="source-icon">${getSourceIcon(item.source || '')}</span>
                        <span>${item.source || 'Bilinmeyen Kaynak'}</span>
                    </div>
                    <div class="date-tag">${formatDate(item.added_at)}</div>
                </div>
            `;
            container.appendChild(card);
        });
    }

    function setupCategories() {
        const categories = new Set();
        allData.forEach(item => {
            if (item.category) categories.add(item.category.toLowerCase());
        });

        categories.forEach(cat => {
            const btn = document.createElement('button');
            btn.className = 'filter-btn';
            btn.setAttribute('data-filter', cat);
            btn.textContent = cat.charAt(0).toUpperCase() + cat.slice(1);
            categoryFilters.appendChild(btn);
        });

        // Event listeners for categories
        document.querySelectorAll('.filter-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
                e.target.classList.add('active');
                currentFilter = e.target.getAttribute('data-filter');
                renderArticles();
            });
        });
    }

    function setupSortingAndSearch() {
        document.querySelectorAll('.sort-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                document.querySelectorAll('.sort-btn').forEach(b => b.classList.remove('active'));
                e.target.classList.add('active');
                currentSort = e.target.getAttribute('data-sort');
                renderArticles();
            });
        });

        searchInput.addEventListener('input', (e) => {
            searchQuery = e.target.value;
            renderArticles();
        });
    }

    // Veriyi Yukle
    fetch('data.json')
        .then(response => {
            if (!response.ok) throw new Error('Veri dosyasi bulunamadi');
            return response.json();
        })
        .then(data => {
            allData = data;
            loading.style.display = 'none';
            setupCategories();
            setupSortingAndSearch();
            renderArticles();
        })
        .catch(err => {
            loading.innerHTML = `<p style="color: var(--danger)">Hata: Veritabanı (data.json) yüklenemedi. Bot henüz veri oluşturmamış olabilir.</p>`;
            console.error(err);
        });
});
