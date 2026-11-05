// The phone top bar's More menu closes on an outside tap or Escape.
(function () {
  function closeMenus(except) {
    document.querySelectorAll("details.pf-more[open]").forEach(function (menu) {
      if (menu !== except) menu.removeAttribute("open");
    });
  }
  // On a phone the tabs scroll sideways: keep the current one in view after a page load.
  var current = document.querySelector(".pf-tab[aria-current]");
  if (current) current.parentElement.scrollLeft = current.offsetLeft - current.parentElement.offsetLeft - 16;
  document.addEventListener("click", function (event) {
    closeMenus(event.target.closest ? event.target.closest("details.pf-more") : null);
  });
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") closeMenus(null);
  });
})();
