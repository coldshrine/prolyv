/* New session: steppers, steep timer, tea picker, live preview, draft autosave. Session page: steep by steep, Follow, Reply. Vanilla JS, no deps. */
(function () {
  var PALE = [255, 243, 220];

  // The same lightening as cup_styles on the server: every colour in the gradient moves towards the pale tone.
  function lighten(css, step) {
    var t = (Math.min(8, Math.max(1, step)) - 1) / 12;
    return css.replace(/#[0-9a-fA-F]{6}/g, function (hex) {
      var out = "#";
      for (var i = 0; i < 3; i++) {
        var v = parseInt(hex.substr(1 + i * 2, 2), 16);
        var c = Math.round(v + (PALE[i] - v) * t).toString(16);
        out += c.length < 2 ? "0" + c : c;
      }
      return out;
    });
  }
  function paintCup(cup, swirl, core, gloss) {
    if (!cup) return;
    cup.querySelector(".cup-swirl").style.background = swirl;
    var layers = cup.querySelectorAll(".cup-layer");
    layers[0].style.background = core;
    if (gloss) layers[1].style.background = gloss;
  }

  // tab bar: logging and reading sessions belong to a tab, though their URLs don't say so
  var tabHint = document.querySelector("[data-tab]");
  var tabTarget = tabHint ? tabHint.dataset.tab : (document.getElementById("session-form") ? "/diary" : "");
  if (tabTarget) {
    document.querySelectorAll(".tabbar a[aria-current]").forEach(function (a) { a.removeAttribute("aria-current"); });
    var tab = document.querySelector('.tabbar a[href="' + tabTarget + '"]');
    if (tab) tab.setAttribute("aria-current", "page");
  }

  initForm();
  initPage();

  function initForm() {
    var form = document.getElementById("session-form");
    if (!form) return;
    var MAX_STEEPS = 40;
    var ORBS = [["#FF8A3D", "#B33A00"], ["#FF9A45", "#C24A08"], ["#FFA94F", "#D05A10"], ["#FFB85C", "#DB6B18"], ["#FFC56E", "#E27E24"], ["#FFD283", "#E89134"], ["#FFDE9C", "#ECA54C"], ["#FFE9B8", "#F0BA6C"]];
    var hasErrors = !!form.dataset.errors;
    var editing = !!form.dataset.editing;
    var list = document.getElementById("steep-list");
    var display = document.getElementById("timer-display");
    var status = document.getElementById("timer-status");
    var toggle = document.getElementById("timer-toggle");
    var logBtn = document.getElementById("timer-log");
    var addBtn = document.getElementById("steep-add");
    var suggestEl = document.getElementById("timer-suggest");
    var suggested = (form.dataset.suggested || "").split(",").filter(Boolean).map(Number);
    var draftKey = "prolyv:session-draft:" + (form.dataset.user || "") + ":" + (form.dataset.draftKey || "new");
    var running = false, startedAt = 0, banked = 0, tick = null;
    var teaId = document.getElementById("tea_id"), teaName = document.getElementById("tea_name");
    var teaCard = document.getElementById("tea-card"), teaField = document.getElementById("tea-field");
    var suggest = document.getElementById("tea-suggest");
    var pvCupBox = document.getElementById("pv-cup");
    var cardInfo = null; // what the draft needs to redraw a linked tea

    if (!hasErrors) form.classList.add("js-ready"); // on an error page every steep stays open, so a bad one can be fixed

    function pad(n) { return (n < 10 ? "0" : "") + n; }
    function fmt(sec) { return pad(Math.floor(sec / 60)) + ":" + pad(sec % 60); }
    function items() { return list.querySelectorAll(".steep-item"); }
    function steepCount() { return items().length; }
    function elapsedSec() { return Math.floor((banked + (running ? Date.now() - startedAt : 0)) / 1000); }
    function lastSeconds() {
      var all = items();
      return all.length ? parseInt(all[all.length - 1].querySelector("[name=steep_seconds]").value, 10) || 0 : 0;
    }
    function nextSuggested() {
      var n = steepCount();
      if (suggested[n]) return suggested[n];
      if (!n) return 10;
      var last = lastSeconds();
      return last < 30 ? last + 5 : Math.round((last * 1.5) / 5) * 5;
    }
    function orbCss(i) {
      var p = ORBS[Math.min(i, ORBS.length - 1)];
      return "radial-gradient(circle at 32% 28%, rgba(255,255,255,0.85) 0, rgba(255,255,255,0) 26%), radial-gradient(circle at 50% 60%, " + p[0] + " 0, " + p[1] + " 100%)";
    }
    function showStatus() {
      var n = Math.min(steepCount() + 1, MAX_STEEPS);
      status.textContent = "STEEP " + n + " · " + (running ? "RUNNING" : banked ? "PAUSED" : "READY");
    }
    function renumber() {
      var all = items();
      all.forEach(function (li, i) {
        var seconds = li.querySelector("[name=steep_seconds]").value;
        li.querySelector(".steep-n").textContent = i + 1;
        li.querySelector(".steep-sec").textContent = seconds + " s";
        li.querySelector(".orb").style.background = orbCss(i);
        li.querySelector(".steep-orb").setAttribute("aria-label", "Steep " + (i + 1) + ", " + seconds + " seconds");
      });
      var full = all.length >= MAX_STEEPS, next = nextSuggested();
      document.getElementById("steep-count").textContent = all.length;
      document.getElementById("steep-empty").hidden = all.length > 0;
      document.getElementById("steep-full").hidden = !full;
      document.getElementById("steep-add-sec").textContent = next;
      suggestEl.textContent = next + " s";
      logBtn.disabled = full; addBtn.disabled = full;
      showStatus();
      refresh();
    }
    function openSteep(li) {
      items().forEach(function (other) {
        var open = other === li;
        other.classList.toggle("is-open", open);
        other.querySelector(".steep-orb").setAttribute("aria-expanded", open ? "true" : "false");
      });
    }
    function addSteep(seconds, note, quiet) {
      if (steepCount() >= MAX_STEEPS) return;
      var li = document.createElement("li");
      li.className = "steep-item";
      li.innerHTML = '<button type="button" class="steep-orb"><span class="orb" aria-hidden="true"></span><span class="steep-sec"></span></button>' +
        '<div class="steep-edit"><label>Steep <span class="steep-n"></span>, seconds <input type="number" name="steep_seconds" min="1" max="3600" inputmode="numeric"></label>' +
        '<label>What did it taste like? <input type="text" name="steep_note" maxlength="200"></label>' +
        '<button type="button" class="steep-remove">Remove</button></div>';
      li.querySelector("[name=steep_seconds]").value = seconds;
      li.querySelector("[name=steep_note]").value = note || "";
      list.appendChild(li);
      openSteep(li);
      renumber();
      if (!quiet) saveDraft();
    }
    list.addEventListener("click", function (e) {
      var remove = e.target.closest(".steep-remove");
      if (remove) {
        remove.closest(".steep-item").remove();
        var all = items();
        if (all.length) openSteep(all[all.length - 1]);
        renumber(); saveDraft();
        return;
      }
      var orb = e.target.closest(".steep-orb");
      if (orb) {
        var li = orb.closest(".steep-item");
        if (li.classList.contains("is-open")) { li.classList.remove("is-open"); orb.setAttribute("aria-expanded", "false"); }
        else openSteep(li);
      }
    });
    list.addEventListener("input", function (e) { if (e.target.name === "steep_seconds") renumber(); });
    document.getElementById("steep-reset").addEventListener("click", function () {
      if (steepCount() && !window.confirm("Clear all logged steeps?")) return;
      list.innerHTML = "";
      clearTimer();
      renumber(); saveDraft();
    });

    // timer
    function clearTimer() {
      running = false; banked = 0; clearInterval(tick);
      display.textContent = "00:00";
      paintToggle();
    }
    function paintToggle() {
      toggle.textContent = running ? "Stop" : "Start";
      toggle.classList.toggle("is-running", running);
      showStatus();
    }
    function logSteep() {
      var sec = elapsedSec();
      clearTimer();
      addSteep(sec > 0 ? sec : nextSuggested(), "");
    }
    toggle.addEventListener("click", function () {
      if (running) {
        banked += Date.now() - startedAt;
        running = false; clearInterval(tick);
      } else {
        running = true; startedAt = Date.now();
        tick = setInterval(function () { display.textContent = fmt(elapsedSec()); }, 250);
      }
      paintToggle();
    });
    logBtn.addEventListener("click", logSteep);
    addBtn.addEventListener("click", function () { addSteep(nextSuggested(), ""); });

    // steppers
    form.addEventListener("click", function (e) {
      var btn = e.target.closest(".ns-step-btn");
      if (!btn) return;
      var box = btn.closest(".ns-stepper"), input = box.querySelector("input");
      var current = parseFloat(input.value.replace(",", ".")), next;
      if (isNaN(current)) next = Number(box.dataset.start);
      else next = Math.min(Number(box.dataset.max), Math.max(Number(box.dataset.min), current + Number(btn.dataset.dir) * Number(box.dataset.step)));
      input.value = Number(next.toFixed(1));
      delete input.dataset.fromTea;
      refresh(); saveDraft();
    });
    ["grams", "water_ml", "temp_c"].forEach(function (id) {
      document.getElementById(id).addEventListener("input", function () { delete this.dataset.fromTea; });
    });

    // stars: tapping the current rating clears it
    var rated = form.querySelector("[name=rating]:checked");
    function paintStars() {
      var checked = form.querySelector("[name=rating]:checked"), value = checked ? Number(checked.value) : 0;
      form.querySelectorAll(".ns-star").forEach(function (star) {
        star.classList.toggle("on", Number(star.querySelector("input").value) <= value);
      });
    }
    form.addEventListener("click", function (e) {
      if (e.target.name !== "rating") return;
      if (rated === e.target) { e.target.checked = false; rated = null; }
      else rated = e.target;
      paintStars(); refresh(); saveDraft();
    });

    // tea picker (HTMX fills #tea-suggest; we handle the click)
    function showCard(info) {
      cardInfo = info;
      document.getElementById("tea-card-name").textContent = info.name;
      document.getElementById("tea-card-sub").textContent = info.sub || "";
      if (info.swirl) {
        paintCup(document.querySelector("#tea-card-cup .cup"), info.swirl, info.core, info.gloss);
        pvCupBox.dataset.swirl = info.swirl; pvCupBox.dataset.core = info.core;
        paintCup(pvCupBox.querySelector(".cup"), info.swirl, info.core, info.gloss);
        pvCupBox.querySelector(".cup").style.boxShadow = info.shadow;
      }
      teaCard.hidden = false; teaField.hidden = true;
    }
    function unlinkTea() {
      teaId.value = ""; cardInfo = null;
      teaCard.hidden = true; teaField.hidden = false;
      pvCupBox.dataset.swirl = form.dataset.plainSwirl; pvCupBox.dataset.core = form.dataset.plainCore;
      paintCup(pvCupBox.querySelector(".cup"), form.dataset.plainSwirl, form.dataset.plainCore, form.dataset.plainGloss);
      pvCupBox.querySelector(".cup").style.boxShadow = form.dataset.plainShadow;
      refresh();
    }
    suggest.addEventListener("click", function (e) {
      var item = e.target.closest(".suggest-item");
      if (!item) return;
      teaId.value = item.dataset.id; teaName.value = item.dataset.name;
      // parameters follow the tea, unless the person has already set their own
      [["grams", "grams"], ["water_ml", "water"], ["temp_c", "temp"]].forEach(function (pair) {
        var el = document.getElementById(pair[0]), value = item.dataset[pair[1]];
        if (el.value && el.dataset.fromTea !== "1") return;
        el.value = value || "";
        if (value) el.dataset.fromTea = "1"; else delete el.dataset.fromTea;
      });
      suggested = item.dataset.seconds ? item.dataset.seconds.split(",").map(Number) : [];
      suggest.innerHTML = "";
      showCard({ name: item.dataset.name, sub: item.dataset.sub, swirl: item.dataset.swirl, core: item.dataset.core, gloss: item.dataset.gloss, shadow: item.dataset.shadow });
      renumber(); saveDraft();
    });
    teaName.addEventListener("input", function () { if (teaId.value) unlinkTea(); refresh(); });
    document.getElementById("tea-change").addEventListener("click", function () {
      unlinkTea(); teaName.focus(); teaName.select(); saveDraft();
    });
    document.addEventListener("click", function (e) {
      if (!suggest.contains(e.target) && e.target !== teaName) suggest.innerHTML = "";
    });

    var photos = document.getElementById("ns-photos-input");
    if (photos) photos.addEventListener("change", function () {
      if (photos.files.length) document.getElementById("photo-hint").textContent = photos.files.length + (photos.files.length === 1 ? " photo" : " photos") + " ready to upload";
    });

    // "How others will see it"
    function fitValues() {
      form.querySelectorAll(".ns-step-value input").forEach(function (input) {
        input.style.width = (Math.max(1, input.value.length) * 0.82 + 0.1) + "em"; // the unit stays right after the number
      });
    }
    function refresh() {
      fitValues();
      var name = teaName.value.trim();
      document.getElementById("pv-title").textContent = name || "Your tea";
      var bits = [];
      var grams = document.getElementById("grams").value.trim(), water = document.getElementById("water_ml").value.trim(), temp = document.getElementById("temp_c").value.trim();
      var vessel = document.getElementById("ns-vessel").value, n = steepCount();
      if (grams) bits.push(grams + " g");
      if (water) bits.push(water + " ml");
      if (temp) bits.push(temp + " °C");
      if (vessel) bits.push(vessel);
      if (n) bits.push(n + (n === 1 ? " steep" : " steeps"));
      document.getElementById("pv-summary").textContent = bits.join(" · ");
      var orbs = document.getElementById("pv-orbs");
      orbs.textContent = "";
      for (var i = 0; i < n; i++) {
        var dot = document.createElement("span");
        dot.className = "orb"; dot.style.background = orbCss(i);
        orbs.appendChild(dot);
      }
      var checked = form.querySelector("[name=rating]:checked"), rating = checked ? Number(checked.value) : 0;
      document.getElementById("pv-stars").textContent = rating ? "★★★★★".slice(0, rating) + "☆☆☆☆☆".slice(0, 5 - rating) : "";
      var tags = document.getElementById("pv-tags");
      tags.textContent = "";
      form.querySelectorAll("[name=taste_tags]:checked").forEach(function (el) {
        var li = document.createElement("li");
        li.textContent = el.parentNode.querySelector("span").textContent;
        tags.appendChild(li);
      });
      document.getElementById("pv-vis").textContent = document.getElementById("share-feed").checked ? "This entry will appear in the community feed" : "Only you can see this entry";
      var step = Math.min(8, Math.max(1, n));
      paintCup(pvCupBox.querySelector(".cup"), lighten(pvCupBox.dataset.swirl, step), lighten(pvCupBox.dataset.core, step));
    }

    // draft autosave: new sessions only, editing starts from what is saved
    function collect() {
      var data = {};
      form.querySelectorAll("input, textarea, select").forEach(function (el) {
        if (!el.name || el.type === "file" || el.name === "remove_photo" || el.name.indexOf("steep_") === 0) return;
        if (el.type === "hidden" && el.name !== "tea_id") return;
        if (el.name === "taste_tags") { (data.taste_tags = data.taste_tags || []); if (el.checked) data.taste_tags.push(el.value); }
        else if (el.type === "checkbox") data[el.name] = el.checked;
        else if (el.type === "radio") { if (el.checked) data[el.name] = el.value; }
        else data[el.name] = el.value;
      });
      data.steeps = Array.prototype.map.call(items(), function (li) {
        return { seconds: li.querySelector("[name=steep_seconds]").value, note: li.querySelector("[name=steep_note]").value };
      });
      data.card = cardInfo;
      return data;
    }
    function saveDraft() {
      if (editing || hasErrors && !form.dataset.draftReady) return;
      try { localStorage.setItem(draftKey, JSON.stringify(collect())); } catch (e) {}
    }
    function restoreDraft() {
      var raw = null;
      try { raw = localStorage.getItem(draftKey); } catch (e) {}
      if (!raw || editing || hasErrors) return;
      var data; try { data = JSON.parse(raw); } catch (e) { return; }
      if (!data || (!data.tea_name && !(data.steeps || []).length && !data.note)) return;
      if (steepCount() || (teaName.value && teaName.value !== data.tea_name)) return; // the page already has content
      Object.keys(data).forEach(function (name) {
        if (name === "steeps" || name === "card") return;
        form.querySelectorAll('[name="' + name + '"]').forEach(function (el) {
          if (el.type === "hidden" && name !== "tea_id") return;
          if (name === "taste_tags") el.checked = Array.isArray(data[name]) && data[name].indexOf(el.value) >= 0;
          else if (el.type === "checkbox") el.checked = !!data[name];
          else if (el.type === "radio") el.checked = el.value === data[name];
          else el.value = data[name];
        });
      });
      (data.steeps || []).forEach(function (s) { if (s.seconds) addSteep(s.seconds, s.note, true); });
      if (teaId.value && data.card) showCard(data.card);
      rated = form.querySelector("[name=rating]:checked");
      paintStars();
      var hint = document.getElementById("draft-hint"); hint.hidden = false;
      document.getElementById("draft-discard").addEventListener("click", function (e) {
        e.preventDefault(); try { localStorage.removeItem(draftKey); } catch (err) {} location.reload();
      });
    }
    form.addEventListener("input", function () { refresh(); saveDraft(); });
    form.addEventListener("change", function () { refresh(); saveDraft(); });
    var submitting = false;
    form.addEventListener("submit", function (e) {
      if (submitting) { e.preventDefault(); return; } // a double click saves once
      form.querySelectorAll('[type="submit"]').forEach(function (b) { b.disabled = true; });
      document.querySelectorAll('[form="session-form"]').forEach(function (b) { b.disabled = true; });
      if (running || banked) logSteep(); // the running steep counts
      submitting = true;
      saveDraft(); // the draft goes only after the save went through (app.js, on the next page)
      try { if (!editing) sessionStorage.setItem("prolyv:saved-draft", draftKey); } catch (e) {}
    });
    window.addEventListener("beforeunload", function () { if (running && !submitting) saveDraft(); });
    window.addEventListener("pageshow", function () { // Back to this page: the buttons work again
      submitting = false;
      document.querySelectorAll('#session-form [type="submit"], [form="session-form"]').forEach(function (b) { b.disabled = false; });
    });
    // Enter in a one-line field never saves the whole session.
    form.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && e.target.tagName === "INPUT" && e.target.type !== "submit") e.preventDefault();
    });
    document.querySelectorAll(".session-cancel").forEach(function (cancel) {
      cancel.addEventListener("click", function () { if (!editing) { try { localStorage.removeItem(draftKey); } catch (e) {} } });
    });

    if (teaId.value && !teaCard.hidden) {
      cardInfo = { name: document.getElementById("tea-card-name").textContent, sub: document.getElementById("tea-card-sub").textContent, swirl: pvCupBox.dataset.swirl, core: pvCupBox.dataset.core };
    }
    restoreDraft();
    if (!hasErrors && items().length) openSteep(items()[items().length - 1]);
    renumber();
    form.dataset.draftReady = "1";
  }

  function initPage() {
    // Steep by steep: the cup and the line follow the steep you touch
    var steeps = document.getElementById("sp-steeps");
    if (steeps) {
      var box = steeps.querySelector(".sp-steep-cup");
      var now = document.getElementById("sp-steep-now");
      var pick = function (btn) {
        steeps.querySelectorAll(".sp-steep").forEach(function (b) { b.setAttribute("aria-pressed", b === btn ? "true" : "false"); });
        now.textContent = btn.dataset.text;
        var step = Number(btn.dataset.steep);
        paintCup(box.querySelector(".cup"), lighten(box.dataset.swirl, step), lighten(box.dataset.core, step));
      };
      ["click", "mouseover", "focusin"].forEach(function (type) {
        steeps.addEventListener(type, function (e) {
          var btn = e.target.closest(".sp-steep");
          if (btn) pick(btn);
        });
      });
      var first = steeps.querySelector('.sp-steep[aria-pressed="true"]');
      if (first) pick(first);
    }

    // Follow stays on this page. Without JS the form still works and lands on the profile.
    document.addEventListener("submit", function (e) {
      if (!e.target.matches("[data-follow]")) return;
      e.preventDefault();
      var form = e.target, button = form.querySelector("button");
      button.disabled = true;
      fetch(form.action, { method: "POST", body: new FormData(form), credentials: "same-origin", redirect: "manual" }).then(function (response) {
        if (response.type !== "opaqueredirect" && !response.ok) { form.submit(); return; }
        var on = button.getAttribute("aria-pressed") !== "true";
        button.setAttribute("aria-pressed", on ? "true" : "false");
        button.textContent = on ? "Following" : "Follow";
      }).catch(function () { form.submit(); }).then(function () { button.disabled = false; });
    });

    // Reply: starts the comment with @handle, which notifies that person
    document.addEventListener("click", function (e) {
      var reply = e.target.closest("[data-reply]");
      var box = document.getElementById("comment-body");
      if (reply && box) {
        e.preventDefault();
        var mention = "@" + reply.dataset.reply + " ";
        if (box.value.indexOf(mention) !== 0) box.value = mention + box.value;
        box.focus();
        box.setSelectionRange(box.value.length, box.value.length);
        box.scrollIntoView({ block: "center" });
      }
      var back = e.target.closest("[data-back]");
      if (back && document.referrer.indexOf(location.origin + "/") === 0 && history.length > 1) {
        e.preventDefault();
        history.back();
      }
    });
  }
})();
