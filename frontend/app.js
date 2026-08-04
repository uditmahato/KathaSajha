/* KathaSajha frontend — vanilla JS SPA over the FastAPI backend. */
(function () {
    'use strict';

    const TOKEN_KEY = 'kathasajha_token';
    const $ = (id) => document.getElementById(id);

    // i18n.js is loaded immediately before this file, so KS_I18N normally
    // exists. The fallback is not defensive clutter: without it, a 5xx or a
    // truncated response on /assets/i18n.js throws on the FIRST statement of
    // this IIFE, so no handler is ever bound and an English visitor gets a
    // blank page instead of the fully working English app they had before this
    // feature existed.
    //
    // What it recovers, precisely: every static string still renders, because
    // the English is in the markup and the applier simply never runs. The whole
    // landing page, the auth form and the legal pages are intact. What stays
    // broken is text this file BUILDS - plan cards, the usage badge, stage
    // labels, toasts - because their English lives in the file that failed to
    // load. That is a degraded app rather than no app, and it is the most that
    // can be recovered without duplicating the runtime catalogue here, which is
    // the duplication this whole design exists to avoid.
    const i18n = window.KS_I18N || {
        locale: 'en',
        supported: ['en'],
        t: () => '',
        tOrNull: () => null,
        apply: () => {},
        setLocale: () => {},
        beforeSwitch: null,
        ready: Promise.resolve(),
    };
    if (!window.KS_I18N && window.console) {
        console.error('KathaSajha: /assets/i18n.js did not load; running in degraded English mode.');
    }
    const t = (key, params) => i18n.t(key, params);
    // Numbers stay Latin in both locales. Prices ($6.00, NPR 799) and quota
    // counts cannot be Devanagari without a lot more work, and a card showing
    // ३ अगस्ट next to 10 stories is worse than one that is consistently Latin.
    // `undefined` on the English path, NOT 'en': passing a locale here would
    // pin every English reader to en-US, so an en-GB parent's 4 August story
    // would start reading as 8 April. Only Nepali needs an override at all.
    const DATE_LOCALE = i18n.locale === 'ne' ? 'ne-NP-u-nu-latn' : undefined;

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
            els.confirmTitle.textContent = opts.title || t('confirm.default_title');
            els.confirmBody.textContent = opts.body || '';
            els.confirmOk.textContent = opts.okLabel || t('confirm.default_ok');
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

    /** Turn an error body into a sentence for this parent.
     *
     *  The server sends `detail` (English prose, always) alongside an optional
     *  stable `code` and `params`. In English the server's own prose wins —
     *  duplicating it here would be two sources of truth for one sentence. In
     *  another locale a `srv.<code>` translation wins when one exists, and the
     *  English prose is the fallback when it does not. So an untranslated error
     *  degrades to English rather than to nothing.
     *
     *  Shared by api() and the raw PDF fetch, which bypasses api() entirely and
     *  would otherwise stay English after everything else was translated.
     */
    function errorMessage(body, status) {
        if (body && typeof body.code === 'string' && body.code) {
            const local = i18n.tOrNull('srv.' + body.code, body.params || {});
            if (local) return local;
        }
        const detail = body && body.detail;
        if (typeof detail === 'string' && detail) return detail;
        // Pydantic's own 422 text is framework English we do not own and cannot
        // translate. In English it is still the most useful thing available —
        // "Name may only contain letters, spaces, hyphens and apostrophes" tells
        // a parent exactly what to change, and replacing it with a generic would
        // be a straight regression for the readers who can read it. Only a
        // non-English reader gets the generic, because untranslated English is
        // worth less to them than a sentence they can read.
        if (Array.isArray(detail) && detail[0]) {
            if (i18n.locale === 'en' && detail[0].msg) return detail[0].msg;
            return t('err.check_details');
        }
        return t('err.request_failed', { status: status });
    }

    /** Failure text for a story or job row.
     *
     *  These are English sentences frozen into Postgres at the moment a
     *  generation failed, sometimes by the worker in another process. `error`
     *  is still written for every failure and stays the fallback, so rows
     *  predating error codes — and rows written by a worker that has not been
     *  redeployed yet — keep rendering exactly as they always did.
     */
    function storedError(row, fallbackKey) {
        if (row && typeof row.error_code === 'string' && row.error_code) {
            const local = i18n.tOrNull('srv.' + row.error_code, {});
            if (local) return local;
        }
        return (row && row.error) || t(fallbackKey);
    }

    async function api(path, options = {}) {
        const headers = Object.assign({ 'Content-Type': 'application/json' }, options.headers || {});
        // NOT `t`: that is the module-level translator, and shadowing it here
        // turned the expired-session path into "t is not a function".
        const jwt = token();
        if (jwt) headers['Authorization'] = 'Bearer ' + jwt;
        const resp = await fetch(path, Object.assign({}, options, { headers }));
        if (resp.status === 401 && !path.startsWith('/api/auth/')) {
            logout();
            throw new Error(t('err.session_expired'));
        }
        if (resp.status === 204) return null;
        let body = null;
        try { body = await resp.json(); } catch (_) { /* non-JSON */ }
        if (!resp.ok) {
            const err = new Error(errorMessage(body, resp.status));
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
        document.getElementById('familyWrap').classList.add('hidden');
        // Clear the DOM too: on a shared computer the next person must not see
        // the previous family's children's names still on screen.
        savedChildren = [];
        famEls.options.replaceChildren();
        famEls.list.replaceChildren();
        famEls.picker.classList.add('hidden');
        famEls.backdrop.classList.add('hidden');
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
                ? t('usage.left_today', { count: u.remaining_today })
                : t('usage.left_month', { count: u.remaining_this_month, limit: u.monthly_limit });
        } catch (_) { els.usageBadge.textContent = ''; }
    }

    // ---------- Auth ----------
    let authMode = 'login';

    function setAuthMode(mode) {
        authMode = mode;
        els.tabLogin.classList.toggle('active', mode === 'login');
        els.tabRegister.classList.toggle('active', mode === 'register');
        els.nameField.classList.toggle('hidden', mode === 'login');
        els.authSubmit.textContent = t(mode === 'login' ? 'auth.submit.login' : 'auth.submit.register');
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
                b.textContent = t('plan.badge.current');
                card.appendChild(b);
            } else if (!p.purchasable) {
                const b = document.createElement('span');
                b.className = 'plan-badge';
                b.textContent = t('plan.badge.soon');
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
            price.textContent = p.monthly_price_usd === 0 ? t('plan.price.free') : '$' + p.monthly_price_usd.toFixed(2);
            if (p.monthly_price_usd > 0) {
                const per = document.createElement('span');
                per.className = 'per';
                per.textContent = t('plan.price.per_month');
                price.appendChild(per);
            }
            const npr = document.createElement('div');
            npr.className = 'plan-price-npr';
            npr.textContent = p.monthly_price_npr > 0
                ? t('plan.price.npr', { amount: p.monthly_price_npr })
                : t('plan.price.no_card');
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
                cta.textContent = t('plan.cta.current');
                cta.disabled = true;
            } else if (p.purchasable && p.monthly_price_usd > 0) {
                // Only reachable once billing is configured server-side: the API
                // derives `purchasable` from that, so this branch stays dark
                // until Stripe credentials exist.
                cta.textContent = t('plan.cta.upgrade', { plan: p.name });
                cta.addEventListener('click', () => startCheckout(p, cta, 'pricing_page'));
            } else if (p.purchasable) {
                cta.textContent = t('plan.cta.start_free');
                cta.addEventListener('click', () => (token() ? show(els.createView) : goToAuth('register')));
            } else {
                cta.textContent = t('plan.cta.notify');
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
        button.textContent = t('checkout.opening');
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
        if (status === 'cancelled') return toast(t('checkout.cancelled'));
        if (status !== 'success' || !sessionId) return;
        try {
            const r = await api('/api/billing/checkout/' + encodeURIComponent(sessionId) + '/confirm', {
                method: 'POST',
            });
            plansCache = null;  // purchasability and "Your plan" both just changed
            await refreshUsage();
            toast(r.plan_status === 'pending'
                ? t('checkout.pending')
                : t('checkout.welcome', { plan: r.plan }), 'success');
        } catch (_) {
            toast(t('checkout.unconfirmed'), 'error');
        }
    }

    /** The daily wall is the highest-intent moment in the product. Answer it with
     *  an offer, not a red error line. */
    async function showUpgradeMoment(detail) {
        let plus = null;
        try {
            plus = (await loadPlans()).find((p) => p.code === 'plus');
        } catch (_) { /* still show the message below */ }

        els.upgradeBody.textContent = detail || t('upgrade.default_body');
        els.upgradePlan.replaceChildren();
        els.upgradeResult.classList.add('hidden');
        els.upgradeInterest.classList.toggle('hidden', !plus);
        els.upgradeInterest.disabled = false;

        if (plus) {
            const h = document.createElement('h3');
            h.textContent = t('upgrade.plan_soon', { plan: plus.name });
            const ul = document.createElement('ul');
            plus.features.slice(0, 4).forEach((f) => {
                const li = document.createElement('li');
                li.textContent = f;
                ul.appendChild(li);
            });
            const price = document.createElement('p');
            price.className = 'muted small';
            price.style.margin = '0.6rem 0 0';
            price.textContent = t('upgrade.planned_price', {
                usd: plus.monthly_price_usd.toFixed(2), npr: plus.monthly_price_npr,
            });
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
    const PROMPT_MAX = 500;
    const stageLabel = (stage) => t('stage.' + stage) || stage;

    els.storyPrompt.addEventListener('input', () => {
        els.promptCount.textContent = t('create.count', {
            count: els.storyPrompt.value.length, max: PROMPT_MAX,
        });
    });

    els.sampleBtn.addEventListener('click', () => {
        // The example is copy, not data: a Nepali interface should suggest an
        // idea in Nepali, or the one button that shows what good input looks
        // like shows it in the wrong language.
        els.storyPrompt.value = t('create.sample_prompt');
        els.storyPrompt.dispatchEvent(new Event('input'));
    });

    els.generateBtn.addEventListener('click', async () => {
        const prompt = els.storyPrompt.value.trim();
        setError(els.createError, '');
        if (prompt.length < 3) return setError(els.createError, t('create.err.too_short'));
        els.generateBtn.disabled = true;
        try {
            const resp = await api('/api/stories', {
                method: 'POST',
                body: JSON.stringify({
                    prompt: prompt,
                    language: els.storyLanguage.value,
                    hero_name: els.heroName.value.trim(),
                    // Empty when no children are saved, so the request is
                    // byte-identical to what it was before this feature.
                    child_ids: selectedChildIds(),
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
        els.progressStage.textContent = stageLabel('queued');
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
            els.progressStage.textContent = stageLabel(job.stage);
            let pct = 4;
            if (job.stage === 'writing_story') {
                pct = 20;
            } else if (job.progress_total > 0) {
                pct = Math.round(30 + (job.progress_current / job.progress_total) * 65);
                els.progressDetail.textContent = t('progress.illustration', {
                    n: Math.min(job.progress_current + 1, job.progress_total),
                    total: job.progress_total,
                });
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
                    toast(t('toast.story_ready'), 'success');
                } catch (err) {
                    setError(els.createError, t('create.err.display_failed', { message: err.message }));
                }
                return;
            }
            if (job.status === 'failed') {
                stopPolling();
                refreshUsage().catch(() => {});
                if (revealed) show(els.createView);
                return setError(els.createError, storedError(job, 'err.generation_failed'));
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
            // Explicit locale, explicit numbering system. Left implicit,
            // toLocaleDateString under ne-NP emits Devanagari numerals, which
            // would sit beside Latin prices and quota counts on the same card.
            p.textContent = new Date(s.created_at).toLocaleDateString(DATE_LOCALE);
            const pill = document.createElement('span');
            pill.className = 'status-pill ' + s.status;
            pill.textContent = t('status.' + s.status) || s.status;
            meta.append(h3, p, pill);
            card.append(cover, meta);
            card.addEventListener('click', () => openStory(s.id).catch((err) => toast(err.message, 'error')));
            card.tabIndex = 0;
            card.setAttribute('role', 'button');
            card.setAttribute('aria-label', t('library.open_story', { title: s.title || s.prompt.slice(0, 60) }));
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
                img.alt = t('reader.page_alt', { n: index + 1, text: page.text.slice(0, 120) });
                img.loading = 'lazy';
                div.appendChild(img);
            } else if (page.image_error) {
                const note = document.createElement('p');
                note.className = 'image-note';
                note.textContent = t('reader.image_unavailable');
                div.appendChild(note);
            } else if (options.pendingImages) {
                // Story text is ready but this illustration is still being painted.
                const ph = document.createElement('div');
                ph.className = 'image-placeholder';
                ph.textContent = t('reader.painting');
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
        els.storyTitle.textContent = story.title || t('reader.untitled');
        renderPages(els.storyContent, story.pages, {
            language: story.language,
            pendingImages: story.status === 'generating' || story.status === 'pending',
        });
        if (story.status === 'failed') {
            const note = document.createElement('p');
            note.className = 'error-text';
            note.style.textAlign = 'center';
            note.textContent = storedError(story, 'reader.story_failed');
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
                img.alt = t('reader.page_alt', { n: index + 1, text: page.text.slice(0, 120) });
                const placeholder = pageEl.querySelector('.image-placeholder');
                if (placeholder) placeholder.replaceWith(img);
                else pageEl.insertBefore(img, pageEl.firstChild);
            } else if (page.image_error) {
                const placeholder = pageEl.querySelector('.image-placeholder');
                if (placeholder) {
                    const note = document.createElement('p');
                    note.className = 'image-note';
                    note.textContent = t('reader.image_unavailable');
                    placeholder.replaceWith(note);
                }
            }
        });
    }

    function showShareLink(slug) {
        const url = window.location.origin + '/shared/' + slug;
        els.shareInfo.textContent = t('share.link', { url: url });
        els.shareInfo.classList.remove('hidden');
        if (navigator.clipboard) {
            navigator.clipboard.writeText(url).then(() => {
                els.shareInfo.textContent = t('share.link_copied', { url: url });
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
            title: t('delete_story.title'),
            body: t('delete_story.body', { title: currentStory.title || t('reader.untitled') }),
            okLabel: t('delete_story.ok'),
        });
        if (!ok) return;
        try {
            await api('/api/stories/' + currentStory.id, { method: 'DELETE' });
            currentStory = null;
            await loadLibrary();
            show(els.createView);
            toast(t('toast.story_deleted'));
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
        els.pdfBtn.textContent = t('pdf.making');
        try {
            const path = isSharedView
                ? '/api/stories/shared/' + currentSharedSlug + '/pdf'
                : '/api/stories/' + currentStory.id + '/pdf';
            const headers = {};
            const jwt = token();   // not `t` — see api(); it shadows the translator
            if (jwt && !isSharedView) headers['Authorization'] = 'Bearer ' + jwt;
            const resp = await fetch(path, { headers });
            if (!resp.ok) {
                // This call deliberately bypasses api() (it wants a blob, not
                // JSON), so it has to reach for the same error decoder or its
                // 409 and 503 would be the last two English strings left.
                let body = null;
                try { body = await resp.json(); } catch (_) { /* non-JSON */ }
                throw new Error(body ? errorMessage(body, resp.status) : t('pdf.failed'));
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

    // ---------- Saved children and characters ----------
    // The retention feature: once the app knows a family, no story starts from
    // an empty box. A parent with no saved children sees the form unchanged.
    const MAX_CHILDREN_PER_STORY = 3;
    let savedChildren = [];
    const famEls = {
        wrap: $('familyWrap'), link: $('familyLink'), backdrop: $('familyBackdrop'),
        list: $('familyList'), form: $('familyForm'), name: $('familyName'),
        band: $('familyBand'), error: $('familyError'), close: $('familyClose'),
        picker: $('castPicker'), options: $('castOptions'), hint: $('castHint'),
    };

    function selectedChildIds() {
        return [...famEls.options.querySelectorAll('input:checked')].map((i) => i.value);
    }

    function renderCastPicker() {
        // Keep whatever the parent had ticked: adding a sibling mid-flow must
        // not silently clear the story they were about to make.
        const keep = new Set(selectedChildIds());
        famEls.picker.classList.toggle('hidden', savedChildren.length === 0);
        famEls.options.replaceChildren();
        savedChildren.forEach((child) => {
            const label = document.createElement('label');
            label.className = 'cast-option';
            const box = document.createElement('input');
            box.type = 'checkbox';
            box.value = child.id;
            box.checked = keep.has(child.id);
            box.addEventListener('change', enforceCastCap);
            const text = document.createElement('span');
            text.textContent = child.name;
            label.append(box, text);
            famEls.options.appendChild(label);
        });
        enforceCastCap();
    }

    function enforceCastCap() {
        // A story with more than three named children becomes a roll call, and
        // someone always ends up as scenery. Say why rather than just disabling.
        const chosen = selectedChildIds().length;
        famEls.options.querySelectorAll('input').forEach((box) => {
            box.disabled = !box.checked && chosen >= MAX_CHILDREN_PER_STORY;
        });
        famEls.hint.textContent = chosen >= MAX_CHILDREN_PER_STORY
            ? t('cast.hint_cap', { max: MAX_CHILDREN_PER_STORY })
            : '';
    }

    function renderFamilyList() {
        famEls.list.replaceChildren();
        savedChildren.forEach((child) => {
            const li = document.createElement('li');
            const name = document.createElement('span');
            name.textContent = child.name;
            const remove = document.createElement('button');
            remove.className = 'link-btn';
            remove.textContent = t('family.remove');
            remove.addEventListener('click', async () => {
                try {
                    await api('/api/profiles/children/' + child.id, { method: 'DELETE' });
                    await loadFamily();
                    // Stories already made keep their names: they were snapshotted.
                    toast(t('family.removed_toast', { name: child.name }));
                } catch (err) { toast(err.message, 'error'); }
            });
            li.append(name, remove);
            famEls.list.appendChild(li);
        });
        if (!savedChildren.length) {
            const empty = document.createElement('li');
            empty.className = 'muted small';
            empty.textContent = t('family.empty');
            famEls.list.appendChild(empty);
        }
    }

    async function loadFamily() {
        try {
            savedChildren = await api('/api/profiles/children');
        } catch (_) {
            savedChildren = [];
        }
        renderFamilyList();
        renderCastPicker();
    }

    famEls.link.addEventListener('click', () => {
        famEls.backdrop.classList.remove('hidden');
        famEls.name.focus();
    });
    famEls.close.addEventListener('click', () => famEls.backdrop.classList.add('hidden'));
    famEls.backdrop.addEventListener('mousedown', (e) => {
        if (e.target === famEls.backdrop) famEls.backdrop.classList.add('hidden');
    });
    famEls.form.addEventListener('submit', async (e) => {
        e.preventDefault();
        setError(famEls.error, '');
        try {
            await api('/api/profiles/children', {
                method: 'POST',
                body: JSON.stringify({ name: famEls.name.value, age_band: famEls.band.value }),
            });
            famEls.name.value = '';
            famEls.band.value = '';
            await loadFamily();
        } catch (err) {
            setError(famEls.error, err.message);
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
        delEls.confirm.textContent = t('delete_account.deleting');
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
            els.storyTitle.textContent = story.title || t('reader.untitled');
            renderPages(els.storyContent, story.pages, { language: story.language });
            els.shareBtn.classList.add('hidden');
            els.deleteBtn.classList.add('hidden');
            els.pdfBtn.classList.remove('hidden');
            els.shareInfo.classList.add('hidden');
            show(els.storyView);
            // Shared stories are the growth loop: invite logged-out readers in.
            if (!token()) els.guestNav.classList.remove('hidden');
        } catch (err) {
            els.storyTitle.textContent = t('reader.not_found');
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
        document.getElementById('familyWrap').classList.remove('hidden');
        await loadFamily();
        setError(els.createError, '');
        try {
            await Promise.all([refreshUsage(), loadLibrary()]);
        } catch (err) {
            setError(els.createError, t('err.library_load', { message: err.message }));
        }
        show(els.createView); // always land somewhere visible
    }

    // Switching locale reloads the page, which kills the poll loop and the
    // progress panel. The story keeps generating server-side and reappears in
    // the library, but a parent watching the bar should be told that, not shown
    // it vanishing.
    i18n.beforeSwitch = () => {
        if (!pollTimer) return true;
        return confirmDialog({
            title: t('locale.switch_busy.title'),
            body: t('locale.switch_busy.body'),
            okLabel: t('locale.switch_busy.ok'),
        });
    };

    // ---------- Boot ----------
    // Everything below waits on the catalogue. Every <section class="view">
    // ships hidden and only show() reveals one, so gating here is what keeps
    // the landing hero from painting in English and flipping to Nepali. For an
    // English visitor `ready` is already resolved and nothing is fetched.
    // `ready` never rejects: a missing or broken catalogue boots in English.
    i18n.ready.then(() => {
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
    });
})();
