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
