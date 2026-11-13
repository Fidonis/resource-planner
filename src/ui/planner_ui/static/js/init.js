// Runs before the first paint (loaded without defer), so the theme and the sidebar are right on
// the first frame instead of changing once the deferred bundles have started.
(function () {
  var root = document.documentElement;
  var stored = null;
  var sidebar = null;
  try {
    stored = localStorage.getItem("rp-theme");
    sidebar = localStorage.getItem("rp-sidebar");
  } catch (e) {
    /* storage can be blocked; the defaults apply */
  }
  var dark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  var theme = stored === "fidonis-light" || stored === "fidonis-dark"
    ? stored
    : (dark ? "fidonis-dark" : "fidonis-light");
  root.setAttribute("data-theme", theme);
  root.setAttribute("data-sidebar", sidebar === "collapsed" || sidebar === "pinned" ? sidebar : "pinned");
})();
