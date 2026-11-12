/* Forum pages: sort without a button, quoting, per-reply Reply, the session picker and links to replies further down. */
(function () {
  document.addEventListener("change", function (e) {
    var form = e.target.closest && e.target.closest("form[data-autosubmit]");
    if (form) form.submit();
    if (e.target.matches && e.target.matches("[data-attach]")) showAttached(e.target);
  });

  function showAttached(select) {
    var label = select.parentNode.querySelector("[data-attach-label]");
    var option = select.options[select.selectedIndex];
    if (label) label.textContent = select.value && option ? option.textContent : tr("Attach a session");
  }

  // What someone actually said: quoted lines are dropped, so quoting a reply that opens with a quote keeps the answer.
  function ownWords(text) {
    var kept = text.replace(/\r\n/g, "\n").split("\n").filter(function (line) { return !/^\s*>/.test(line); }).join("\n");
    var first = kept.split(/\n\s*\n/).map(function (p) { return p.replace(/\s+/g, " ").trim(); }).filter(Boolean)[0];
    if (!first) first = text.replace(/^[\s>]+/, "").split(/\n\s*\n/)[0].replace(/\s+/g, " ").trim();
    return first.length > 600 ? first.slice(0, 600).trim() + "…" : first;
  }

  function goToBox(box) {
    box.focus();
    box.scrollIntoView({ block: "center" });
  }

  document.addEventListener("click", function (e) {
    var box = document.getElementById("reply-body");
    if (!box || !e.target.closest) return;
    var quote = e.target.closest("[data-quote-text]");
    if (quote) {
      box.value = "> @" + quote.dataset.quoteAuthor + ": " + ownWords(quote.dataset.quoteText) + "\n\n" + box.value;
      goToBox(box);
      return;
    }
    var reply = e.target.closest("[data-reply-to]");
    if (reply) {
      e.preventDefault();
      var mention = reply.dataset.replyTo ? "@" + reply.dataset.replyTo + " " : "";
      if (mention && box.value.indexOf(mention) === -1) box.value = box.value ? box.value.replace(/\s*$/, "\n\n") + mention : mention;
      goToBox(box);
    }
  });

  document.querySelectorAll("[data-attach]").forEach(showAttached);

  // the picked section stays in view in the sideways row of chips
  var chip = document.querySelector(".f-chip[aria-current]");
  if (chip && chip.parentNode.scrollWidth > chip.parentNode.clientWidth) {
    chip.parentNode.scrollLeft = chip.offsetLeft - (chip.parentNode.clientWidth - chip.offsetWidth) / 2;
  }

  // an old link to a reply that is not among the first ones loaded: ask for the part of the thread that has it
  var match = location.hash.match(/^#reply-([0-9a-f-]{36})$/);
  if (match && !document.getElementById(location.hash.slice(1))) {
    var query = new URLSearchParams(location.search);
    if (document.getElementById("replies") && !query.get("reply")) {
      query.set("reply", match[1]);
      location.replace(location.pathname + "?" + query.toString() + location.hash);
    }
  }
})();
