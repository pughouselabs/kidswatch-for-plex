// Copy and share buttons. Without this script the buttons stay hidden and the text and links still work.
(function () {
  "use strict";

  function selectText(el) {
    var range = document.createRange();
    range.selectNodeContents(el);
    var sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
  }

  // Copy the text of an element, falling back to the older selection command.
  function copyFrom(el) {
    function fallback() {
      selectText(el);
      var ok = false;
      try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
      return ok ? Promise.resolve() : Promise.reject(new Error("copy failed"));
    }
    if (navigator.clipboard && window.isSecureContext) {
      return navigator.clipboard.writeText(el.textContent.trim()).catch(fallback);
    }
    return fallback();
  }

  // Swap the button label and announce a message, then put both back.
  function feedback(button) {
    var status = document.getElementById(button.getAttribute("data-copy-status"));
    var label = button.querySelector("[data-copy-label]") || button;
    var idle = label.textContent;
    var timer;
    return function (text, message, ms) {
      label.textContent = text;
      button.classList.toggle("copied", text === "Copied");
      if (status) status.textContent = message;
      clearTimeout(timer);
      timer = setTimeout(function () {
        label.textContent = idle;
        button.classList.remove("copied");
        if (status) status.textContent = "";
      }, ms);
    };
  }

  function copyWithFeedback(el, show, done) {
    return copyFrom(el).then(function () {
      show("Copied", done, 2500);
    }, function () {
      selectText(el);
      show("Selected", "Selected. Press Ctrl+C, or Cmd+C on a Mac, to copy it.", 8000);
    });
  }

  document.querySelectorAll("[data-copy-target]").forEach(function (button) {
    var target = document.getElementById(button.getAttribute("data-copy-target"));
    if (!target) return;
    var show = feedback(button);
    button.addEventListener("click", function () {
      copyWithFeedback(target, show, button.getAttribute("data-copy-done") || "Copied to the clipboard.");
    });
    button.hidden = false;
  });

  // Share buttons open the device's share sheet when there is one, otherwise copy the link.
  document.querySelectorAll("[data-share-url]").forEach(function (button) {
    var link = document.getElementById(button.getAttribute("data-share-fallback"));
    if (!link) return;
    var show = feedback(button);
    var done = "Link copied. Paste it into a text or email.";
    var data = { title: button.getAttribute("data-share-title") || document.title, url: button.getAttribute("data-share-url") };
    button.addEventListener("click", function () {
      if (navigator.share && (!navigator.canShare || navigator.canShare(data))) {
        navigator.share(data).catch(function (err) {
          if (err && err.name === "AbortError") return;
          copyWithFeedback(link, show, done);
        });
        return;
      }
      copyWithFeedback(link, show, done);
    });
    button.hidden = false;
  });
})();
