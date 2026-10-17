/* Data freshness bar: when the data was last refreshed, a live countdown to the next refresh, and an
   honest "running late" state when the bot is behind. Added at the top of every page.

   The schedule rules come from forecast.json (meta.schedule, written by pipeline/schedule.py), so
   this always matches what the bot actually does:
     the bot wakes at minute `check_minute` past every UTC hour, runs only if the newest snapshot is
     at least `min_gap_minutes` old, starts about `start_lag_minutes` late, and takes about
     `process_seconds` to scrape, forecast and publish.

   Testing: add ?pulseNow=2026-10-18T03:30:00Z to any page to pretend it is that moment. */
(function () {
  'use strict';

  var DATA = 'data/forecast.json';
  var DEFAULTS = { check_minute: 7, min_gap_minutes: 210, start_lag_minutes: 10, process_seconds: 75 };
  var LATE_AFTER_MS = 20 * 60000;       // this long past the expected time counts as "late"
  var skew = 0;
  try {
    var q = new URLSearchParams(location.search).get('pulseNow');
    var t0 = q ? Date.parse(q) : NaN;
    if (!isNaN(t0)) skew = t0 - Date.now();
  } catch (e) {}
  var now = function () { return Date.now() + skew; };
  var hiddenTab = function () { return document.hidden && skew === 0; };   // a time-travel test run ignores tab visibility

  var meta = null;        // the meta this page was built from
  var fresh = null;       // newer meta, once the bot has published it
  var open = false;
  var bar, row, label, sub, fill, panel, toggle, live, lastState = '';
  var polling = null, pollMs = 0, baseSig = null;

  // ---------- formatting (everything in IST, like the rest of the site) ----------
  function pad(n) { return n < 10 ? '0' + n : '' + n; }
  function istDate(ms) { return new Date(ms + 19800000); }
  function hm(ms) { var d = istDate(ms); return pad(d.getUTCHours()) + ':' + pad(d.getUTCMinutes()); }
  function dayKey(ms) { var d = istDate(ms); return d.getUTCFullYear() * 10000 + (d.getUTCMonth() + 1) * 100 + d.getUTCDate(); }
  function when(ms) {
    var diff = dayKey(ms) - dayKey(now());
    return hm(ms) + ' IST' + (diff === 1 ? ' tomorrow' : diff === -1 ? ' yesterday' : diff > 1 || diff < -1 ? ' (' + istDate(ms).getUTCDate() + '/' + (istDate(ms).getUTCMonth() + 1) + ')' : '');
  }
  function dur(ms) {
    var s = Math.max(0, Math.round(ms / 1000)), h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
    if (h) return h + 'h ' + pad(m) + 'm';
    if (m) return m + 'm ' + pad(x) + 's';
    return x + 's';
  }
  function age(ms) {
    var m = Math.round(ms / 60000);
    return m < 60 ? m + ' min' : Math.floor(m / 60) + 'h ' + pad(m % 60) + 'm';
  }

  // ---------- the schedule maths ----------
  function plan(m) {
    var S = {}, k, src = m.schedule || {};
    for (k in DEFAULTS) S[k] = src[k] != null ? src[k] : DEFAULTS[k];
    var last = Date.parse(m.data_through);
    var earliest = last + S.min_gap_minutes * 60000;
    var t = new Date(earliest); t.setUTCMinutes(S.check_minute, 0, 0);
    if (t.getTime() < earliest) t = new Date(t.getTime() + 3600000);
    var check = t.getTime();
    var start = check + S.start_lag_minutes * 60000;
    return { S: S, last: last, earliest: earliest, check: check, start: start, live: start + S.process_seconds * 1000 };
  }
  function nextCheckAfter(p, n) {
    return n <= p.check ? p.check : p.check + Math.ceil((n - p.check) / 3600000) * 3600000;
  }

  // ---------- DOM ----------
  function build() {
    bar = document.createElement('div');
    bar.id = 'hz-pulse';
    bar.setAttribute('role', 'region');
    bar.setAttribute('aria-label', 'Data freshness');
    bar.innerHTML =
      '<div class="hzp-top">' +
        '<button type="button" class="hzp-row" aria-expanded="false" aria-controls="hzp-panel">' +
          '<span class="hzp-mark" aria-hidden="true"></span>' +
          '<span class="hzp-label"><span class="hzp-k">Data freshness</span></span>' +
          '<span class="hzp-sub"></span>' +
          '<span class="hzp-chev" aria-hidden="true">▾</span>' +
        '</button>' +
        '<a class="hzp-refresh" href="#" hidden>Refresh to see it</a>' +
      '</div>' +
      '<div class="hzp-bar" aria-hidden="true"><i></i></div>' +
      '<div class="hzp-panel" id="hzp-panel" hidden></div>' +
      '<span class="hzp-sr" aria-live="polite"></span>';
    document.body.insertBefore(bar, document.body.firstChild);
    row = bar.querySelector('.hzp-row'); label = bar.querySelector('.hzp-label'); sub = bar.querySelector('.hzp-sub');
    fill = bar.querySelector('.hzp-bar i'); panel = bar.querySelector('.hzp-panel'); live = bar.querySelector('.hzp-sr');
    toggle = bar.querySelector('.hzp-chev');
    row.addEventListener('click', function () {
      open = !open; row.setAttribute('aria-expanded', String(open)); panel.hidden = !open; toggle.textContent = open ? '▴' : '▾'; render(); sizeVar();
    });
    bar.querySelector('.hzp-refresh').addEventListener('click', function (e) { e.preventDefault(); location.reload(); });
    function close() { if (!open) return; open = false; row.setAttribute('aria-expanded', 'false'); panel.hidden = true; toggle.textContent = '▾'; sizeVar(); }
    panel.addEventListener('click', function (e) { if (e.target.closest('.hzp-close')) { close(); row.focus(); } });
    document.addEventListener('click', function (e) { if (open && !bar.contains(e.target)) close(); });
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape') close(); });
    if (window.ResizeObserver) new ResizeObserver(sizeVar).observe(bar);
    sizeVar();
  }
  // pages that fill the viewport (the live map) subtract the bar's height
  function sizeVar() { document.documentElement.style.setProperty('--hz-strip', (bar ? bar.offsetHeight : 0) + 'px'); }

  function panelHtml(p, m, state, n) {
    var S = p.S, built = m.generated_at ? Date.parse(m.generated_at) : null, late = state === 'late';
    var check = late ? nextCheckAfter(p, n) : p.check;
    var start = check + S.start_lag_minutes * 60000, live = start + S.process_seconds * 1000;
    var gap = Math.floor(S.min_gap_minutes / 60) + 'h ' + pad(S.min_gap_minutes % 60) + 'm';
    return '<div class="hzp-head"><span class="hzp-k">How the data stays fresh</span><button type="button" class="hzp-close" aria-label="Close">×</button></div>' +
      '<ol class="hzp-steps">' +
      '<li><b>Last refresh</b><span>Data through ' + when(p.last) + ' (' + age(n - p.last) + ' ago)' +
        (built ? '; forecast rebuilt ' + hm(built) + ' IST' : '') + '.</span></li>' +
      '<li><b>' + (late ? 'Missed slot, next try' : 'Next bot run') + '</b><span>' +
        (late ? 'The ' + hm(p.check) + ' IST run has not shown up. The bot tries again at ' : 'Scheduled for ') + when(check) +
        '; GitHub usually starts it about ' + S.start_lag_minutes + ' minutes later (around ' + hm(start) + ' IST). ' +
        'The bot wakes at :' + pad((S.check_minute + 30) % 60) + ' IST every hour and only runs when the data is at least ' + gap + ' old.</span></li>' +
      '<li><b>Scrape, forecast, publish</b><span>Reading eRaktKosh, rebuilding the forecast and publishing the site takes about ' + (S.process_seconds < 100 ? 'a minute' : Math.round(S.process_seconds / 60) + ' minutes') + '.</span></li>' +
      '<li><b>Live on this site</b><span>' + (late ? 'If the next try runs on time, ' : 'About ') + when(live) + '.</span></li>' +
      '</ol>' +
      '<p class="hzp-note">GitHub runs scheduled jobs on a best-effort basis, so the bot can be late. If it is, this bar says so instead of guessing, and the page tells you as soon as new data is published.</p>';
  }

  // ---------- render, once a second ----------
  function render() {
    if (!meta) return;
    var n = now(), p = plan(meta), over = n - p.live, state, text, subtext, color;
    if (fresh) {
      state = 'new'; text = '<span class="hzp-k">New data is live</span>'; subtext = 'through ' + when(Date.parse(fresh.data_through)); color = 'new';
    } else if (over < 0) {
      state = 'wait'; color = 'ok';
      text = '<span class="hzp-k">Next update in</span> <b>' + dur(-over) + '</b>';
      subtext = 'Data through ' + hm(p.last) + ' IST · ' + age(n - p.last) + ' old';
    } else if (over < LATE_AFTER_MS) {
      state = 'due'; color = 'warn';
      text = '<span class="hzp-k">Update due</span> <b>checking now</b>';
      subtext = 'Expected around ' + hm(p.live) + ' IST · data is ' + age(n - p.last) + ' old';
    } else {
      state = 'late'; color = 'bad';
      text = '<span class="hzp-k">Running late</span> <b>waiting ' + age(over) + '</b>';
      subtext = 'GitHub is behind on its schedule · next try about ' + hm(nextCheckAfter(p, n)) + ' IST';
    }
    bar.setAttribute('data-state', color);
    label.innerHTML = text; sub.textContent = subtext;
    var total = Math.max(1, p.live - p.last);
    fill.style.width = (fresh ? 100 : Math.max(0, Math.min(1, (n - p.last) / total)) * 100).toFixed(1) + '%';
    bar.querySelector('.hzp-refresh').hidden = !fresh;
    if (open) panel.innerHTML = panelHtml(p, meta, state, n);
    if (state !== lastState) {   // only announce changes of state, never the ticking seconds
      lastState = state;
      live.textContent = state === 'new' ? 'New data is live. Refresh to see it.' : state === 'due' ? 'Data update is due now.' : state === 'late' ? 'Data update is running late.' : '';
    }
  }

  // ---------- noticing the new data ----------
  function sig(res) { return res.headers.get('etag') || (res.headers.get('last-modified') + '|' + res.headers.get('content-length')); }
  function pull() {   // full read, compare with what the page is showing
    return fetch(DATA, { cache: 'no-store' }).then(function (r) { baseSig = sig(r); return r.json(); }).then(function (j) {
      if (j && j.meta && j.meta.generated_at !== meta.generated_at) { fresh = j.meta; render(); stopPolling(); }
    });
  }
  function check() {
    if (!meta || fresh) return;
    if (baseSig === null) { pull().catch(function () {}); return; }
    fetch(DATA, { method: 'HEAD', cache: 'no-store' }).then(function (r) { if (sig(r) !== baseSig) return pull(); }).catch(function () {});
  }
  function stopPolling() { if (polling) { clearInterval(polling); polling = null; pollMs = 0; } }
  function schedulePolling() {
    // quiet until the bot could plausibly have started, then every 30 s; every 2 min once clearly late
    if (fresh) return;
    var p = plan(meta), n = now();
    if (n < p.check - 60000) return;
    var want = n - p.live > 30 * 60000 ? 120000 : 30000;
    if (polling && pollMs === want) return;
    stopPolling();
    pollMs = want;
    check();
    polling = setInterval(function () { if (!hiddenTab()) check(); }, want);
  }

  function tick() {
    if (hiddenTab()) return;
    render();
    schedulePolling();
  }

  function start(data) {
    meta = data.meta;
    build();
    render();
    setInterval(tick, 1000);
    document.addEventListener('visibilitychange', function () { if (!hiddenTab()) { render(); check(); } });
  }

  function load() {
    var p = window.HemzoData ? window.HemzoData.load() : fetch(DATA, { cache: 'no-cache' }).then(function (r) { return r.json(); });
    p.then(start).catch(function () { /* no data, no bar */ });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', load); else load();
})();
