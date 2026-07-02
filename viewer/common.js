(function() {
  // 1. Get division and season/year from URL query param, then localStorage, then default
  const urlParams = new URLSearchParams(window.location.search);
  let division = urlParams.get('division') || localStorage.getItem('division') || 'mixed';
  
  // Normalize division
  if (division === 'men') division = 'mens';
  if (division === 'women') division = 'womens';
  if (!['mixed', 'mens', 'womens'].includes(division)) {
    division = 'mixed';
  }
  
  // Save back to localStorage
  localStorage.setItem('division', division);
  window.currentDivision = division;

  let season = urlParams.get('season') || urlParams.get('year') || localStorage.getItem('season') || '2026';
  // Validate season year range: 2018 to 2027
  const validSeasons = ['2018', '2019', '2020', '2021', '2022', '2023', '2024', '2025', '2026', '2027'];
  if (!validSeasons.includes(season)) {
    season = '2026';
  }
  localStorage.setItem('season', season);
  window.currentSeason = season;
  
  // 2. Get data path helper
  window.getDataPath = function(filename) {
    return `../out/${division}/${season}/${filename}`;
  };
  
  // 3. Helper to build link carrying division and season query params
  window.getLinkUrl = function(href, extraParams = {}) {
    try {
      const url = new URL(href, window.location.href);
      url.searchParams.set('division', division);
      url.searchParams.set('season', season);
      for (const [k, v] of Object.entries(extraParams)) {
        if (v === null || v === undefined) {
          url.searchParams.delete(k);
        } else {
          url.searchParams.set(k, v);
        }
      }
      // If same origin and relative path in project
      if (url.origin === window.location.origin) {
        return url.pathname + url.search + url.hash;
      }
      return url.href;
    } catch (e) {
      // Fallback for relative paths if URL parsing fails
      const prefix = href.includes('?') ? '&' : '?';
      return `${href}${prefix}division=${division}&season=${season}`;
    }
  };

  // 4. Inject styles for selectors
  const style = document.createElement('style');
  style.textContent = `
    .div-selector {
      display: inline-flex;
      background: var(--surface2, #1a2035);
      border: 1px solid var(--border, #222c44);
      border-radius: 20px;
      padding: 2px;
      gap: 2px;
      margin-left: 1.5rem;
      vertical-align: middle;
    }
    .div-btn {
      background: transparent;
      border: none;
      color: var(--text-dim, #6b80a8);
      padding: 4px 12px;
      font-size: 0.78rem;
      font-weight: 600;
      border-radius: 18px;
      cursor: pointer;
      transition: color 0.15s, background-color 0.15s, box-shadow 0.15s;
    }
    .div-btn:hover {
      color: var(--text, #dce6f5);
    }
    .div-btn.active {
      background: var(--accent, #4a9eff);
      color: #ffffff;
      box-shadow: 0 2px 8px rgba(74, 158, 255, 0.35);
    }
    .season-selector {
      display: inline-flex;
      align-items: center;
      background: var(--surface2, #1a2035);
      border: 1px solid var(--border, #222c44);
      border-radius: 20px;
      padding: 2px 10px 2px 14px;
      margin-left: 0.75rem;
      vertical-align: middle;
      cursor: pointer;
      color: var(--text-dim, #6b80a8);
      font-size: 0.78rem;
      font-weight: 600;
      transition: border-color 0.15s, color 0.15s;
    }
    .season-selector:hover {
      border-color: var(--accent, #4a9eff);
      color: var(--text, #dce6f5);
    }
    .season-select {
      background: transparent;
      border: none;
      color: inherit;
      font-size: inherit;
      font-weight: inherit;
      cursor: pointer;
      outline: none;
      padding-right: 4px;
      appearance: none;
      -webkit-appearance: none;
    }
    .season-selector::after {
      content: "";
      display: inline-block;
      width: 0;
      height: 0;
      margin-left: 6px;
      vertical-align: middle;
      border-left: 4px solid transparent;
      border-right: 4px solid transparent;
      border-top: 4px solid currentColor;
      pointer-events: none;
    }
    @media (max-width: 768px) {
      .div-selector {
        margin-left: 0.5rem;
      }
      .div-btn {
        padding: 3px 8px;
        font-size: 0.72rem;
      }
      .season-selector {
        margin-left: 0.25rem;
        padding: 2px 6px 2px 10px;
        font-size: 0.72rem;
      }
    }
  `;
  document.head.appendChild(style);

  // 5. Setup DOMContentLoaded handler to update titles, headers, insert selector, and patch links
  document.addEventListener('DOMContentLoaded', () => {
    // A. Update page titles
    const divisionNames = {
      'mixed': 'Mixed',
      'mens': "Men's",
      'womens': "Women's"
    };
    const divLabel = divisionNames[division];

    // Document title
    if (document.title) {
      document.title = document.title.replace(/Club Mixed|Club Men's|Club Women's/g, `Club ${divLabel}`);
      document.title = document.title.replace(/\b20\d{2}\b/g, season);
    }

    // Wordmark text
    const wmName = document.querySelector('.wm-name') || document.querySelector('.wm-sub');
    if (wmName) {
      wmName.textContent = wmName.textContent.replace(/Club Mixed|Club Men's|Club Women's/g, `Club ${divLabel}`);
      wmName.textContent = wmName.textContent.replace(/\b20\d{2}\b/g, season);
    }

    // Hero title
    const heroTitle = document.querySelector('.hero-title');
    if (heroTitle) {
      heroTitle.innerHTML = `Club ${divLabel} <span>${season}</span>`;
    }

    const heroSub = document.querySelector('.hero-sub');
    if (heroSub) {
      heroSub.textContent = heroSub.textContent.replace(/\b20\d{2}\b/g, season);
    }

    // B. Find wordmark to insert selectors after it
    const wordmark = document.querySelector('.wordmark') || document.querySelector('.wm');
    if (wordmark && !document.querySelector('.div-selector')) {
      const container = document.createElement('div');
      container.className = 'div-selector';
      
      const btnMixed = document.createElement('button');
      btnMixed.className = 'div-btn' + (division === 'mixed' ? ' active' : '');
      btnMixed.textContent = 'Mixed';
      btnMixed.onclick = () => switchDivision('mixed');

      const btnMens = document.createElement('button');
      btnMens.className = 'div-btn' + (division === 'mens' ? ' active' : '');
      btnMens.textContent = "Men's";
      btnMens.onclick = () => switchDivision('mens');

      const btnWomens = document.createElement('button');
      btnWomens.className = 'div-btn' + (division === 'womens' ? ' active' : '');
      btnWomens.textContent = "Women's";
      btnWomens.onclick = () => switchDivision('womens');

      container.appendChild(btnMixed);
      container.appendChild(btnMens);
      container.appendChild(btnWomens);

      // Create season selector container
      const seasonContainer = document.createElement('div');
      seasonContainer.className = 'season-selector';
      
      const select = document.createElement('select');
      select.className = 'season-select';
      
      // Supported seasons: 2018-2027 in reverse chronological order
      const seasons = [2027, 2026, 2025, 2024, 2023, 2022, 2021, 2020, 2019, 2018];
      seasons.forEach(yr => {
        const opt = document.createElement('option');
        opt.value = yr;
        opt.textContent = yr;
        if (yr.toString() === season) {
          opt.selected = true;
        }
        opt.style.background = '#141928';
        opt.style.color = '#dce6f5';
        select.appendChild(opt);
      });
      
      select.onchange = (e) => switchSeason(e.target.value);
      seasonContainer.appendChild(select);

      // Insert them after wordmark
      const nextNode = wordmark.nextSibling;
      wordmark.parentNode.insertBefore(container, nextNode);
      wordmark.parentNode.insertBefore(seasonContainer, nextNode);
    }

    // C. Automatically rewrite links to preserve division and season
    updateAllLinks();
  });

  function switchDivision(newDiv) {
    if (newDiv === division) return;
    localStorage.setItem('division', newDiv);
    
    // Refresh page with new division query parameter
    const url = new URL(window.location.href);
    url.searchParams.set('division', newDiv);
    url.searchParams.set('season', season);
    
    if (window.location.pathname.endsWith('team.html')) {
      window.location.href = `rankings.html?division=${newDiv}&season=${season}`;
    } else {
      window.location.href = url.pathname + url.search + url.hash;
    }
  }

  function switchSeason(newSeason) {
    if (newSeason === season) return;
    localStorage.setItem('season', newSeason);
    
    // Refresh page with new season query parameter
    const url = new URL(window.location.href);
    url.searchParams.set('division', division);
    url.searchParams.set('season', newSeason);
    
    if (window.location.pathname.endsWith('team.html')) {
      window.location.href = `rankings.html?division=${division}&season=${newSeason}`;
    } else {
      window.location.href = url.pathname + url.search + url.hash;
    }
  }

  function updateAllLinks() {
    // Rewrites hrefs for links targeting local pages
    const links = document.querySelectorAll('a');
    links.forEach(a => {
      const href = a.getAttribute('href');
      if (!href) return;
      
      // Skip absolute external links, JavaScript, anchors
      if (href.startsWith('http://') || href.startsWith('https://') || href.startsWith('//') || href.startsWith('javascript:') || href.startsWith('#')) {
        return;
      }
      
      // Update href
      a.setAttribute('href', window.getLinkUrl(href));
    });
  }

  // Hook into dynamically generated HTML if any
  window.updateDynamicLinks = updateAllLinks;
})();
