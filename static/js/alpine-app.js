// Alpine.js stores and configurations for RomHoard
document.addEventListener('alpine:init', () => {
    // Modal store
    Alpine.store('modals', {
        active: [],
        locked: new Set(),

        lock(id) {
            this.locked.add(id);
        },

        unlock(id) {
            this.locked.delete(id);
        },

        isLocked(id) {
            return this.locked.has(id);
        },

        open(id) {
            const modal = document.getElementById(`modal-${id}`);
            if (!modal) {
                console.warn(`Modal "modal-${id}" not found`);
                return;
            }
            modal.classList.remove('hidden');
            modal.classList.add('flex');
            document.body.style.overflow = 'hidden';
            if (!this.active.includes(id)) {
                this.active.push(id);
            }
            document.dispatchEvent(new CustomEvent('modal-opened', { detail: { id } }));
        },

        close(id) {
            const modal = document.getElementById(`modal-${id}`);
            if (!modal) return;
            modal.classList.add('hidden');
            modal.classList.remove('flex');
            this.active = this.active.filter(m => m !== id);
            this.locked.delete(id);  // Auto-unlock on close
            if (this.active.length === 0) {
                document.body.style.overflow = 'auto';
            }
            document.dispatchEvent(new CustomEvent('modal-closed', { detail: { id } }));
        },

        closeAll() {
            [...this.active].forEach(id => this.close(id));
        },

        isOpen(id) {
            return this.active.includes(id);
        }
    });

    // Selection store
    Alpine.store('selection', {
        items: new Set(),
        metadata: new Map(),
        config: null,

        configure(config) {
            this.config = config;
            this.items = new Set();
            this.metadata = new Map();
        },

        add(id, meta = null) {
            this.items.add(id);
            if (meta) this.metadata.set(id, meta);
            this.updateUI();
        },

        remove(id) {
            this.items.delete(id);
            this.metadata.delete(id);
            this.updateUI();
        },

        toggle(id, meta = null) {
            if (this.has(id)) {
                this.remove(id);
            } else {
                this.add(id, meta);
            }
        },

        has(id) {
            return this.items.has(id);
        },

        clear() {
            this.items = new Set();
            this.metadata = new Map();
            if (this.config?.checkboxClass) {
                document.querySelectorAll(`.${this.config.checkboxClass}`)
                    .forEach(cb => cb.checked = false);
            }
            if (this.config?.selectAllId) {
                const selectAll = document.getElementById(this.config.selectAllId);
                if (selectAll) selectAll.checked = false;
            }
            this.updateUI();
        },

        getIds() {
            return Array.from(this.items);
        },

        get size() {
            return this.items.size;
        },

        getMeta(id) {
            return this.metadata.get(id);
        },

        updateUI() {
            if (this.config?.countId) {
                const countEl = document.getElementById(this.config.countId);
                if (countEl) {
                    const count = this.size;
                    const itemType = this.config.itemType || 'item';
                    let label;
                    if (itemType === 'entry') {
                        label = count === 1 ? 'entry' : 'entries';
                    } else {
                        label = count === 1 ? itemType : `${itemType}s`;
                    }
                    countEl.textContent = `${count} ${label} selected`;
                }
            }

            if (this.config?.toolbarId) {
                const toolbar = document.getElementById(this.config.toolbarId);
                if (toolbar) {
                    toolbar.classList.toggle('visible', this.size > 0);
                }
            }

            if (this.config?.selectAllId && this.config?.checkboxClass) {
                const selectAll = document.getElementById(this.config.selectAllId);
                const allCheckboxes = document.querySelectorAll(`.${this.config.checkboxClass}`);
                const checkedCount = Array.from(allCheckboxes).filter(cb => cb.checked).length;

                if (selectAll && allCheckboxes.length > 0) {
                    selectAll.checked = checkedCount === allCheckboxes.length;
                    selectAll.indeterminate = checkedCount > 0 && checkedCount < allCheckboxes.length;
                }
            }

            if (this.config?.onUpdate) {
                this.config.onUpdate(this);
            }
        },

        restoreCheckboxes() {
            if (!this.config?.checkboxClass) return;
            document.querySelectorAll(`.${this.config.checkboxClass}`).forEach(cb => {
                const idKey = this.config.itemType ? `${this.config.itemType}Id` : 'itemId';
                const id = parseInt(cb.dataset[idKey] || cb.dataset.itemId);
                cb.checked = this.has(id);
            });
            this.updateUI();
        }
    });

    // Transfer config store
    Alpine.store('transferConfig', {
        type: 'ftp',
        host: '',
        port: '',
        user: '',
        password: '',
        anonymous: false,
        pathPrefix: '',
        testStatus: 'idle',
        testMessage: '',

        reset() {
            this.type = 'ftp';
            this.host = '';
            this.port = '';
            this.user = '';
            this.password = '';
            this.anonymous = false;
            this.pathPrefix = '';
            this.testStatus = 'idle';
            this.testMessage = '';
        },

        async testConnection(deviceId) {
            this.testStatus = 'testing';
            this.testMessage = '';

            const formData = new FormData();
            formData.append('csrfmiddlewaretoken', getCsrfToken());
            formData.append('device_id', deviceId);
            formData.append('transfer_type', this.type);
            formData.append('transfer_host', this.host);
            formData.append('transfer_port', this.port);
            formData.append('transfer_user', this.user);
            formData.append('transfer_password', this.password);
            formData.append('transfer_anonymous', this.anonymous);
            formData.append('transfer_path_prefix', this.pathPrefix);

            try {
                const response = await fetch('/devices/test-connection/', {
                    method: 'POST',
                    body: formData
                });

                const contentType = response.headers.get('content-type');
                let data;
                if (contentType?.includes('application/json')) {
                    data = await response.json();
                } else {
                    data = { success: false, error: await response.text() };
                }

                if (data.success) {
                    this.testStatus = 'success';
                    this.testMessage = data.message || 'Connection successful!';
                } else {
                    this.testStatus = 'error';
                    this.testMessage = data.error || data.message || 'Connection failed';
                }
            } catch (error) {
                this.testStatus = 'error';
                this.testMessage = `Error: ${error.message}`;
            }
        },

        getPayload() {
            return {
                transfer_type: this.type,
                transfer_host: this.host,
                transfer_port: this.port,
                transfer_user: this.user,
                transfer_password: this.password,
                transfer_anonymous: this.anonymous,
                transfer_path_prefix: this.pathPrefix
            };
        }
    });

    // Download manager store
    Alpine.store('downloadManager', {
        deviceId: null,
        saveAsDefault: false,
        progressContent: '',
        collectionCreator: null,
        collectionSlug: null,
        collectionName: null,
        matchedCount: 0,
        gameIds: [],

        // Fetch and display game preview in modal
        async loadGamePreview(targetId) {
            const target = document.getElementById(targetId);
            if (!target || target.dataset.loaded) return;

            const body = this.collectionSlug
                ? { collection_creator: this.collectionCreator, collection_slug: this.collectionSlug }
                : { game_ids: this.gameIds };

            try {
                const response = await fetch('/preview-games/', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': getCsrfToken()
                    },
                    body: JSON.stringify(body)
                });
                target.innerHTML = await response.text();
                target.dataset.loaded = 'true';
            } catch (error) {
                target.innerHTML = '<p class="text-[var(--color-text-muted)] text-sm">Failed to load games</p>';
            }
        },

        // Reset preview state when modal opens
        resetPreview() {
            const previews = document.querySelectorAll('[id$="-games-preview"]');
            previews.forEach(el => {
                el.innerHTML = '';
                delete el.dataset.loaded;
            });
        },

        async saveDefaultIfChecked() {
            if (!this.saveAsDefault || !this.deviceId) return;
            try {
                const formData = new FormData();
                formData.append('device_id', this.deviceId);
                await fetch('/devices/set-default/', {
                    method: 'POST',
                    headers: {
                        'X-CSRFToken': getCsrfToken()
                    },
                    body: formData
                });
            } catch (error) {
                console.error('Failed to save default device:', error);
            }
        },

        async startDownload(endpoint, body) {
            await this.saveDefaultIfChecked();
            Alpine.store('modals').close('device-picker');
            Alpine.store('modals').open('download-progress');
            Alpine.store('modals').lock('download-progress');
            this.progressContent = spinnerHtml('Preparing download...');

            const contentEl = document.getElementById('download-modal-content');
            if (contentEl) contentEl.innerHTML = this.progressContent;

            try {
                const response = await fetch(endpoint, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': getCsrfToken()
                    },
                    body: JSON.stringify({ device_id: this.deviceId, ...body })
                });

                const data = await response.json();

                if (data.redirect_url) {
                    window.location.href = data.redirect_url;
                    Alpine.store('modals').close('download-progress');
                } else if (data.job_id) {
                    htmx.ajax('GET', `/download/status/${data.job_id}/`, { target: '#download-modal-content' });
                } else {
                    throw new Error('Invalid response from server');
                }
            } catch (error) {
                if (contentEl) {
                    contentEl.innerHTML = `
                        <div class="p-6 text-center">
                            <p class="retro-result-error mb-4">Error: ${error.message}</p>
                            <button data-modal-close="download-progress" class="retro-btn">Close</button>
                        </div>`;
                }
            }
        },

        async startSend(endpoint, body) {
            if (!this.deviceId) {
                alert('Please select a device');
                return;
            }

            await this.saveDefaultIfChecked();
            Alpine.store('modals').close('send');
            Alpine.store('modals').open('send-progress');
            Alpine.store('modals').lock('send-progress');
            this.progressContent = spinnerHtml('Preparing to send...');

            const contentEl = document.getElementById('send-progress-content');
            if (contentEl) contentEl.innerHTML = this.progressContent;

            try {
                const transferConfig = Alpine.store('transferConfig').getPayload();
                const response = await fetch(endpoint, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': getCsrfToken()
                    },
                    body: JSON.stringify({ device_id: this.deviceId, ...transferConfig, ...body })
                });

                const data = await response.json();

                if (!response.ok) {
                    throw new Error(data.error || 'Server error');
                }

                if (data.job_id) {
                    htmx.ajax('GET', `/send/status/${data.job_id}/`, { target: '#send-progress-content' });
                } else {
                    throw new Error('Invalid response from server');
                }
            } catch (error) {
                if (contentEl) {
                    contentEl.innerHTML = `
                        <div class="p-6 text-center">
                            <p class="retro-result-error mb-4">Error: ${error.message}</p>
                            <button data-modal-close="send-progress" class="retro-btn">Close</button>
                        </div>`;
                }
            }
        }
    });

    // Romset picker store
    Alpine.store('romsetPicker', {
        romsetId: null,

        async open(romsetId) {
            this.romsetId = romsetId;
            Alpine.store('modals').open('romset-download');
            htmx.ajax('GET', `/download/romset/${romsetId}/picker/`, { target: '#romset-download-modal-content' });
        },

        close() {
            Alpine.store('modals').close('romset-download');
        },

        async start(csrfToken) {
            this.close();
            Alpine.store('modals').open('download-progress');
            Alpine.store('modals').lock('download-progress');

            const contentEl = document.getElementById('download-modal-content');
            if (contentEl) contentEl.innerHTML = spinnerHtml('Preparing files...');

            try {
                const response = await fetch(`/download/romset/${this.romsetId}/start/`, {
                    method: 'POST',
                    headers: { 'X-CSRFToken': csrfToken }
                });

                const data = await response.json();

                if (data.redirect_url) {
                    window.location.href = data.redirect_url;
                    Alpine.store('modals').close('download-progress');
                } else if (data.job_id) {
                    htmx.ajax('GET', `/download/status/${data.job_id}/`, { target: '#download-modal-content' });
                } else {
                    throw new Error('Invalid response from server');
                }
            } catch (error) {
                if (contentEl) {
                    contentEl.innerHTML = `
                        <div class="p-6 text-center">
                            <p class="retro-result-error mb-4">Error: ${error.message}</p>
                            <button data-modal-close="download-progress" class="retro-btn">Close</button>
                        </div>`;
                }
            }
        }
    });

    // Game download store
    Alpine.store('gameDownload', {
        async handle(gameId) {
            try {
                const response = await fetch(`/download/game/${gameId}/?picker=1`);
                const contentType = response.headers.get('content-type');

                if (contentType?.includes('application/json')) {
                    const data = await response.json();
                    if (data.picker_url) {
                        const romsetId = data.picker_url.match(/\/romset\/(\d+)\//)?.[1];
                        if (romsetId) {
                            Alpine.store('romsetPicker').open(romsetId);
                            return;
                        }
                    }
                }

                window.location.href = `/download/game/${gameId}/`;
            } catch (error) {
                console.error('Download error:', error);
                window.location.href = `/download/game/${gameId}/`;
            }
        }
    });

    // Toast notification store
    Alpine.store('toast', {
        message: '',
        type: 'success',
        visible: false,
        timeout: null,
        link: null,
        linkText: null,

        show(message, type = 'success', duration = 3000, link = null, linkText = null) {
            if (this.timeout) clearTimeout(this.timeout);

            this.message = message;
            this.type = type;
            this.link = link;
            this.linkText = linkText;
            this.visible = true;

            this.timeout = setTimeout(() => {
                this.visible = false;
            }, duration);
        },

        hide() {
            this.visible = false;
            if (this.timeout) clearTimeout(this.timeout);
        }
    });

    // Search filters store for advanced library search
    Alpine.store('searchFilters', {
        query: '',
        // Multi-select: arrays of {slug, name} objects
        systems: [],
        genres: [],
        ratingOp: '',
        ratingMin: null,
        ratingMax: null,
        filtersExpanded: false,

        // Single-system mode (for game list page)
        singleSystemMode: false,
        fixedSystem: null,      // { slug, name }
        targetContainer: '#system-grid-container',

        // Collection mode (for collection detail page)
        collectionMode: false,
        collectionCreator: null,
        collectionSlug: null,
        matchStatus: 'all',     // 'all' | 'in_library' | 'not_in_library'

        // Sort state (for collection mode)
        sort: 'position',
        order: 'asc',

        configure(opts = {}) {
            // Reset all mode flags first
            this.singleSystemMode = false;
            this.fixedSystem = null;
            this.collectionMode = false;
            this.collectionCreator = null;
            this.collectionSlug = null;
            this.matchStatus = 'all';
            this.targetContainer = '#system-grid-container';

            if (opts.collection) {
                // Collection mode
                this.collectionMode = true;
                this.collectionCreator = opts.creator;
                this.collectionSlug = opts.collection;
                this.targetContainer = opts.target || '#collection-entries-container';
            } else if (opts.singleSystem) {
                // Single system mode
                this.singleSystemMode = true;
                this.fixedSystem = { slug: opts.singleSystem, name: opts.systemName || opts.singleSystem };
                this.targetContainer = opts.target || '#game-table-container';
            }

            // Reset filter state when reconfiguring
            this.query = '';
            this.systems = [];
            this.genres = [];
            this.ratingOp = '';
            this.ratingMin = null;
            this.ratingMax = null;
            this.sort = 'position';
            this.order = 'asc';
        },

        setSort(sort, defaultOrder = 'asc') {
            if (this.sort === sort) {
                // Toggle order if clicking same sort
                this.order = this.order === 'asc' ? 'desc' : 'asc';
            } else {
                this.sort = sort;
                this.order = defaultOrder;
            }
            this.search();
        },

        // Get system param for genre dropdown (single system or multi-select)
        getSystemParam() {
            if (this.singleSystemMode && this.fixedSystem) {
                return this.fixedSystem.slug;
            }
            return this.systems.map(s => s.slug).join(',');
        },

        get activeFilterCount() {
            // In single-system mode, don't count systems
            // In collection mode, count systems
            const systemCount = this.singleSystemMode ? 0 : this.systems.length;
            const statusCount = (this.collectionMode && this.matchStatus !== 'all') ? 1 : 0;
            return systemCount + this.genres.length + (this.ratingOp && this.ratingMin ? 1 : 0) + statusCount;
        },

        setMatchStatus(status) {
            this.matchStatus = status;
            this.search();
        },

        clearMatchStatus() {
            this.matchStatus = 'all';
        },

        toggleSystem(slug, name) {
            const idx = this.systems.findIndex(s => s.slug === slug);
            if (idx >= 0) {
                this.systems.splice(idx, 1);
            } else {
                this.systems.push({ slug, name });
            }
            this.search();
        },

        toggleGenre(slug, name) {
            const idx = this.genres.findIndex(g => g.slug === slug);
            if (idx >= 0) {
                this.genres.splice(idx, 1);
            } else {
                this.genres.push({ slug, name });
            }
            this.search();
        },

        hasSystem(slug) {
            return this.systems.some(s => s.slug === slug);
        },

        hasGenre(slug) {
            return this.genres.some(g => g.slug === slug);
        },

        removeSystem(slug) {
            const idx = this.systems.findIndex(s => s.slug === slug);
            if (idx >= 0) this.systems.splice(idx, 1);
            this.search();
        },

        removeGenre(slug) {
            const idx = this.genres.findIndex(g => g.slug === slug);
            if (idx >= 0) this.genres.splice(idx, 1);
            this.search();
        },

        clearRating() {
            this.ratingOp = '';
            this.ratingMin = null;
            this.ratingMax = null;
            this.search();
        },

        toParams() {
            const params = new URLSearchParams();
            if (this.query) params.set('q', this.query);
            // In single-system mode, don't include system in params (it's in the URL path)
            // In collection mode, include system for filtering within collection
            if (!this.singleSystemMode && this.systems.length) {
                params.set('system', this.systems.map(s => s.slug).join(','));
            }
            if (this.genres.length) params.set('genre', this.genres.map(g => g.slug).join(','));
            if (this.ratingOp && this.ratingMin) {
                params.set('rating_op', this.ratingOp);
                params.set('rating_min', this.ratingMin);
                if (this.ratingOp === 'between' && this.ratingMax) {
                    params.set('rating_max', this.ratingMax);
                }
            }
            // In collection mode, include status filter and sort
            if (this.collectionMode) {
                if (this.matchStatus !== 'all') {
                    params.set('status', this.matchStatus);
                }
                if (this.sort && this.sort !== 'position') {
                    params.set('sort', this.sort);
                    params.set('order', this.order);
                } else if (this.sort === 'position' && this.order !== 'asc') {
                    params.set('sort', this.sort);
                    params.set('order', this.order);
                }
            }
            return params;
        },

        fromUrl() {
            const params = new URLSearchParams(window.location.search);
            this.query = params.get('q') || '';

            // Parse systems - only in global mode and collection mode
            // (single-system mode ignores URL system param)
            let systemSlugs = [];
            if (!this.singleSystemMode) {
                systemSlugs = (params.get('system') || '').split(',').filter(s => s);
                this.systems = systemSlugs.map(slug => ({ slug, name: slug }));
            }

            // Parse genres - use slugs initially, then fetch names
            const genreSlugs = (params.get('genre') || '').split(',').filter(g => g);
            this.genres = genreSlugs.map(slug => ({ slug, name: slug }));

            // Parse rating
            this.ratingOp = params.get('rating_op') || '';
            this.ratingMin = params.get('rating_min') || null;
            this.ratingMax = params.get('rating_max') || null;

            // Parse status and sort (collection mode only)
            if (this.collectionMode) {
                this.matchStatus = params.get('status') || 'all';
                this.sort = params.get('sort') || 'position';
                this.order = params.get('order') || 'asc';
            }

            // Track if we have filters to trigger search
            const hasFilters = this.query || systemSlugs.length || genreSlugs.length ||
                (this.ratingOp && this.ratingMin) || (this.collectionMode && this.matchStatus !== 'all');

            // Fetch display names for systems and genres
            if (systemSlugs.length > 0) {
                const baseUrl = this.collectionMode
                    ? `/collections/${this.collectionCreator}/${this.collectionSlug}/filter-options/systems/`
                    : '/filter-options/systems/';
                fetch(baseUrl + '?selected=' + systemSlugs.join(','))
                    .then(r => r.text())
                    .then(html => {
                        const parser = new DOMParser();
                        const doc = parser.parseFromString(html, 'text/html');
                        this.systems = this.systems.map(sys => {
                            const label = doc.querySelector(`[data-slug="${sys.slug}"]`);
                            return { slug: sys.slug, name: label?.dataset.name || sys.slug };
                        });
                    });
            }
            if (genreSlugs.length > 0) {
                const baseUrl = this.collectionMode
                    ? `/collections/${this.collectionCreator}/${this.collectionSlug}/filter-options/genres/`
                    : '/filter-options/genres/';
                fetch(baseUrl + '?selected=' + genreSlugs.join(','))
                    .then(r => r.text())
                    .then(html => {
                        const parser = new DOMParser();
                        const doc = parser.parseFromString(html, 'text/html');
                        this.genres = this.genres.map(g => {
                            const label = doc.querySelector(`[data-slug="${g.slug}"]`);
                            return { slug: g.slug, name: label?.dataset.name || g.slug };
                        });
                    });
            }

            // Trigger search if URL has any filters (without updating URL again)
            if (hasFilters) {
                this.searchFromUrl();
            }
        },

        // Search triggered by URL params - does not update URL
        searchFromUrl() {
            const params = this.toParams();
            let url;

            if (this.collectionMode && this.collectionSlug) {
                url = `/collections/${this.collectionCreator}/${this.collectionSlug}/search/?${params.toString()}`;
            } else if (this.singleSystemMode && this.fixedSystem) {
                url = `/library/${this.fixedSystem.slug}/search/?${params.toString()}`;
            } else {
                url = '/search/?' + params.toString();
            }

            htmx.ajax('GET', url, {
                target: this.targetContainer,
                swap: 'innerHTML'
            });
        },

        clearAll() {
            this.query = '';
            this.systems = [];
            this.genres = [];
            this.ratingOp = '';
            this.ratingMin = null;
            this.ratingMax = null;
            if (this.collectionMode) {
                this.matchStatus = 'all';
            }
            this.search();
        },

        search() {
            const params = this.toParams();
            let url, newBrowserUrl;

            if (this.collectionMode && this.collectionSlug) {
                url = `/collections/${this.collectionCreator}/${this.collectionSlug}/search/?${params.toString()}`;
                newBrowserUrl = `/collections/${this.collectionCreator}/${this.collectionSlug}/${params.toString() ? '?' + params.toString() : ''}`;
            } else if (this.singleSystemMode && this.fixedSystem) {
                url = `/library/${this.fixedSystem.slug}/search/?${params.toString()}`;
                newBrowserUrl = `/library/${this.fixedSystem.slug}/${params.toString() ? '?' + params.toString() : ''}`;
            } else {
                url = '/search/?' + params.toString();
                newBrowserUrl = '/' + (params.toString() ? '?' + params.toString() : '');
            }

            // Update URL and trigger HTMX
            htmx.ajax('GET', url, {
                target: this.targetContainer,
                swap: 'innerHTML'
            });

            // Update browser URL without reload
            window.history.pushState({}, '', newBrowserUrl);
        },

        // Check if any filters are active (for collection drag-drop disable)
        hasActiveFilters() {
            return this.query || this.systems.length || this.genres.length ||
                (this.ratingOp && this.ratingMin) || (this.collectionMode && this.matchStatus !== 'all');
        }
    });

    // Hub votes store - tracks which collections user has voted for (client-side only)
    Alpine.store('hubVotes', {
        voted: new Set(),

        init() {
            this.refreshFromCookie();
        },

        refreshFromCookie() {
            this.voted.clear();
            const cookie = document.cookie
                .split('; ')
                .find(row => row.startsWith('hub_votes='));
            if (cookie) {
                try {
                    const encoded = cookie.split('=')[1];
                    const decoded = decodeURIComponent(encoded);
                    const slugs = JSON.parse(decoded);
                    if (Array.isArray(slugs)) {
                        slugs.forEach(slug => this.voted.add(slug));
                    }
                } catch (e) {
                    // Invalid cookie format, ignore
                }
            }
        },

        has(slug) {
            return this.voted.has(slug);
        },

        add(slug) {
            this.voted.add(slug);
        },

        remove(slug) {
            this.voted.delete(slug);
        }
    });

    // Initialize hub votes on page load
    Alpine.store('hubVotes').init();

    // Collection picker store
    Alpine.store('collectionPicker', {
        selectedCreator: null,
        selectedSlug: null,
        selectedName: null,
        pendingGame: null,

        reset() {
            this.selectedCreator = null;
            this.selectedSlug = null;
            this.selectedName = null;
            this.pendingGame = null;
        },

        setGame(gameId, gameName, systemSlug) {
            this.pendingGame = { gameId, gameName, systemSlug };
        },

        loadPicker(targetId, games = null) {
            let url = '/collections/picker/';
            const params = new URLSearchParams();

            if (games && games.length > 0) {
                // Bulk mode - pass games as JSON
                params.set('games', JSON.stringify(games));
            } else if (this.pendingGame) {
                // Single game mode
                params.set('game_name', this.pendingGame.gameName);
                params.set('system_slug', this.pendingGame.systemSlug);
            }

            const queryString = params.toString();
            if (queryString) {
                url += '?' + queryString;
            }

            htmx.ajax('GET', url, { target: '#' + targetId, swap: 'innerHTML' });
        },

        async addSingle(notes = '', onSuccess = null) {
            if (!this.selectedCreator || !this.selectedSlug || !this.pendingGame) {
                Alpine.store('toast').show('Please select a collection', 'error');
                return;
            }

            const formData = new FormData();
            formData.append('game_name', this.pendingGame.gameName);
            formData.append('system_slug', this.pendingGame.systemSlug);
            formData.append('notes', notes);

            try {
                const response = await fetch(`/collections/${this.selectedCreator}/${this.selectedSlug}/entries/add/`, {
                    method: 'POST',
                    headers: { 'X-CSRFToken': getCsrfToken() },
                    body: formData
                });

                if (response.ok) {
                    const data = await response.json();
                    Alpine.store('modals').close('add-to-collection');
                    Alpine.store('toast').show(
                        'Added to ',
                        'success',
                        5000,
                        `/collections/${this.selectedCreator}/${this.selectedSlug}/`,
                        data.collection_name
                    );
                    this.reset();
                    if (onSuccess) onSuccess(data);
                } else {
                    const text = await response.text();
                    Alpine.store('toast').show(text || 'Failed to add', 'error');
                }
            } catch (error) {
                Alpine.store('toast').show(`Error: ${error.message}`, 'error');
            }
        },

        async addBulk(games) {
            if (!this.selectedCreator || !this.selectedSlug) {
                Alpine.store('toast').show('Please select a collection', 'error');
                return;
            }

            try {
                const response = await fetch(`/collections/${this.selectedCreator}/${this.selectedSlug}/entries/bulk-add/`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': getCsrfToken()
                    },
                    body: JSON.stringify({ games })
                });

                const data = await response.json();

                if (data.success) {
                    Alpine.store('modals').close('bulk-add-to-collection');
                    let msg = `Added ${data.added} game${data.added === 1 ? '' : 's'} to `;
                    if (data.skipped > 0) {
                        msg += ` (${data.skipped} already in collection)`;
                    }
                    Alpine.store('toast').show(
                        msg,
                        'success',
                        5000,
                        `/collections/${this.selectedCreator}/${this.selectedSlug}/`,
                        data.collection_name
                    );
                    Alpine.store('selection').clear();
                    this.reset();
                } else {
                    Alpine.store('toast').show(data.error || 'Failed to add', 'error');
                }
            } catch (error) {
                Alpine.store('toast').show(`Error: ${error.message}`, 'error');
            }
        }
    });
});

// Global keyboard handler for Escape key
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && window.Alpine && Alpine.store('modals')?.active.length > 0) {
        const topModal = Alpine.store('modals').active.slice(-1)[0];
        if (!Alpine.store('modals').isLocked(topModal)) {
            Alpine.store('modals').close(topModal);
        }
    }
});

// HTMX afterSwap integration for selection restoration
// Use document instead of document.body since this script may load before body exists
document.addEventListener('htmx:afterSwap', (e) => {
    if (!window.Alpine) return;
    const selection = Alpine.store('selection');
    if (selection?.config?.containerIdForSwap &&
        e.detail.target.id === selection.config.containerIdForSwap) {
        selection.restoreCheckboxes();
    }
});

// Selection checkbox event delegation
document.addEventListener('change', (e) => {
    if (!window.Alpine) return;
    const selection = Alpine.store('selection');
    if (!selection?.config) return;

    // Handle individual item checkboxes
    if (selection.config.checkboxClass && e.target.matches(`.${selection.config.checkboxClass}`)) {
        const idKey = selection.config.itemType ? `${selection.config.itemType}Id` : 'itemId';
        const id = parseInt(e.target.dataset[idKey] || e.target.dataset.itemId);

        if (e.target.checked) {
            const meta = {};
            if (e.target.dataset.gameId) meta.gameId = parseInt(e.target.dataset.gameId);
            if (e.target.dataset.systemSlug) meta.systemSlug = e.target.dataset.systemSlug;
            if (e.target.dataset.isMatched) meta.isMatched = e.target.dataset.isMatched === 'true';
            selection.add(id, Object.keys(meta).length > 0 ? meta : null);
        } else {
            selection.remove(id);
        }
    }

    // Handle select-all checkbox
    if (selection.config.selectAllId && e.target.id === selection.config.selectAllId) {
        const isChecked = e.target.checked;
        document.querySelectorAll(`.${selection.config.checkboxClass}`).forEach(cb => {
            cb.checked = isChecked;
            const idKey = selection.config.itemType ? `${selection.config.itemType}Id` : 'itemId';
            const id = parseInt(cb.dataset[idKey] || cb.dataset.itemId);

            if (isChecked) {
                const meta = {};
                if (cb.dataset.gameId) meta.gameId = parseInt(cb.dataset.gameId);
                if (cb.dataset.systemSlug) meta.systemSlug = cb.dataset.systemSlug;
                if (cb.dataset.isMatched) meta.isMatched = cb.dataset.isMatched === 'true';
                selection.add(id, Object.keys(meta).length > 0 ? meta : null);
            } else {
                selection.remove(id);
            }
        });
    }
});

// Clear selection button click handler
document.addEventListener('click', (e) => {
    if (!window.Alpine) return;
    const selection = Alpine.store('selection');
    if (!selection?.config?.clearBtnId) return;

    if (e.target.id === selection.config.clearBtnId || e.target.closest(`#${selection.config.clearBtnId}`)) {
        selection.clear();
    }
});

// data-modal-open click handler
document.addEventListener('click', (e) => {
    if (!window.Alpine) return;
    const trigger = e.target.closest('[data-modal-open]');
    if (trigger) {
        const modalId = trigger.dataset.modalOpen;
        Alpine.store('modals').open(modalId);
    }
});

// data-modal-close click handler
document.addEventListener('click', (e) => {
    if (!window.Alpine) return;
    const trigger = e.target.closest('[data-modal-close]');
    if (trigger) {
        const modalId = trigger.dataset.modalClose;
        Alpine.store('modals').close(modalId);
    }
});
