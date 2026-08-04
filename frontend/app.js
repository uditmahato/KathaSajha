/* KathaSajha frontend — vanilla JS SPA over the FastAPI backend. */
(function () {
    'use strict';

    const TOKEN_KEY = 'kathasajha_token';
    const $ = (id) => document.getElementById(id);

    const els = {
        userNav: $('userNav'), usageBadge: $('usageBadge'),
        navLibrary: $('navLibrary'), navLogout: $('navLogout'),
        guestNav: $('guestNav'), navLogin: $('navLogin'), navSignup: $('navSignup'),
        landingView: $('landingView'), heroSignup: $('heroSignup'), footerSignup: $('footerSignup'),
        authView: $('authView'), createView: $('createView'), storyView: $('storyView'),
        forgotView: $('forgotView'), forgotForm: $('forgotForm'), forgotEmail: $('forgotEmail'),
        forgotMessage: $('forgotMessage'), forgotError: $('forgotError'), forgotSubmit: $('forgotSubmit'),
        forgotLink: $('forgotLink'), backToLogin: $('backToLogin'),
        resetView: $('resetView'), resetForm: $('resetForm'), resetPassword: $('resetPassword'),
        resetError: $('resetError'), resetSubmit: $('resetSubmit'),
        tabLogin: $('tabLogin'), tabRegister: $('tabRegister'),
        authForm: $('authForm'), authName: $('authName'), nameField: $('nameField'),
        authEmail: $('authEmail'), authPassword: $('authPassword'),
        authError: $('authError'), authSubmit: $('authSubmit'),
        storyPrompt: $('storyPrompt'), promptCount: $('promptCount'),
        heroName: $('heroName'), storyLanguage: $('storyLanguage'),
        generateBtn: $('generateBtn'), sampleBtn: $('sampleBtn'), createError: $('createError'),
        progressPanel: $('progressPanel'), progressStage: $('progressStage'),
        progressBar: $('progressBar'), progressDetail: $('progressDetail'), progressTrack: $('progressTrack'),
        libraryGrid: $('libraryGrid'), libraryEmpty: $('libraryEmpty'),
        backBtn: $('backBtn'), shareBtn: $('shareBtn'), pdfBtn: $('pdfBtn'), deleteBtn: $('deleteBtn'),
        shareInfo: $('shareInfo'), storyTitle: $('storyTitle'), storyContent: $('storyContent'),
        toastHost: $('toastHost'), confirmBackdrop: $('confirmBackdrop'), confirmTitle: $('confirmTitle'),
        confirmBody: $('confirmBody'), confirmOk: $('confirmOk'), confirmCancel: $('confirmCancel'),
        landingPlans: $('landingPlans'),
        upgradeBackdrop: $('upgradeBackdrop'), upgradeBody: $('upgradeBody'), upgradePlan: $('upgradePlan'),
        upgradeResult: $('upgradeResult'), upgradeDismiss: $('upgradeDismiss'), upgradeInterest: $('upgradeInterest'),
    };

    // ---------- Feedback primitives ----------
    function toast(message, kind) {
        const el = document.createElement('div');
        el.className = 'toast' + (kind ? ' ' + kind : '');
        el.textContent = message;
        els.toastHost.appendChild(el);
        setTimeout(() => el.remove(), kind === 'error' ? 6000 : 4000);
    }

    /** Accessible replacement for window.confirm. Resolves true/false. */
    function confirmDialog(opts) {
        return new Promise((resolve) => {
            const previouslyFocused = document.activeElement;
            els.confirmTitle.textContent = opts.title || 'Are you sure?';
            els.confirmBody.textContent = opts.body || '';
            els.confirmOk.textContent = opts.okLabel || 'Confirm';
            els.confirmBackdrop.classList.remove('hidden');
            els.confirmOk.focus();

            function cleanup(result) {
                els.confirmBackdrop.classList.add('hidden');
                els.confirmOk.removeEventListener('click', onOk);
                els.confirmCancel.removeEventListener('click', onCancel);
                document.removeEventListener('keydown', onKey);
                els.confirmBackdrop.removeEventListener('mousedown', onBackdrop);
                if (previouslyFocused && previouslyFocused.focus) previouslyFocused.focus();
                resolve(result);
            }
            function onOk() { cleanup(true); }
            function onCancel() { cleanup(false); }
            function onBackdrop(e) { if (e.target === els.confirmBackdrop) cleanup(false); }
            function onKey(e) {
                if (e.key === 'Escape') { cleanup(false); return; }
                if (e.key !== 'Tab') return;
                // Trap focus inside the dialog.
                const focusables = [els.confirmCancel, els.confirmOk];
                const first = focusables[0], last = focusables[focusables.length - 1];
                if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
                else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
            }
            els.confirmOk.addEventListener('click', onOk);
            els.confirmCancel.addEventListener('click', onCancel);
            els.confirmBackdrop.addEventListener('mousedown', onBackdrop);
            document.addEventListener('keydown', onKey);
        });
    }

    let currentStory = null;   // story object shown in the reader
    let pollTimer = null;
    let pollGen = 0;           // bumping this invalidates any in-flight poll loop
    let libraryTimer = null;   // auto-refresh while stories are generating
    let isSharedView = false;
    let currentSharedSlug = null; // the public PDF endpoint is keyed by slug, not id

    // ---------- API helpers ----------
    function token() { return localStorage.getItem(TOKEN_KEY); }

    async function api(path, options = {}) {
        const headers = Object.assign({ 'Content-Type': 'application/json' }, options.headers || {});
        const t = token();
        if (t) headers['Authorization'] = 'Bearer ' + t;
        const resp = await fetch(path, Object.assign({}, options, { headers }));
        if (resp.status === 401 && !path.startsWith('/api/auth/')) {
            logout();
            throw new Error('Session expired. Please log in again.');
        }
        if (resp.status === 204) return null;
        let body = null;
        try { body = await resp.json(); } catch (_) { /* non-JSON */ }
        if (!resp.ok) {
            const detail = body && body.detail;
            const msg = typeof detail === 'string' ? detail
                : Array.isArray(detail) && detail[0] ? (detail[0].msg || 'Invalid input')
                : 'Request failed (' + resp.status + ')';
            const err = new Error(msg);
            err.status = resp.status;
            // The server marks the daily wall specifically; it deserves an offer,
            // not the same treatment as a validation error.
            err.quotaExhausted = ['daily', 'monthly'].indexOf(resp.headers.get('X-Quota-Exhausted')) !== -1;
            throw err;
        }
        return body;
    }

    // ---------- View switching ----------
    const ALL_VIEWS = ['landingView', 'authView', 'forgotView', 'resetView', 'createView', 'storyView'];

    function show(view) {
        ALL_VIEWS.forEach((k) => els[k].classList.add('hidden'));
        view.classList.remove('hidden');
        // Guest nav accompanies the marketing pages; user nav the app.
        const loggedOut = [els.landingView, els.authView, els.forgotView, els.resetView].indexOf(view) !== -1;
        els.guestNav.classList.toggle('hidden', view !== els.landingView);
        if (loggedOut) els.userNav.classList.add('hidden');
        window.scrollTo({ top: 0 });
        // Move focus into the new view so keyboard and screen-reader users are not
        // stranded where the old view used to be.
        view.setAttribute('tabindex', '-1');
        view.focus({ preventScroll: true });
    }

    function setError(el, message) {
        if (message) { el.textContent = message; el.classList.remove('hidden'); }
        else { el.textContent = ''; el.classList.add('hidden'); }
    }

    function logout() {
        localStorage.removeItem(TOKEN_KEY);
        els.userNav.classList.add('hidden');
        document.getElementById('deleteAccountWrap').classList.add('hidden');
        stopPolling();
        if (libraryTimer) { clearTimeout(libraryTimer); libraryTimer = null; }
        // Clear everything the previous account left behind: on a shared
        // computer the next person must not see the last family's story,
        // library, or usage — especially right after an account deletion.
        currentStory = null;
        isSharedView = false;
        currentSharedSlug = null;
        plansCache = null;
        els.storyTitle.textContent = '';
        els.storyContent.replaceChildren();
        els.libraryGrid.replaceChildren();
        els.usageBadge.textContent = '';
        show(els.landingView);
    }

    async function refreshUsage() {
        try {
            const u = await api('/api/auth/usage');
            // The monthly allowance is the real one; the daily cap only smooths
            // bursts. Show whichever is actually binding, so the number on screen
            // is the number that will stop them.
            els.usageBadge.textContent = u.remaining_today < u.remaining_this_month
                ? u.remaining_today + ' stories left today'
                : u.remaining_this_month + ' of ' + u.monthly_limit + ' stories left this month';
        } catch (_) { els.usageBadge.textContent = ''; }
    }

    // ---------- Auth ----------
    let authMode = 'login';

    function setAuthMode(mode) {
        authMode = mode;
        els.tabLogin.classList.toggle('active', mode === 'login');
        els.tabRegister.classList.toggle('active', mode === 'register');
        els.nameField.classList.toggle('hidden', mode === 'login');
        els.authSubmit.textContent = mode === 'login' ? 'Log in' : 'Create account';
        setError(els.authError, '');
    }

    els.tabLogin.addEventListener('click', () => setAuthMode('login'));
    els.tabRegister.addEventListener('click', () => setAuthMode('register'));

    els.authForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        setError(els.authError, '');
        els.authSubmit.disabled = true;
        try {
            const payload = { email: els.authEmail.value.trim(), password: els.authPassword.value };
            if (authMode === 'register') payload.display_name = els.authName.value.trim();
            const data = await api('/api/auth/' + (authMode === 'login' ? 'login' : 'register'), {
                method: 'POST', body: JSON.stringify(payload),
            });
            localStorage.setItem(TOKEN_KEY, data.access_token);
            els.authPassword.value = '';
            await enterApp();
        } catch (err) {
            setError(els.authError, err.message);
        } finally {
            els.authSubmit.disabled = false;
        }
    });

    // ---------- Plans and the upgrade moment ----------
    let plansCache = null;

    async function loadPlans() {
        if (!plansCache) plansCache = await api('/api/plans');
        return plansCache;
    }

    function renderPlanCards(container, plans) {
        container.replaceChildren();
        plans.forEach((p) => {
            const card = document.createElement('div');
            card.className = 'plan-card' + (p.highlight ? ' highlight' : '');
            if (p.is_current) {
                const b = document.createElement('span');
                b.className = 'plan-badge current';
                b.textContent = 'Your plan';
                card.appendChild(b);
            } else if (!p.purchasable) {
                const b = document.createElement('span');
                b.className = 'plan-badge';
                b.textContent = 'Opening soon';
                card.appendChild(b);
            }
            const name = document.createElement('h3');
            name.className = 'plan-name';
            name.textContent = p.name;
            const tagline = document.createElement('p');
            tagline.className = 'plan-tagline';
            tagline.textContent = p.tagline;
            const price = document.createElement('div');
            price.className = 'plan-price';
            price.textContent = p.monthly_price_usd === 0 ? 'Free' : '$' + p.monthly_price_usd.toFixed(2);
            if (p.monthly_price_usd > 0) {
                const per = document.createElement('span');
                per.className = 'per';
                per.textContent = ' / month';
                price.appendChild(per);
            }
            const npr = document.createElement('div');
            npr.className = 'plan-price-npr';
            npr.textContent = p.monthly_price_npr > 0 ? 'about NPR ' + p.monthly_price_npr + ' a month' : 'No card needed';
            const list = document.createElement('ul');
            list.className = 'plan-features';
            p.features.forEach((f) => {
                const li = document.createElement('li');
                li.textContent = f;
                list.appendChild(li);
            });
            card.append(name, tagline, price, npr, list);

            const cta = document.createElement('button');
            cta.className = 'btn ' + (p.highlight ? 'btn-primary' : 'btn-secondary');
            if (p.is_current) {
                cta.textContent = 'Current plan';
                cta.disabled = true;
            } else if (p.purchasable && p.monthly_price_usd > 0) {
                // Only reachable once billing is configured server-side: the API
                // derives `purchasable` from that, so this branch stays dark
                // until Stripe credentials exist.
                cta.textContent = 'Upgrade to ' + p.name;
                cta.addEventListener('click', () => startCheckout(p, cta, 'pricing_page'));
            } else if (p.purchasable) {
                cta.textContent = 'Start free';
                cta.addEventListener('click', () => (token() ? show(els.createView) : goToAuth('register')));
            } else {
                cta.textContent = 'Tell me when it opens';
                cta.addEventListener('click', async () => {
                    if (!token()) return goToAuth('register');
                    cta.disabled = true;
                    try {
                        const r = await api('/api/plans/interest', {
                            method: 'POST',
                            body: JSON.stringify({ plan_code: p.code, source: 'pricing_page' }),
                        });
                        toast(r.message, 'success');
                    } catch (err) {
                        toast(err.message, 'error');
                        cta.disabled = false;
                    }
                });
            }
            card.appendChild(cta);
            container.appendChild(card);
        });
    }

    /** Send the browser to hosted checkout. A top-level navigation, not a form
     *  post, so the page's CSP needs no Stripe origins at all. */
    async function startCheckout(plan, button, source) {
        if (!token()) return goToAuth('register');
        const label = button.textContent;
        button.disabled = true;
        button.textContent = 'Opening checkout...';
        try {
            const r = await api('/api/billing/checkout', {
                method: 'POST',
                body: JSON.stringify({ plan_code: plan.code, source: source }),
            });
            window.location.assign(r.checkout_url);
        } catch (err) {
            toast(err.message, 'error');
            button.disabled = false;
            button.textContent = label;
        }
    }

    /** Confirm on return from checkout rather than waiting for the webhook.
     *  Webhooks are asynchronous and can be misconfigured entirely, so this is
     *  what guarantees a paying customer is upgraded when they get back. */
    async function confirmCheckoutReturn(params) {
        const status = params.get('status');
        const sessionId = params.get('session_id');
        window.history.replaceState({}, '', '/');
        if (status === 'cancelled') return toast('Checkout cancelled. Nothing was charged.');
        if (status !== 'success' || !sessionId) return;
        try {
            const r = await api('/api/billing/checkout/' + encodeURIComponent(sessionId) + '/confirm', {
                method: 'POST',
            });
            plansCache = null;  // purchasability and "Your plan" both just changed
            await refreshUsage();
            toast(r.plan_status === 'pending'
                ? 'Payment received. Your plan will update in a moment.'
                : 'Welcome to ' + r.plan + '. Enjoy the stories.', 'success');
        } catch (_) {
            toast('We could not confirm your payment yet. It may take a moment to appear.', 'error');
        }
    }

    /** The daily wall is the highest-intent moment in the product. Answer it with
     *  an offer, not a red error line. */
    async function showUpgradeMoment(detail) {
        let plus = null;
        try {
            plus = (await loadPlans()).find((p) => p.code === 'plus');
        } catch (_) { /* still show the message below */ }

        els.upgradeBody.textContent =
            detail || "You've used today's free stories. Your allowance resets tomorrow morning.";
        els.upgradePlan.replaceChildren();
        els.upgradeResult.classList.add('hidden');
        els.upgradeInterest.classList.toggle('hidden', !plus);
        els.upgradeInterest.disabled = false;

        if (plus) {
            const h = document.createElement('h3');
            h.textContent = plus.name + ' is opening soon';
            const ul = document.createElement('ul');
            plus.features.slice(0, 4).forEach((f) => {
                const li = document.createElement('li');
                li.textContent = f;
                ul.appendChild(li);
            });
            const price = document.createElement('p');
            price.className = 'muted small';
            price.style.margin = '0.6rem 0 0';
            price.textContent = 'Planned at $' + plus.monthly_price_usd.toFixed(2)
                + ' a month (about NPR ' + plus.monthly_price_npr + ').';
            els.upgradePlan.append(h, ul, price);
        }

        const previouslyFocused = document.activeElement;
        els.upgradeBackdrop.classList.remove('hidden');
        els.upgradeDismiss.focus();

        function close() {
            els.upgradeBackdrop.classList.add('hidden');
            document.removeEventListener('keydown', onKey);
            els.upgradeBackdrop.removeEventListener('mousedown', onBackdrop);
            if (previouslyFocused && previouslyFocused.focus) previouslyFocused.focus();
        }
        function onKey(e) {
            if (e.key === 'Escape') { close(); return; }
            if (e.key !== 'Tab') return;
            const focusables = [els.upgradeDismiss, els.upgradeInterest].filter((b) => !b.classList.contains('hidden'));
            const first = focusables[0], last = focusables[focusables.length - 1];
            if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
            else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
        }
        function onBackdrop(e) { if (e.target === els.upgradeBackdrop) close(); }
        document.addEventListener('keydown', onKey);
        els.upgradeBackdrop.addEventListener('mousedown', onBackdrop);
        els.upgradeDismiss.onclick = close;
        els.upgradeInterest.onclick = async () => {
            els.upgradeInterest.disabled = true;
            try {
                const r = await api('/api/plans/interest', {
                    method: 'POST',
                    body: JSON.stringify({ plan_code: 'plus', source: 'quota_wall' }),
                });
                els.upgradeResult.textContent = r.message;
                els.upgradeResult.classList.remove('hidden');
                els.upgradeInterest.classList.add('hidden');
            } catch (err) {
                toast(err.message, 'error');
                els.upgradeInterest.disabled = false;
            }
        };
    }

    // ---------- Password recovery ----------
    els.forgotLink.addEventListener('click', () => {
        els.forgotEmail.value = els.authEmail.value.trim();
        setError(els.forgotError, '');
        els.forgotMessage.classList.add('hidden');
        show(els.forgotView);
        els.forgotEmail.focus();
    });
    els.backToLogin.addEventListener('click', () => { setAuthMode('login'); show(els.authView); });

    els.forgotForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        setError(els.forgotError, '');
        els.forgotSubmit.disabled = true;
        try {
            const resp = await api('/api/auth/forgot-password', {
                method: 'POST', body: JSON.stringify({ email: els.forgotEmail.value.trim() }),
            });
            els.forgotMessage.textContent = resp.message;
            els.forgotMessage.classList.remove('hidden');
        } catch (err) {
            setError(els.forgotError, err.message);
        } finally {
            els.forgotSubmit.disabled = false;
        }
    });

    els.resetForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        setError(els.resetError, '');
        els.resetSubmit.disabled = true;
        try {
            const params = new URLSearchParams(window.location.search);
            const data = await api('/api/auth/reset-password', {
                method: 'POST',
                body: JSON.stringify({ token: params.get('token') || '', password: els.resetPassword.value }),
            });
            localStorage.setItem(TOKEN_KEY, data.access_token);
            window.history.replaceState({}, '', '/');  // drop the token from the URL
            await enterApp();
        } catch (err) {
            setError(els.resetError, err.message);
        } finally {
            els.resetSubmit.disabled = false;
        }
    });

    // ---------- Create + progress ----------
    const STAGE_LABELS = {
        queued: 'Waiting in the queue…',
        writing_story: 'Writing your story…',
        illustrating: 'Painting the illustrations…',
        finalizing: 'Binding the book…',
        done: 'Done!',
        failed: 'Something went wrong',
    };

    els.storyPrompt.addEventListener('input', () => {
        els.promptCount.textContent = els.storyPrompt.value.length + ' / 500';
    });

    els.sampleBtn.addEventListener('click', () => {
        els.storyPrompt.value = 'Leo the Lion and Lily the Lost Girl find their way home through the Himalayan foothills';
        els.storyPrompt.dispatchEvent(new Event('input'));
    });

    els.generateBtn.addEventListener('click', async () => {
        const prompt = els.storyPrompt.value.trim();
        setError(els.createError, '');
        if (prompt.length < 3) return setError(els.createError, 'Please enter a story idea (at least a few words).');
        els.generateBtn.disabled = true;
        try {
            const resp = await api('/api/stories', {
                method: 'POST',
                body: JSON.stringify({
                    prompt: prompt,
                    language: els.storyLanguage.value,
                    hero_name: els.heroName.value.trim(),
                }),
            });
            startPolling(resp.job_id, resp.story_id);
        } catch (err) {
            els.generateBtn.disabled = false;
            if (err.quotaExhausted) {
                refreshUsage().catch(() => {});
                await showUpgradeMoment(err.message);
                return;
            }
            setError(els.createError, err.message);
        }
    });

    function stopPolling() {
        pollGen += 1;
        if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
        els.progressPanel.classList.add('hidden');
        els.generateBtn.disabled = false;
    }

    function startPolling(jobId, storyId) {
        pollGen += 1;
        const myGen = pollGen;
        let revealed = false;   // has the story text been shown yet
        els.progressPanel.classList.remove('hidden');
        els.progressBar.style.width = '4%';
        els.progressStage.textContent = STAGE_LABELS.queued;
        els.progressDetail.textContent = '';

        const poll = async () => {
            let job;
            try {
                job = await api('/api/jobs/' + jobId);
            } catch (err) {
                if (myGen !== pollGen) return; // superseded by logout/another run
                stopPolling();
                return setError(els.createError, err.message);
            }
            if (myGen !== pollGen) return;
            els.progressStage.textContent = STAGE_LABELS[job.stage] || job.stage;
            let pct = 4;
            if (job.stage === 'writing_story') {
                pct = 20;
            } else if (job.progress_total > 0) {
                pct = Math.round(30 + (job.progress_current / job.progress_total) * 65);
                els.progressDetail.textContent = 'Illustration ' + Math.min(job.progress_current + 1, job.progress_total) + ' of ' + job.progress_total;
            }
            els.progressBar.style.width = pct + '%';
            els.progressTrack.setAttribute('aria-valuenow', String(pct));

            // As soon as the text exists, show the story and let illustrations
            // stream in. Waiting for every image wastes most of the perceived wait.
            if (!revealed && job.stage === 'illustrating') {
                revealed = true;
                try {
                    await openStory(storyId);
                    if (myGen !== pollGen) return;
                    els.progressPanel.classList.add('hidden');
                } catch (_) { revealed = false; }
            } else if (revealed && job.status !== 'complete') {
                try {
                    const story = await api('/api/stories/' + storyId);
                    if (myGen !== pollGen) return;
                    currentStory = story;
                    patchPageImages(story);
                } catch (_) { /* keep polling; the job status is authoritative */ }
            }

            if (job.status === 'complete') {
                els.progressBar.style.width = '100%';
                stopPolling();
                try {
                    await Promise.all([refreshUsage(), loadLibrary()]);
                    await openStory(storyId);
                    toast('Your story is ready.', 'success');
                } catch (err) {
                    setError(els.createError, 'Your story was created, but displaying it failed: ' + err.message);
                }
                return;
            }
            if (job.status === 'failed') {
                stopPolling();
                refreshUsage().catch(() => {});
                if (revealed) show(els.createView);
                return setError(els.createError, job.error || 'Story generation failed. Please try again.');
            }
            pollTimer = setTimeout(poll, 1200);
        };
        poll();
    }

    // ---------- Library ----------
    async function loadLibrary() {
        if (libraryTimer) { clearTimeout(libraryTimer); libraryTimer = null; }
        const items = await api('/api/stories');
        els.libraryGrid.replaceChildren();
        els.libraryEmpty.classList.toggle('hidden', items.length > 0);
        // A refresh mid-generation loses the job id; keep the library polling
        // itself until in-flight stories settle (backend fails stale ones).
        if (items.some((s) => s.status === 'pending' || s.status === 'generating')) {
            libraryTimer = setTimeout(() => {
                if (token() && !els.createView.classList.contains('hidden')) {
                    loadLibrary().catch(() => {});
                }
            }, 5000);
        }
        for (const s of items) {
            const card = document.createElement('div');
            card.className = 'story-card';
            const cover = s.cover_image_url
                ? Object.assign(document.createElement('img'), { className: 'cover', src: s.cover_image_url, alt: '' })
                : Object.assign(document.createElement('div'), { className: 'cover-placeholder', textContent: '📖' });
            const meta = document.createElement('div');
            meta.className = 'meta';
            const h3 = document.createElement('h3');
            h3.textContent = s.title || s.prompt.slice(0, 60);
            const p = document.createElement('p');
            p.textContent = new Date(s.created_at).toLocaleDateString();
            const pill = document.createElement('span');
            pill.className = 'status-pill ' + s.status;
            pill.textContent = s.status;
            meta.append(h3, p, pill);
            card.append(cover, meta);
            card.addEventListener('click', () => openStory(s.id).catch((err) => toast(err.message, 'error')));
            card.tabIndex = 0;
            card.setAttribute('role', 'button');
            card.setAttribute('aria-label', 'Open story: ' + (s.title || s.prompt.slice(0, 60)));
            card.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); card.click(); }
            });
            els.libraryGrid.appendChild(card);
        }
    }

    // ---------- Reader ----------
    function renderPages(container, pages, opts) {
        const options = opts || {};
        container.replaceChildren();
        pages.forEach((page, index) => {
            const div = document.createElement('div');
            div.className = 'story-page';
            div.dataset.position = String(page.position);
            if (page.image_url) {
                const img = document.createElement('img');
                img.src = page.image_url;
                // Describe the scene rather than saying "image": the text of the page
                // is what the illustration depicts.
                img.alt = 'Illustration for page ' + (index + 1) + ': ' + page.text.slice(0, 120);
                img.loading = 'lazy';
                div.appendChild(img);
            } else if (page.image_error) {
                const note = document.createElement('p');
                note.className = 'image-note';
                note.textContent = '(Illustration unavailable for this page)';
                div.appendChild(note);
            } else if (options.pendingImages) {
                // Story text is ready but this illustration is still being painted.
                const ph = document.createElement('div');
                ph.className = 'image-placeholder';
                ph.textContent = 'Painting this scene...';
                div.appendChild(ph);
            }
            const text = document.createElement('p');
            text.className = 'text';
            if (options.language === 'ne') text.lang = 'ne';
            text.textContent = page.text;
            div.appendChild(text);
            container.appendChild(div);
        });
    }

    async function openStory(storyId, opts) {
        const options = opts || {};
        // Leaving the shared context: stale shared state here would make
        // Save PDF fetch the previously viewed shared story instead of this one.
        isSharedView = false;
        currentSharedSlug = null;
        const story = await api('/api/stories/' + storyId);
        currentStory = story;
        els.storyTitle.textContent = story.title || 'Untitled Story';
        renderPages(els.storyContent, story.pages, {
            language: story.language,
            pendingImages: story.status === 'generating' || story.status === 'pending',
        });
        if (story.status === 'failed') {
            const note = document.createElement('p');
            note.className = 'error-text';
            note.style.textAlign = 'center';
            note.textContent = story.error || 'This story could not be generated. You can delete it and try again.';
            els.storyContent.prepend(note);
        }
        els.shareBtn.classList.toggle('hidden', story.status !== 'complete');
        els.pdfBtn.classList.toggle('hidden', story.status !== 'complete');
        els.deleteBtn.classList.toggle('hidden', story.status === 'generating' || story.status === 'pending');
        els.shareInfo.classList.add('hidden');
        if (story.share_slug) showShareLink(story.share_slug);
        if (!options.keepView) {
            if (!options.skipHistory) {
                window.history.pushState({ view: 'story', storyId: story.id }, '', '/story/' + story.id);
            }
            show(els.storyView);
        }
        return story;
    }

    /** Fill in illustrations as they finish, without disturbing the reader's scroll. */
    function patchPageImages(story) {
        story.pages.forEach((page, index) => {
            const pageEl = els.storyContent.querySelector('[data-position="' + page.position + '"]');
            if (!pageEl || pageEl.querySelector('img')) return;
            if (page.image_url) {
                const img = document.createElement('img');
                img.src = page.image_url;
                img.alt = 'Illustration for page ' + (index + 1) + ': ' + page.text.slice(0, 120);
                const placeholder = pageEl.querySelector('.image-placeholder');
                if (placeholder) placeholder.replaceWith(img);
                else pageEl.insertBefore(img, pageEl.firstChild);
            } else if (page.image_error) {
                const placeholder = pageEl.querySelector('.image-placeholder');
                if (placeholder) {
                    const note = document.createElement('p');
                    note.className = 'image-note';
                    note.textContent = '(Illustration unavailable for this page)';
                    placeholder.replaceWith(note);
                }
            }
        });
    }

    function showShareLink(slug) {
        const url = window.location.origin + '/shared/' + slug;
        els.shareInfo.textContent = 'Share link: ' + url;
        els.shareInfo.classList.remove('hidden');
        if (navigator.clipboard) {
            navigator.clipboard.writeText(url).then(() => {
                els.shareInfo.textContent = 'Share link (copied to clipboard): ' + url;
            }).catch(() => {});
        }
    }

    els.shareBtn.addEventListener('click', async () => {
        if (!currentStory) return;
        try {
            const resp = await api('/api/stories/' + currentStory.id + '/share', { method: 'POST' });
            showShareLink(resp.share_slug);
        } catch (err) { toast(err.message, 'error'); }
    });

    els.deleteBtn.addEventListener('click', async () => {
        if (!currentStory) return;
        const ok = await confirmDialog({
            title: 'Delete this story?',
            body: '"' + (currentStory.title || 'Untitled Story') + '" and its illustrations will be permanently removed. This cannot be undone.',
            okLabel: 'Delete forever',
        });
        if (!ok) return;
        try {
            await api('/api/stories/' + currentStory.id, { method: 'DELETE' });
            currentStory = null;
            await loadLibrary();
            show(els.createView);
            toast('Story deleted.');
        } catch (err) { toast(err.message, 'error'); }
    });

    els.backBtn.addEventListener('click', () => {
        if (isSharedView) {
            // Shared pages live at /shared/{slug}; go home so the URL and the
            // header nav are consistent for both visitors and logged-in users.
            window.location.href = '/';
            return;
        }
        window.history.back();
    });

    // Browser Back/Forward must move between views, not leave the app.
    window.addEventListener('popstate', async (e) => {
        const state = e.state || {};
        if (state.view === 'story' && state.storyId) {
            try { await openStory(state.storyId, { skipHistory: true }); return; } catch (_) { /* fall through */ }
        }
        if (token()) {
            try { await loadLibrary(); } catch (_) { /* still land somewhere visible */ }
            show(els.createView);
        } else {
            show(els.landingView);
        }
    });

    // ---------- PDF ----------
    // Rendered on the server as a real book (vector text, cover, page
    // numbers, back cover). The old client path screenshotted the DOM into
    // JPEGs and a sizing bug shipped two blank sheets after every page.
    els.pdfBtn.addEventListener('click', async () => {
        if (!currentStory) return;
        const original = els.pdfBtn.textContent;
        els.pdfBtn.disabled = true;
        els.pdfBtn.textContent = 'Making your book...';
        try {
            const path = isSharedView
                ? '/api/stories/shared/' + currentSharedSlug + '/pdf'
                : '/api/stories/' + currentStory.id + '/pdf';
            const headers = {};
            const t = token();
            if (t && !isSharedView) headers['Authorization'] = 'Bearer ' + t;
            const resp = await fetch(path, { headers });
            if (!resp.ok) {
                let detail = 'The book could not be created. Please try again.';
                try { detail = (await resp.json()).detail || detail; } catch (_) { /* keep default */ }
                throw new Error(detail);
            }
            const blob = await resp.blob();
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = (els.storyTitle.textContent.replace(/[^a-z0-9ऀ-ॿ]/gi, '_') || 'story') + '.pdf';
            document.body.appendChild(a);
            a.click();
            a.remove();
            setTimeout(() => URL.revokeObjectURL(url), 4000);
        } catch (err) {
            toast(err.message, 'error');
        } finally {
            els.pdfBtn.disabled = false;
            els.pdfBtn.textContent = original;
        }
    });

    // ---------- Account deletion ----------
    // The legal deletion path: password re-entry, an explicit warning, and a
    // full logout on success. Link only appears while logged in.
    const delEls = {
        wrap: $('deleteAccountWrap'), link: $('deleteAccountLink'), backdrop: $('deleteBackdrop'),
        form: $('deleteForm'), password: $('deletePassword'), error: $('deleteError'),
        cancel: $('deleteCancel'), confirm: $('deleteConfirm'),
    };

    let deleteInFlight = false;
    let deletePreviousFocus = null;

    function closeDeleteModal() {
        // Never dismissable mid-request: the DELETE is irreversible and a late
        // error must not land in a modal that is no longer on screen.
        if (deleteInFlight) return;
        delEls.backdrop.classList.add('hidden');
        delEls.password.value = '';
        setError(delEls.error, '');
        if (deletePreviousFocus && deletePreviousFocus.focus) deletePreviousFocus.focus();
    }

    delEls.link.addEventListener('click', () => {
        deletePreviousFocus = document.activeElement;
        delEls.password.value = '';
        setError(delEls.error, '');
        delEls.backdrop.classList.remove('hidden');
        delEls.password.focus();
    });
    delEls.cancel.addEventListener('click', closeDeleteModal);
    delEls.backdrop.addEventListener('mousedown', (e) => { if (e.target === delEls.backdrop) closeDeleteModal(); });
    document.addEventListener('keydown', (e) => {
        if (delEls.backdrop.classList.contains('hidden')) return;
        if (e.key === 'Escape') return closeDeleteModal();
        if (e.key !== 'Tab') return;
        // Trap focus, matching confirmDialog: a modal that leaks focus to the
        // page behind it is unusable by keyboard and screen-reader users.
        const focusables = [delEls.password, delEls.cancel, delEls.confirm];
        const first = focusables[0], last = focusables[focusables.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    });

    delEls.form.addEventListener('submit', async (e) => {
        e.preventDefault();
        setError(delEls.error, '');
        deleteInFlight = true;
        delEls.confirm.disabled = true;
        delEls.cancel.disabled = true;
        const label = delEls.confirm.textContent;
        delEls.confirm.textContent = 'Deleting...';
        try {
            const r = await api('/api/auth/me', {
                method: 'DELETE',
                body: JSON.stringify({ password: delEls.password.value }),
            });
            deleteInFlight = false;
            closeDeleteModal();
            logout();
            toast(r.message, 'success');
        } catch (err) {
            deleteInFlight = false;
            setError(delEls.error, err.message);
        } finally {
            deleteInFlight = false;
            delEls.confirm.disabled = false;
            delEls.cancel.disabled = false;
            delEls.confirm.textContent = label;
        }
    });

    // ---------- Nav ----------
    function goToAuth(mode) {
        setAuthMode(mode);
        show(els.authView);
        els.authEmail.focus();
    }
    els.navLogin.addEventListener('click', () => goToAuth('login'));
    els.navSignup.addEventListener('click', () => goToAuth('register'));
    els.heroSignup.addEventListener('click', () => goToAuth('register'));
    els.footerSignup.addEventListener('click', () => goToAuth('register'));

    els.navLibrary.addEventListener('click', async () => {
        try { await loadLibrary(); } catch (err) { setError(els.createError, err.message); }
        show(els.createView);
    });
    els.navLogout.addEventListener('click', logout);

    // ---------- Boot ----------
    async function loadSharedStory(slug) {
        isSharedView = true;
        currentSharedSlug = slug;
        try {
            const story = await api('/api/stories/shared/' + slug);
            currentStory = story;
            els.storyTitle.textContent = story.title || 'Untitled Story';
            renderPages(els.storyContent, story.pages, { language: story.language });
            els.shareBtn.classList.add('hidden');
            els.deleteBtn.classList.add('hidden');
            els.pdfBtn.classList.remove('hidden');
            els.shareInfo.classList.add('hidden');
            show(els.storyView);
            // Shared stories are the growth loop: invite logged-out readers in.
            if (!token()) els.guestNav.classList.remove('hidden');
        } catch (err) {
            els.storyTitle.textContent = 'Story not found';
            els.storyContent.replaceChildren();
            els.shareBtn.classList.add('hidden');
            els.deleteBtn.classList.add('hidden');
            els.pdfBtn.classList.add('hidden');
            show(els.storyView);
        }
    }

    async function enterApp() {
        try {
            await api('/api/auth/me');
        } catch (_) {
            return logout();
        }
        els.userNav.classList.remove('hidden');
        document.getElementById('deleteAccountWrap').classList.remove('hidden');
        setError(els.createError, '');
        try {
            await Promise.all([refreshUsage(), loadLibrary()]);
        } catch (err) {
            setError(els.createError, 'Could not load your stories: ' + err.message);
        }
        show(els.createView); // always land somewhere visible
    }

    // Pricing renders on the landing page for everyone, signed in or not.
    loadPlans()
        .then((p) => renderPlanCards(els.landingPlans, p))
        .catch(() => { els.landingPlans.replaceChildren(); });

    const sharedMatch = window.location.pathname.match(/^\/shared\/([a-z0-9]+)$/i);
    const storyMatch = window.location.pathname.match(/^\/story\/([a-z0-9]+)$/i);
    if (window.location.pathname === '/billing/return') {
        const params = new URLSearchParams(window.location.search);
        if (token()) {
            enterApp().then(() => confirmCheckoutReturn(params)).catch(() => show(els.landingView));
        } else {
            goToAuth('login');
        }
    } else if (window.location.pathname === '/reset-password') {
        show(els.resetView);
        els.resetPassword.focus();
    } else if (sharedMatch) {
        loadSharedStory(sharedMatch[1]);
    } else if (storyMatch && token()) {
        // Deep link into one of the user's own stories (refresh or bookmark).
        enterApp()
            .then(() => openStory(storyMatch[1], { skipHistory: true }))
            .catch(() => { window.history.replaceState({}, '', '/'); });
    } else if (token()) {
        enterApp();
    } else {
        show(els.landingView);
    }
})();
