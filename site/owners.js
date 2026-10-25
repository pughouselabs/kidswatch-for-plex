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
