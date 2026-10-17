/* Shared loader + helpers for web/data/forecast.json, which the pipeline (pipeline/run.py)
   rewrites every time the scraper adds a snapshot. */
window.HemzoData = (function () {
  var promise = null;

  function load() {
    if (!promise) {
      promise = (window.__hdp || fetch('data/forecast.json', { cache: 'no-cache' })).then(function (r) {
        if (!r.ok) throw new Error('forecast.json: HTTP ' + r.status);
        return r.json();
      });
    }
    return promise;
  }

  var STATUS = {
    critical: { label: 'Critical', color: '#E3312A', ink: '#FFF7F3', note: 'below 55% of its usual level' },
    low:      { label: 'Low',      color: '#FFC845', ink: '#7A5A00', note: 'below 85% of its usual level' },
    steady:   { label: 'Steady',   color: '#4FC3F7', ink: '#0B4A66', note: 'close to its usual level' },
    surplus:  { label: 'Surplus',  color: '#3ECF8E', ink: '#0B4A38', note: 'well above its usual level' },
    sparse:   { label: 'Sparse',   color: '#CFC8C4', ink: '#171019', note: 'too few units here to judge a trend' }
  };
  var NODATA = { label: 'No reports', color: '#CFC8C4', ink: '#171019', note: 'no bank here has ever published this group' };

  var COMPONENT_LABEL = { 'Packed RBC': 'Packed red cells', 'Whole Blood': 'Whole blood' };

  // data uses ASCII 'O-'; the design shows a true minus sign
  function disp(group) { return group.replace('-', '−'); }

  function pad(n) { return n < 10 ? '0' + n : '' + n; }
  var MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

  // "17 Oct, 20:32 IST" read straight from the +05:30 timestamp, whatever the viewer's timezone
  function stamp(meta) {
    var m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(meta.data_through);
    return m ? (+m[3]) + ' ' + MONTHS[+m[2] - 1] + ', ' + m[4] + ':' + m[5] + ' IST' : meta.data_through;
  }

  function ago(meta) {
    var mins = Math.max(0, Math.round((Date.now() - new Date(meta.data_through).getTime()) / 60000));
    if (mins < 60) return mins + ' min ago';
    var h = Math.round(mins / 60);
    return h < 48 ? h + ' h ago' : Math.round(h / 24) + ' days ago';
  }

  function haversineKm(a, b) {
    var R = 6371, rad = Math.PI / 180;
    var dLat = (b.lat - a.lat) * rad, dLon = (b.lon - a.lon) * rad;
    var x = Math.sin(dLat / 2) * Math.sin(dLat / 2) +
      Math.cos(a.lat * rad) * Math.cos(b.lat * rad) * Math.sin(dLon / 2) * Math.sin(dLon / 2);
    return 2 * R * Math.asin(Math.sqrt(x));
  }

  // "45% of usual", but "4.4x its usual level" once the figure gets silly
  function usual(r) {
    var c = r.cover;
    if (c >= 3) return (Math.round(c * 10) / 10).toFixed(1).replace(/\.0$/, '') + '\u00d7 its usual level';
    return Math.round(c * 100) + '% of usual';
  }

  // higher = more worth looking at. No data and tiny series sink to the bottom.
  function riskScore(r) {
    if (!r || r.nodata) return -2;
    if (r.status === 'sparse') return -1;
    return (r.risk3 == null ? 0 : r.risk3) + (1 - Math.min(r.cover, 1.5)) * 0.6;
  }

  // 0 units is ambiguous in a public register: sold out, or nobody is publishing this group
  var ZERO_NOTE = 'Zero can mean the group ran out, or that no bank here is publishing it. The public register cannot tell the two apart.';

  var GLOSSARY = [
    { t: 'Usual level', d: 'What a blood group normally holds in a district: its own median over the last 14 days. \u201c86% of usual\u201d means 14% below normal.' },
    { t: 'Critical, Low, Steady, Surplus', d: 'Critical is under 55% of usual, Low is under 85%, Steady is up to 135%, and Surplus is above that. Very small stocks are marked Sparse instead, because a few units swing the percentage.' },
    { t: 'Shortage risk', d: 'The chance, judged from past patterns, that stock drops below 55% of usual within 3 days.' },
    { t: '80% band', d: 'The range the forecast says stock will land in about 8 times out of 10. A wide band means a less certain forecast.' },
    { t: 'Zero units', d: ZERO_NOTE }
  ];

  return { load: load, STATUS: STATUS, NODATA: NODATA, COMPONENT_LABEL: COMPONENT_LABEL,
           disp: disp, stamp: stamp, ago: ago, haversineKm: haversineKm,
           usual: usual, riskScore: riskScore, ZERO_NOTE: ZERO_NOTE, GLOSSARY: GLOSSARY };
})();
