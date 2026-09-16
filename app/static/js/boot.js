// Applies the saved language and theme before first paint (no flash of the wrong theme or direction).
(function () {
  var lang = "en", theme = "auto";
  try {
    lang = localStorage.getItem("abp.lang") || ((navigator.language || "").slice(0, 2) === "ar" ? "ar" : "en");
    theme = localStorage.getItem("abp.theme") || "auto";
  } catch (e) { /* storage unavailable */ }
  var root = document.documentElement;
  root.lang = lang === "ar" ? "ar" : "en";
  root.dir = lang === "ar" ? "rtl" : "ltr";
  var dark = theme === "dark" || (theme === "auto" && window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
  root.setAttribute("data-bs-theme", dark ? "dark" : "light");
  if (lang === "ar") {
    var link = document.getElementById("tabler-css");   // parsed before this script
    if (link) link.href = "/static/vendor/tabler/tabler.rtl.min.css";
  }
})();
