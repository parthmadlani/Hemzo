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

  return { load: load, STATUS: STATUS, NODATA: NODATA, COMPONENT_LABEL: COMPONENT_LABEL,
           disp: disp, stamp: stamp, ago: ago, haversineKm: haversineKm };
})();
