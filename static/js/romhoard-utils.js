/**
 * RomHoard Pure Utilities
 * Only pure functions and initialization code
 */

function getCsrfToken() {
    const match = document.cookie.match(/(^|;)\s*csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[2]) : '';
}

function spinnerHtml(message = '') {
    const messageHtml = message ? `<p class="mt-4">${message}</p>` : '';
    return `<div class="text-center">
        <div class="animate-spin h-8 w-8 border-4 border-[var(--color-primary)] border-t-transparent rounded-full mx-auto"></div>
        ${messageHtml}
    </div>`;
}

function initNavHighlight() {
    const currentPath = window.location.pathname;
    // Support both desktop (.retro-nav-btn) and mobile (.retro-nav-btn-mobile) nav links
    document.querySelectorAll('.retro-nav-btn, .retro-nav-btn-mobile').forEach(link => {
        const href = link.getAttribute('href');
        if (href === '/' && currentPath === '/') {
            link.classList.add('active');
        } else if (href === '/' && (currentPath.startsWith('/library/') || currentPath.startsWith('/games/'))) {
            link.classList.add('active');
        } else if (href !== '/' && currentPath.startsWith(href)) {
            link.classList.add('active');
        }
    });
}

function initBase(csrfToken) {
    initNavHighlight();
    initGlobalSearch();
    if (csrfToken) {
        document.body.addEventListener('htmx:configRequest', function(event) {
            event.detail.headers['X-CSRFToken'] = csrfToken;
        });
    }
}

/**
 * Initialize global search functionality
 * - On library page: enhance nav search with HTMX for live search
 * - Handle browser back/forward with popstate
 */
function initGlobalSearch() {
    const searchInput = document.getElementById('nav-search');
    const container = document.getElementById('system-grid-container');

    // Only enhance if on library page (where search results container exists)
    if (searchInput && container && window.location.pathname === '/library/') {
        // Add HTMX attributes for live search on library page
        searchInput.setAttribute('hx-get', '/library/search/');
        searchInput.setAttribute('hx-trigger', 'input changed delay:300ms, search');
        searchInput.setAttribute('hx-target', '#system-grid-container');
        searchInput.setAttribute('hx-push-url', 'true');
        htmx.process(searchInput);
    }

    // Handle browser back/forward
    window.addEventListener('popstate', function() {
        const query = new URLSearchParams(window.location.search).get('q') || '';
        const navSearchInput = document.getElementById('nav-search');

        // Update search input value and trigger Alpine reactivity
        if (navSearchInput) {
            navSearchInput.value = query;
            // Dispatch input event to sync Alpine's x-model binding
            navSearchInput.dispatchEvent(new Event('input', { bubbles: true }));
        }

        // Reload content if on library page
        const gridContainer = document.getElementById('system-grid-container');
        if (gridContainer && window.location.pathname === '/library/') {
            htmx.ajax('GET', window.location.href, { target: '#system-grid-container' });
        }
    });
}

function autoTriggerDownload(linkId) {
    const link = document.getElementById(linkId);
    if (link && !link.dataset.triggered) {
        link.dataset.triggered = 'true';
        setTimeout(function() {
            window.location.href = link.href;
        }, 500);
    }
}

// Export to window for Alpine stores
window.getCsrfToken = getCsrfToken;
window.spinnerHtml = spinnerHtml;

// Export RomHoard namespace for templates
window.RomHoard = {
    initBase,
    autoTriggerDownload,
    getCsrfToken
};
