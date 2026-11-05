/* Small page behaviours. No inline scripts anywhere, so the CSP can stay strict. */
(function () {
  // tea card gallery: thumbnails swap the main photo; without JS they simply open the image
  var thumbs = document.getElementById("tea-thumbs");
  if (thumbs) {
    thumbs.addEventListener("click", function (e) {
      var a = e.target.closest("a[data-full]");
      if (!a) return;
      e.preventDefault();
      var img = document.getElementById("tea-photo-img");
      img.src = a.dataset.full;
      img.alt = a.dataset.alt || "";
      var credit = document.getElementById("tea-photo-credit");
      credit.textContent = a.dataset.credit || "";
      if (a.dataset.source) {
        var link = document.createElement("a");
        link.href = a.dataset.source; link.target = "_blank"; link.rel = "noopener nofollow"; link.textContent = "source";
        credit.append(" · ", link);
      }
      thumbs.querySelectorAll("a").forEach(function (t) { t.removeAttribute("aria-current"); });
      a.setAttribute("aria-current", "true");
    });
  }
  // forms that need a confirmation: <form data-confirm="...">
  document.addEventListener("submit", function (e) {
    var message = e.target.dataset && e.target.dataset.confirm;
    if (message && !window.confirm(message)) e.preventDefault();
  });
  // quote a reply into the reply box
  document.addEventListener("click", function (e) {
    var button = e.target.closest("[data-quote]");
    var box = document.getElementById("reply-body");
    if (!button || !box) return;
    var first = button.dataset.quote.split(/\n\s*\n/)[0].replace(/\s+/g, " ").trim();
    box.value = "> @" + button.dataset.quoteAuthor + ": " + first + "\n\n" + box.value;
    box.focus();
    box.scrollIntoView({ block: "center" });
  });
  // copy-link and native share buttons
  function copy(button, text) {
    if (navigator.clipboard) navigator.clipboard.writeText(text).then(function () { button.textContent = "Link copied"; });
  }
  document.addEventListener("click", function (e) {
    var share = e.target.closest("[data-share]");
    if (share) {
      if (navigator.share) { navigator.share({ title: document.title, url: share.dataset.share }).catch(function () {}); }
      else { copy(share, share.dataset.share); }
    }
    var copyBtn = e.target.closest("[data-copy-location]");
    if (copyBtn) copy(copyBtn, location.href);
  });
})();

/* The draft of a saved session goes once the save went through; a signed-out browser keeps nobody's draft. */
(function () {
  try {
    if (/^\/sessions\/[0-9a-f-]{36}$/.test(location.pathname)) {
      var saved = sessionStorage.getItem("prolyv:saved-draft");
      if (saved) { localStorage.removeItem(saved); sessionStorage.removeItem("prolyv:saved-draft"); }
    }
  } catch (e) {}
  document.addEventListener("submit", function (e) {
    if (e.target.getAttribute("action") !== "/logout") return;
    try {
      Object.keys(localStorage).forEach(function (key) { if (key.indexOf("prolyv:session-draft:") === 0) localStorage.removeItem(key); });
    } catch (err) {}
  });
})();

/* Live filters: no empty parameters in the URL, and Back restores what the controls show. */
document.addEventListener("htmx:configRequest", function (e) {
  if (!e.detail.elt.classList || !e.detail.elt.classList.contains("teas-filters")) return;
  var params = e.detail.parameters;
  var names = params.keys ? Array.from(params.keys()) : Object.keys(params);
  names.forEach(function (name) {
    var value = params.get ? params.get(name) : params[name];
    if (!value || (name === "sort" && value === "popular")) { if (params.delete) params.delete(name); else delete params[name]; }
  });
});
document.addEventListener("htmx:historyRestore", function () {
  var query = new URLSearchParams(location.search);
  document.querySelectorAll(".teas-filters input[name], .teas-filters select[name]").forEach(function (el) {
    el.value = query.get(el.name) || (el.name === "sort" ? "popular" : "");
  });
});

/* Menus built on <details> close when you tap outside them. */
document.addEventListener("click", function (e) {
  document.querySelectorAll("details.account-menu[open]").forEach(function (menu) {
    if (!menu.contains(e.target)) menu.removeAttribute("open");
  });
});
