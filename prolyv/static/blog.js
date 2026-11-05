/* Blog post page: the video opens in place on play, and the cup follows the steep you pick. */
(function () {
  var video = document.querySelector("[data-embed-src]");
  if (video) {
    video.addEventListener("click", function (e) {
      e.preventDefault();
      var frame = document.createElement("iframe");
      frame.src = video.dataset.embedSrc;
      frame.title = "Video";
      frame.allow = "autoplay; encrypted-media; fullscreen; picture-in-picture";
      frame.setAttribute("allowfullscreen", "");
      var box = document.createElement("div");
      box.className = "bp-video bp-video-playing";
      box.appendChild(frame);
      video.replaceWith(box);
    });
  }

  var block = document.querySelector("[data-steeps]");
  if (!block) return;
  var buttons = Array.prototype.slice.call(block.querySelectorAll(".bp-steep"));
  var swirl = block.querySelector(".bp-steep-cup .cup-swirl");
  var core = block.querySelector(".bp-steep-cup .cup-layer");
  var label = block.querySelector("[data-steep-label]");
  function pick(button) {
    buttons.forEach(function (b) { b.setAttribute("aria-pressed", b === button ? "true" : "false"); });
    if (swirl) swirl.style.background = button.dataset.swirl;
    if (core) core.style.background = button.dataset.core;
    if (label) label.textContent = button.dataset.label;
  }
  buttons.forEach(function (button) {
    button.addEventListener("click", function () { pick(button); });
    button.addEventListener("focus", function () { pick(button); });
    button.addEventListener("mouseenter", function () { pick(button); });
  });
  if (buttons[0]) pick(buttons[0]);
})();
