// A local example of the app's quick controls. Nothing is sent or saved.
(function () {
  "use strict";
  var demo = document.querySelector("[data-parent-demo]");
  if (!demo) return;

  var state = { bonus: 0, later: 0, pause: "" };
  var previous = null;
  var pause = demo.querySelector("[data-demo-pause]");
  var resume = demo.querySelector("[data-demo-resume]");
  var add = demo.querySelector("[data-demo-add]");
  var toast = demo.querySelector("[data-demo-toast]");
  var notice = demo.querySelector("[data-demo-notice]");
  var undo = demo.querySelector("[data-demo-undo]");

  function minutes(value) {
    var hours = Math.floor(value / 60);
    var rest = value % 60;
    return (hours ? hours + " hr" : "") + (hours && rest ? " " : "") + (rest ? rest + " min" : "");
  }

  function pauseText() {
    if (state.pause === "30") return "Until 6:30 PM";
    if (state.pause === "60") return "Until 7:00 PM";
    if (state.pause === "tomorrow") return "Until tomorrow";
    return "Until you resume";
  }

  function render() {
    var limit = 120 + state.bonus;
    demo.querySelector("[data-demo-limit]").textContent = "of " + minutes(limit);
    demo.querySelector("[data-demo-meter]").max = limit;
    var end = "8:" + (state.later ? "30" : "00") + " PM";
    demo.querySelector("[data-demo-hours]").textContent = minutes(limit - 47) + " left · Ends " + end + (state.bonus ? " · includes " + state.bonus + " min extra" : "");
    var badge = demo.querySelector("[data-demo-badge]");
    badge.textContent = state.pause ? "Paused" : "Watching";
    badge.classList.toggle("live", !state.pause);
    badge.classList.toggle("pa-paused", !!state.pause);
    demo.querySelector("[data-demo-state]").textContent = state.pause ? pauseText() : "Maple Club on the TV";
    pause.hidden = !!state.pause;
    resume.hidden = !state.pause;
    // Match the app: bedtime extensions are relative to the usual bedtime.
    demo.querySelector('[data-demo-action="later:30"]').disabled = state.later === 30;
  }

  function feedback(message) {
    toast.hidden = false;
    notice.textContent = message;
    undo.hidden = !previous;
  }

  demo.querySelectorAll("[data-demo-action]").forEach(function (button) {
    button.disabled = false;
    button.addEventListener("click", function () {
      var action = button.getAttribute("data-demo-action").split(":");
      previous = { bonus: state.bonus, later: state.later, pause: state.pause };
      var message;
      if (action[0] === "pause") {
        state.pause = action[1];
        message = "Rowan is paused. " + pauseText() + ".";
      } else if (action[0] === "resume") {
        state.pause = "";
        message = "Rowan can watch again.";
      } else if (action[0] === "bonus") {
        state.bonus += Number(action[1]);
        message = action[1] + " minutes added for today.";
      } else if (action[0] === "later") {
        state.later = 30;
        message = "Bedtime is 8:30 PM tonight.";
      }
      pause.open = false;
      add.open = false;
      render();
      feedback(message);
      if (action[0] === "pause") resume.focus();
      else if (action[0] === "resume") pause.querySelector("summary").focus();
      else add.querySelector("summary").focus();
    });
  });

  undo.addEventListener("click", function () {
    if (!previous) return;
    state = previous;
    previous = null;
    render();
    feedback("Undone. Your previous settings are back.");
    (state.pause ? resume : pause.querySelector("summary")).focus();
  });

  // Keep one menu open, dismiss outside the preview, and support Escape.
  [pause, add].forEach(function (menu) {
    menu.addEventListener("toggle", function () {
      if (menu.open) (menu === pause ? add : pause).open = false;
    });
  });
  document.addEventListener("click", function (event) {
    if (!event.target.closest(".pa-menu")) {
      pause.open = false;
      add.open = false;
    }
  });
  demo.addEventListener("keydown", function (event) {
    if (event.key !== "Escape") return;
    var menu = event.target.closest(".pa-menu[open]");
    if (!menu) return;
    menu.open = false;
    menu.querySelector("summary").focus();
  });
})();
