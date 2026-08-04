/* KathaSajha i18n runtime.
 *
 * English is not a catalogue. Every English string stays literally where it
 * always was — in the markup for static text, in EN below for text this app
 * builds at runtime. A non-English locale loads ONE override file and swaps
 * what it can. Anything it cannot swap stays English, which means the failure
 * mode of every bug in this file is "the app is in English", never "the app is
 * broken" and never "the app shows i18n.key.name".
 *
 * Loaded as a classic blocking script immediately before app.js, so the DOM it
 * translates already exists and window.KS_I18N is guaranteed present before
 * app.js line 1. CSP is script-src 'self' with no 'unsafe-inline', so there is
 * no inline bootstrap available to pre-empt the paint: the header and footer
 * are painted in English for one frame before the swap. That is accepted and
 * deliberate. Every <section class="view"> ships hidden and is revealed only by
 * show(), which app.js calls after KS_I18N.ready resolves — so the landing hero,
 * the screen that decides signups, never flashes.
 */
(function () {
    'use strict';

    var SUPPORTED = ['en', 'ne'];
    var STORE_KEY = 'kathasajha_locale';
    // If the catalogue cannot be fetched, boot in English rather than hang.
    var LOAD_TIMEOUT_MS = 3000;

    /* Attributes the applier may write. href/src/on* are absent on purpose: a
     * catalogue is data, and data must never become a navigation target or an
     * event handler. */
    var ATTR_ALLOWLIST = ['placeholder', 'title', 'aria-label', 'alt', 'lang'];

    /* ---------- English runtime catalogue ----------
     * ONLY strings this app constructs in JS. Static markup is not here; it is
     * in index.html, annotated with data-i18n. A key that exists in both places
     * would be two sources of truth for one sentence. */
    var EN = {
        // Errors and generic feedback
        'err.session_expired': 'Session expired. Please log in again.',
        'err.invalid_input': 'Invalid input',
        'err.request_failed': 'Request failed ({status})',
        'err.check_details': 'Please check the details you entered.',
        'err.library_load': 'Could not load your stories: {message}',
        'err.generation_failed': 'Story generation failed. Please try again.',

        // Confirmation dialog defaults
        'confirm.default_title': 'Are you sure?',
        'confirm.default_ok': 'Confirm',

        // Header usage badge
        'usage.left_today': { one: '{count} story left today', other: '{count} stories left today' },
        'usage.left_month': '{count} of {limit} stories left this month',

        // Auth
        'auth.submit.login': 'Log in',
        'auth.submit.register': 'Create account',

        // Plans and checkout
        'plan.badge.current': 'Your plan',
        'plan.badge.soon': 'Opening soon',
        'plan.price.free': 'Free',
        'plan.price.per_month': ' / month',
        'plan.price.npr': 'about NPR {amount} a month',
        'plan.price.no_card': 'No card needed',
        'plan.cta.current': 'Current plan',
        'plan.cta.upgrade': 'Upgrade to {plan}',
        'plan.cta.start_free': 'Start free',
        'plan.cta.notify': 'Tell me when it opens',
        'checkout.opening': 'Opening checkout…',
        'checkout.cancelled': 'Checkout cancelled. Nothing was charged.',
        'checkout.pending': 'Payment received. Your plan will update in a moment.',
        'checkout.welcome': 'Welcome to {plan}. Enjoy the stories.',
        'checkout.unconfirmed': 'We could not confirm your payment yet. It may take a moment to appear.',
        'upgrade.default_body': "You've used today's free stories. Your allowance resets tomorrow morning.",
        'upgrade.plan_soon': '{plan} is opening soon',
        'upgrade.planned_price': 'Planned at ${usd} a month (about NPR {npr}).',

        // Create + progress
        'create.count': '{count} / {max}',
        'create.sample_prompt': 'Leo the Lion and Lily the Lost Girl find their way home through the Himalayan foothills',
        'create.err.too_short': 'Please enter a story idea (at least a few words).',
        'create.err.display_failed': 'Your story was created, but displaying it failed: {message}',
        'stage.queued': 'Waiting in the queue…',
        'stage.writing_story': 'Writing your story…',
        'stage.illustrating': 'Painting the illustrations…',
        'stage.finalizing': 'Binding the book…',
        'stage.done': 'Done!',
        'stage.failed': 'Something went wrong',
        'progress.illustration': 'Illustration {n} of {total}',
        'toast.story_ready': 'Your story is ready.',

        // Library
        'library.open_story': 'Open story: {title}',
        'status.pending': 'pending',
        'status.generating': 'generating',
        'status.complete': 'complete',
        'status.failed': 'failed',

        // Reader
        'reader.untitled': 'Untitled Story',
        'reader.not_found': 'Story not found',
        'reader.image_unavailable': '(Illustration unavailable for this page)',
        'reader.painting': 'Painting this scene…',
        'reader.page_alt': 'Illustration for page {n}: {text}',
        'reader.story_failed': 'This story could not be generated. You can delete it and try again.',
        'share.link': 'Share link: {url}',
        'share.link_copied': 'Share link (copied to clipboard): {url}',
        'delete_story.title': 'Delete this story?',
        'delete_story.body': '“{title}” and its illustrations will be permanently removed. This cannot be undone.',
        'delete_story.ok': 'Delete forever',
        'toast.story_deleted': 'Story deleted.',
        'pdf.making': 'Making your book…',
        'pdf.failed': 'The book could not be created. Please try again.',

        // Saved children
        'cast.hint_cap': 'Stories work best with up to {max} heroes — pick who is in this one.',
        'family.remove': 'Remove',
        'family.removed_toast': '{name} removed. Stories already made are unchanged.',
        'family.empty': 'No one saved yet.',
        'delete_account.deleting': 'Deleting…',

        // Switching locale mid-generation
        'locale.switch_busy.title': 'Change language now?',
        'locale.switch_busy.body': 'Your story is still being created. It will keep going and will be waiting in My Stories, but the progress bar will disappear.',
        'locale.switch_busy.ok': 'Change language',
    };

    /* ---------- Locale resolution ----------
     * Explicit choice wins forever. navigator.language only seeds the first
     * visit, and only on an exact match: a Hindi or en-IN speaker served Nepali
     * is a worse outcome than one served English, and loose prefix matching is
     * the usual way that happens. */
    function isSupported(value) {
        return typeof value === 'string' && SUPPORTED.indexOf(value) !== -1;
    }

    function stored() {
        try {
            return localStorage.getItem(STORE_KEY);
        } catch (_) {
            return null;   // private mode / storage disabled
        }
    }

    function fromNavigator() {
        var tags = (navigator.languages && navigator.languages.length)
            ? navigator.languages
            : [navigator.language || ''];
        for (var i = 0; i < tags.length; i++) {
            var tag = String(tags[i]).toLowerCase();
            if (tag === 'ne' || tag === 'ne-np') return 'ne';
            if (tag === 'en' || tag.indexOf('en-') === 0) return 'en';
        }
        return null;
    }

    var explicit = stored();
    var locale = isSupported(explicit) ? explicit : (fromNavigator() || 'en');

    var catalogue = null;   // the active non-English override, or null

    /* ---------- Lookup ---------- */
    function interpolate(template, params) {
        if (!params) return template;
        return template.replace(/\{(\w+)\}/g, function (whole, name) {
            return Object.prototype.hasOwnProperty.call(params, name) ? String(params[name]) : whole;
        });
    }

    /** Two forms is the whole story: English and Nepali have the same plural
     *  cardinality. A third locale needs a real CLDR table, not this. */
    function pick(value, params) {
        if (value && typeof value === 'object') {
            return (params && Number(params.count) === 1) ? value.one : value.other;
        }
        return value;
    }

    function raw(key) {
        if (catalogue && Object.prototype.hasOwnProperty.call(catalogue, key)) {
            var v = catalogue[key];
            // null means "deliberately untranslated": fall through to English.
            if (v !== null && v !== '') return v;
        }
        return Object.prototype.hasOwnProperty.call(EN, key) ? EN[key] : null;
    }

    /** Translate. Never returns a key: an unknown key returns '' so a bug shows
     *  as missing text, not as machinery leaking onto a parent's screen. */
    function t(key, params) {
        var value = raw(key);
        if (value === null) return '';
        return interpolate(pick(value, params), params);
    }

    /** Translate only if a real translation exists. Used for server error codes,
     *  which have NO English entry on purpose — in English the server's own
     *  prose is the source of truth and must not be duplicated here. */
    function tOrNull(key, params) {
        var value = raw(key);
        if (value === null) return null;
        return interpolate(pick(value, params), params);
    }

    /* ---------- The applier ----------
     * textContent only. innerHTML is banned in this file: story titles are model
     * output and already treated as hostile everywhere else, and a catalogue
     * that could inject markup would be the one place that assumption breaks. */
    function applyText(el) {
        var key = el.getAttribute('data-i18n');
        var value = t(key, null);
        if (!value) return;   // no translation: leave the English markup alone

        var slotNames = el.getAttribute('data-i18n-slots');
        if (!slotNames) {
            el.textContent = value;
            return;
        }
        // Mixed content (a sentence containing links). Preserve the real child
        // elements and re-thread them through the translated sentence, so a
        // translation can never delete the link to the Terms it consents to.
        var slots = {};
        var names = slotNames.split(',');
        var missing = false;
        names.forEach(function (name) {
            name = name.trim();
            var node = el.querySelector('[data-i18n-slot="' + name + '"]');
            if (!node) { missing = true; return; }
            slots[name] = node;
        });
        if (missing) return;   // markup changed under us: keep English

        var parts = value.split(/(\{\w+\})/);
        var rebuilt = [];
        for (var i = 0; i < parts.length; i++) {
            var part = parts[i];
            var match = /^\{(\w+)\}$/.exec(part);
            if (match && slots[match[1]]) {
                var slotEl = slots[match[1]];
                var label = t(slotEl.getAttribute('data-i18n'), null);
                if (label) slotEl.textContent = label;
                rebuilt.push(slotEl);
            } else if (part) {
                rebuilt.push(document.createTextNode(part));
            }
        }
        el.replaceChildren.apply(el, rebuilt);
    }

    function applyAttrs(el) {
        // "placeholder:key;title:other.key"
        el.getAttribute('data-i18n-attr').split(';').forEach(function (pair) {
            var bits = pair.split(':');
            if (bits.length !== 2) return;
            var attr = bits[0].trim();
            if (ATTR_ALLOWLIST.indexOf(attr) === -1) return;
            var value = t(bits[1].trim(), null);
            if (value) el.setAttribute(attr, value);
        });
    }

    /** Translate a subtree. Safe to call more than once and safe to call with no
     *  catalogue loaded (it becomes a no-op). */
    function apply(root) {
        var scope = root || document;
        if (!catalogue) return;
        try {
            scope.querySelectorAll('[data-i18n]').forEach(function (el) {
                // A slot child carries its own key but is rendered by its parent.
                if (el.hasAttribute('data-i18n-slot')) return;
                applyText(el);
            });
            scope.querySelectorAll('[data-i18n-attr]').forEach(applyAttrs);
        } catch (e) {
            // Whatever half-applied stays; the rest is English. Both are readable.
            if (window.console) console.warn('i18n: apply failed', e);
        }
    }

    /* ---------- The switcher ----------
     * Lives here rather than in app.js so the legal pages, which do not load
     * app.js at all, still get a working language control. */
    function setLocale(next) {
        if (!isSupported(next) || next === locale) return;
        try { localStorage.setItem(STORE_KEY, next); } catch (_) { /* session-only */ }
        window.location.reload();
    }

    function wireSwitcher() {
        var select = document.getElementById('localeSelect');
        if (!select) return;
        select.value = locale;
        select.addEventListener('change', function () {
            var next = select.value;
            var guard = api.beforeSwitch;
            if (typeof guard !== 'function') return setLocale(next);
            // A story may be generating. Switching reloads and the progress
            // panel disappears, so ask first rather than appearing to lose it.
            Promise.resolve(guard()).then(function (ok) {
                if (ok) setLocale(next);
                else select.value = locale;
            }).catch(function () { setLocale(next); });
        });
    }

    /* ---------- Boot ---------- */
    var api = {
        locale: locale,
        supported: SUPPORTED.slice(),
        t: t,
        tOrNull: tOrNull,
        apply: apply,
        setLocale: setLocale,
        // app.js assigns a function returning (a promise of) a boolean.
        beforeSwitch: null,
        ready: null,
    };
    window.KS_I18N = api;

    /** Reveal content that exists for one locale only.
     *
     *  The legal pages are the reason this exists: privacy.html and terms.html
     *  stay English pending counsel, and a Nepali reader needs to be told that
     *  in Nepali — while an English reader must not be shown a banner saying
     *  the English page is in English. Such a banner cannot live in the markup
     *  as English source, so it ships hidden and is revealed by locale.
     */
    function revealLocaleOnly() {
        document.querySelectorAll('[data-i18n-locale-only]').forEach(function (el) {
            if (el.getAttribute('data-i18n-locale-only') === locale) el.classList.remove('hidden');
        });
    }

    function finish() {
        document.documentElement.setAttribute('data-locale', locale);
        if (locale !== 'en') document.documentElement.lang = locale;
        apply(document);
        revealLocaleOnly();
        wireSwitcher();
    }

    if (locale === 'en') {
        // No CATALOGUE fetch and nothing to wait for — ready is already
        // resolved, so an English visitor's boot is not delayed by a round trip.
        //
        // Not free, though, and the honest number matters: this file itself is
        // a blocking request they do pay (~5.9 KB gzipped), and the data-i18n
        // attributes added ~1.1 KB gzipped to index.html. Both revalidate to 304
        // after the first load. The claim worth defending is behavioural — same
        // markup, same English, same code path — not "zero bytes".
        api.ready = Promise.resolve();
        finish();
    } else {
        api.ready = new Promise(function (resolve) {
            var settled = false;
            function done() {
                if (settled) return;
                settled = true;
                // Only trust a catalogue that actually arrived and has content;
                // a truncated or empty file must not blank out the interface.
                var loaded = window['KS_I18N_' + locale.toUpperCase()];
                if (loaded && typeof loaded === 'object' && Object.keys(loaded).length) {
                    catalogue = loaded;
                }
                finish();
                resolve();
            }
            var timer = setTimeout(done, LOAD_TIMEOUT_MS);
            var script = document.createElement('script');
            script.src = '/assets/i18n/' + locale + '.js';
            script.onload = function () { clearTimeout(timer); done(); };
            script.onerror = function () { clearTimeout(timer); done(); };
            document.head.appendChild(script);
        });
    }
})();
