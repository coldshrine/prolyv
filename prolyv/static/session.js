/* Session form: steep timer, manual steeps, tea picker, draft autosave (localStorage). Vanilla JS, no deps. */
(function () {
  var form = document.getElementById("session-form");
  if (!form) return;
  var list = document.getElementById("steep-list");
  var display = document.getElementById("timer-display");
  var toggle = document.getElementById("timer-toggle");
  var suggestEl = document.getElementById("timer-suggest");
  var suggested = (form.dataset.suggested || "").split(",").filter(Boolean).map(Number);
  var draftKey = "prolyv:session-draft:" + (form.dataset.draftKey || "new");
  var running = false, startedAt = 0, tick = null;

  function fmt(sec) { var m = Math.floor(sec / 60), s = sec % 60; return m + ":" + (s < 10 ? "0" : "") + s; }
  function steepCount() { return list.querySelectorAll(".steep-item").length; }
  function updateSuggest() {
    var n = steepCount();
    suggestEl.textContent = suggested[n] ? "Steep " + (n + 1) + " · suggested " + suggested[n] + " s" : (n ? "Steep " + (n + 1) : "");
  }
  function renumber() {
    list.querySelectorAll(".steep-item").forEach(function (li, i) { li.querySelector(".steep-n").textContent = i + 1; });
    updateSuggest();
  }
  function addSteep(seconds, note) {
    var li = document.createElement("li");
    li.className = "steep-item";
    li.innerHTML = '<span class="steep-n"></span>' +
      '<input type="number" name="steep_seconds" min="1" max="3600" inputmode="numeric" aria-label="Seconds">' +
      '<span class="steep-unit">s</span>' +
      '<input type="text" name="steep_note" maxlength="200" placeholder="note" aria-label="Note">' +
      '<button type="button" class="steep-remove" aria-label="Remove steep">×</button>';
    li.querySelector("[name=steep_seconds]").value = seconds;
    li.querySelector("[name=steep_note]").value = note || "";
    list.appendChild(li);
    renumber();
    saveDraft();
  }
  list.addEventListener("click", function (e) {
    if (e.target.classList.contains("steep-remove")) { e.target.closest(".steep-item").remove(); renumber(); saveDraft(); }
  });

  function stopTimer() {
    running = false; clearInterval(tick);
    var sec = Math.max(1, Math.round((Date.now() - startedAt) / 1000));
    addSteep(sec, "");
    display.textContent = "0:00";
    toggle.textContent = "Start steep";
    toggle.classList.add("btn-primary"); toggle.classList.remove("btn-danger");
  }
  toggle.addEventListener("click", function () {
    if (running) { stopTimer(); return; }
    running = true; startedAt = Date.now();
    toggle.textContent = "Stop & save steep";
    toggle.classList.remove("btn-primary"); toggle.classList.add("btn-danger");
    tick = setInterval(function () { display.textContent = fmt(Math.round((Date.now() - startedAt) / 1000)); }, 250);
  });
  document.getElementById("timer-reset").addEventListener("click", function () {
    running = false; clearInterval(tick); display.textContent = "0:00"; toggle.textContent = "Start steep";
    toggle.classList.add("btn-primary"); toggle.classList.remove("btn-danger");
  });
  document.getElementById("manual-add").addEventListener("click", function () {
    var input = document.getElementById("manual-seconds");
    var sec = parseInt(input.value, 10);
    if (sec > 0) { addSteep(sec, ""); input.value = ""; }
    else if (suggested[steepCount()]) { addSteep(suggested[steepCount()], ""); }
  });

  // tea picker (HTMX fills #tea-suggest; we handle the click)
  var suggest = document.getElementById("tea-suggest");
  var teaId = document.getElementById("tea_id"), teaName = document.getElementById("tea_name");
  var linked = document.getElementById("tea-linked");
  suggest.addEventListener("click", function (e) {
    var item = e.target.closest(".suggest-item");
    if (!item) return;
    teaId.value = item.dataset.id; teaName.value = item.dataset.name;
    ["grams", "water", "temp"].forEach(function (k) {
      var el = document.getElementById(k === "water" ? "water_ml" : k === "temp" ? "temp_c" : "grams");
      if (el && !el.value && item.dataset[k]) el.value = item.dataset[k];
    });
    if (item.dataset.seconds && !suggested.length) { suggested = item.dataset.seconds.split(",").map(Number); }
    suggest.innerHTML = ""; linked.hidden = false; updateSuggest(); saveDraft();
  });
  teaName.addEventListener("input", function () { if (teaId.value) { teaId.value = ""; linked.hidden = true; } });
  document.getElementById("tea-unlink").addEventListener("click", function (e) { e.preventDefault(); teaId.value = ""; linked.hidden = true; saveDraft(); });

  // draft autosave
  function collect() {
    var data = {};
    form.querySelectorAll("input, textarea, select").forEach(function (el) {
      if (!el.name || el.name === "csrf_token" || el.name.indexOf("steep_") === 0) return;
      if (el.type === "checkbox") data[el.name] = el.checked;
      else if (el.type === "radio") { if (el.checked) data[el.name] = el.value; }
      else data[el.name] = el.value;
    });
    data.steeps = Array.prototype.map.call(list.querySelectorAll(".steep-item"), function (li) {
      return { seconds: li.querySelector("[name=steep_seconds]").value, note: li.querySelector("[name=steep_note]").value };
    });
    return data;
  }
  function saveDraft() { try { localStorage.setItem(draftKey, JSON.stringify(collect())); } catch (e) {} }
  function restoreDraft() {
    var raw = null;
    try { raw = localStorage.getItem(draftKey); } catch (e) {}
    if (!raw || form.dataset.editing) return;
    var data; try { data = JSON.parse(raw); } catch (e) { return; }
    if (!data || (!data.tea_name && !(data.steeps || []).length && !data.note)) return;
    if (steepCount() || (teaName.value && teaName.value !== data.tea_name)) return; // the page already has content
    Object.keys(data).forEach(function (name) {
      if (name === "steeps") return;
      var els = form.querySelectorAll('[name="' + name + '"]');
      els.forEach(function (el) {
        if (el.type === "checkbox") el.checked = !!data[name];
        else if (el.type === "radio") el.checked = el.value === data[name];
        else el.value = data[name];
      });
    });
    (data.steeps || []).forEach(function (s) { if (s.seconds) addSteep(s.seconds, s.note); });
    if (teaId.value) linked.hidden = false;
    var hint = document.getElementById("draft-hint"); hint.hidden = false;
    document.getElementById("draft-discard").addEventListener("click", function (e) {
      e.preventDefault(); try { localStorage.removeItem(draftKey); } catch (err) {} location.reload();
    });
  }
  form.addEventListener("input", saveDraft);
  form.addEventListener("change", saveDraft);
  form.addEventListener("submit", function () { try { localStorage.removeItem(draftKey); } catch (e) {} });
  window.addEventListener("beforeunload", function () { if (running) saveDraft(); });

  restoreDraft();
  renumber();
})();
