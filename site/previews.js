// Local Admin preview. No requests, accounts or data are changed.
(function () {
  "use strict";
  var demo = document.querySelector(".admin-demo");
  if (demo) {
    var tablist = demo.querySelector('[role="tablist"]');
    var tabs = Array.from(tablist.querySelectorAll('[role="tab"]'));
    function selectTab(tab, focus) {
      tabs.forEach(function (item) {
        var selected = item === tab;
        item.setAttribute("aria-selected", String(selected));
        item.tabIndex = selected ? 0 : -1;
        document.getElementById(item.getAttribute("aria-controls")).hidden = !selected;
      });
      if (focus) tab.focus();
    }
    tabs.forEach(function (tab, index) {
      var panel = document.getElementById(tab.getAttribute("aria-controls"));
      panel.setAttribute("role", "tabpanel");
      panel.setAttribute("aria-labelledby", tab.id);
      panel.tabIndex = 0;
      tab.addEventListener("click", function () { selectTab(tab, false); });
      tab.addEventListener("keydown", function (event) {
        var next;
        if (event.key === "ArrowRight") next = (index + 1) % tabs.length;
        if (event.key === "ArrowLeft") next = (index + tabs.length - 1) % tabs.length;
        if (event.key === "Home") next = 0;
        if (event.key === "End") next = tabs.length - 1;
        if (next === undefined) return;
        event.preventDefault();
        selectTab(tabs[next], true);
      });
    });
    selectTab(tabs[0], false);
    tablist.hidden = false;
    var approve = demo.querySelector("[data-demo-approve]");
    approve.hidden = false;
    document.querySelector("[data-demo-instruction]").hidden = false;
    approve.addEventListener("click", function () {
      demo.querySelectorAll("[data-child-status]").forEach(function (status) {
        status.textContent = "Active";
        status.classList.replace("owner-waiting", "owner-ok");
      });
      demo.querySelector('[data-stat="open"]').textContent = "0";
      demo.querySelector('[data-stat="waiting"]').textContent = "0";
      demo.querySelector('[data-stat="active"]').textContent = "3";
      demo.querySelector("[data-approval-log]").hidden = false;
      demo.querySelector("#requests-heading").textContent = "Recently answered";
      demo.querySelector("[data-demo-hint]").textContent = "Approved by you · Just now";
      demo.querySelector("[data-demo-status]").textContent = "Rowan is approved. Sarah can manage the everyday rules.";
      approve.textContent = "Approved";
      approve.disabled = true;
    });
  }

  // Reveal the text before copy.js runs so its selection fallback can copy it too.
  var copyPrompt = document.querySelector('[data-copy-target="ai-prompt"]');
  if (copyPrompt) {
    copyPrompt.addEventListener("click", function () {
      document.querySelector(".prompt-disclosure").open = true;
    }, true);
  }

  // Deep links also work when their destination is inside a closed disclosure.
  function revealHash() {
    var target;
    try { target = document.getElementById(decodeURIComponent(location.hash.slice(1))); }
    catch (error) { return; }
    if (!target) return;
    var ancestor = target;
    while (ancestor) {
      if (ancestor.tagName === "DETAILS") ancestor.open = true;
      ancestor = ancestor.parentElement;
    }
    target.scrollIntoView();
  }
  window.addEventListener("hashchange", revealHash);
  if (location.hash) revealHash();
})();

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
