/* Small shared behaviours: close the nav drawer on outside tap, link tap or Escape. */
(function () {
  function closeAll(except) {
    document.querySelectorAll('details.hzc-drawer[open]').forEach(function (d) { if (d !== except) d.open = false; });
  }
  document.addEventListener('click', function (e) {
    var d = e.target.closest && e.target.closest('details.hzc-drawer');
    closeAll(d);
  });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') closeAll(null); });
})();
