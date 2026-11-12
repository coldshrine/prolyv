/* Telegram sign-in: popup where possible, full-page redirect where popups are blocked (in-app browsers). */
(function () {
  var button = document.querySelector("[data-telegram-login]");
  if (!button) return;
  var cfg = { bot_id: button.dataset.botId };
  function submit(user) {
    if (!user || !user.hash) return;
    var form = document.createElement("form");
    form.method = "POST";
    form.action = "/auth/telegram/callback";
    var fields = Object.assign({}, user, { state: button.dataset.state });
    Object.keys(fields).forEach(function (key) {
      var input = document.createElement("input");
      input.type = "hidden"; input.name = key; input.value = fields[key];
      form.appendChild(input);
    });
    document.body.appendChild(form);
    form.submit();
  }
  function fullPageAuth() {
    location.href = Telegram.Login.widgetsOrigin + "/auth?bot_id=" + encodeURIComponent(cfg.bot_id)
      + "&origin=" + encodeURIComponent(location.origin) + "&return_to=" + encodeURIComponent(location.href);
  }
  if (!window.Telegram || !Telegram.Login) {
    button.disabled = true;
    var note = document.createElement("p");
    note.className = "muted small";
    note.textContent = tr("Telegram sign-in could not load. Check your connection and reload.");
    button.insertAdjacentElement("afterend", note);
    return;
  }
  Telegram.Login.init(cfg, submit); // handles the return from a full-page sign-in (#tgAuthResult)
  button.addEventListener("click", function (event) {
    event.preventDefault();
    var realOpen = window.open, popup = null;
    window.open = function () { popup = realOpen.apply(window, arguments); return popup; };
    try { Telegram.Login.auth(cfg, submit); } finally { window.open = realOpen; }
    if (!popup) fullPageAuth();
  });
})();
