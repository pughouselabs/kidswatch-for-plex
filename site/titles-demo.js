// Shared title demo. Without this script each page stays a still picture of the Kids level.
//
// Example titles and their official US ratings (MPA for movies, TV Parental Guidelines for shows),
// checked 2026-10-24 against each title's Rotten Tomatoes page:
//   Toy Story (1995)        G      https://www.rottentomatoes.com/m/toy_story
//   Sesame Street           TV-Y   https://www.rottentomatoes.com/tv/sesame_street
//   Frozen (2013)           PG     https://www.rottentomatoes.com/m/frozen_2013
//   SpongeBob SquarePants   TV-Y7  https://www.rottentomatoes.com/tv/spongebob_squarepants
//   Jurassic Park (1993)    PG-13  https://www.rottentomatoes.com/m/jurassic_park
//   Wednesday               TV-14  https://www.rottentomatoes.com/tv/wednesday
// The tiles are plain designed cards: no posters, cover art or logos.
(function () {
  "use strict";

  // Same allow-lists as AGE_LEVELS in app/rules.py. Anything not listed (R, TV-MA, unrated) stays blocked.
  var LEVELS = {
    little: {
      label: "Little kids",
      movies: ["G", "TV-Y", "TV-G"],
      tv: ["TV-Y", "TV-G"]
    },
    kids: {
      label: "Kids",
      movies: ["G", "PG", "TV-Y", "TV-Y7", "TV-Y7-FV", "TV-G", "TV-PG"],
      tv: ["TV-Y", "TV-Y7", "TV-Y7-FV", "TV-G", "TV-PG"]
    },
    teens: {
      label: "Teens",
      movies: ["G", "PG", "PG-13", "TV-Y", "TV-Y7", "TV-Y7-FV", "TV-G", "TV-PG", "TV-14"],
      tv: ["TV-Y", "TV-Y7", "TV-Y7-FV", "TV-G", "TV-PG", "TV-14"]
    }
  };

  function ratingAllowed(level, type, rating) {
    var allowed = type === "show" ? level.tv : level.movies;
    return Boolean(rating) && allowed.indexOf(rating) !== -1;
  }

  // Name a list of titles the way a person would say it: "A", "A and B", "A, B and C".
  function joinTitles(names) {
    if (names.length < 2) return names.join("");
    return names.slice(0, -1).join(", ") + " and " + names[names.length - 1];
  }

  document.querySelectorAll("[data-level-demo]").forEach(initDemo);

  function initDemo(demo) {
    var seg = demo.querySelector(".seg");
    var panel = demo.querySelector(".tiles").parentElement;
    var help = demo.querySelector(".demo-help");
    var list = panel.querySelector(".tiles");
    var status = demo.querySelector("[data-level-status]");
    var tiles = Array.prototype.slice.call(list.querySelectorAll("li[data-rating]"));
    var onlyAllowed = demo.querySelector("[data-only-allowed]");
    var selectedLevel = "kids";
    function defaultAllowed(tile) {
      return !onlyAllowed.checked && ratingAllowed(LEVELS[selectedLevel],
        tile.getAttribute("data-type"), tile.getAttribute("data-rating"));
    }

    function withinLimit(tile) {
      return ratingAllowed(LEVELS.teens, tile.getAttribute("data-type"), tile.getAttribute("data-rating"));
    }

    function isAllowed(tile) {
      var choice = tile.getAttribute("data-choice");
      return withinLimit(tile) && choice !== "block" && (choice === "allow" || defaultAllowed(tile));
    }

    // One native button per tile supports taps, clicks, Enter and Space, including the mark.
    tiles.forEach(function (tile) {
      var button = document.createElement("button");
      button.type = "button";
      button.className = "demo-title";
      button.setAttribute("aria-describedby", help.id);
      while (tile.firstChild) button.appendChild(tile.firstChild);
      // Keep the visible title, rating and status separate in the button's name.
      button.querySelectorAll(".tile-title, .tile-rating, .tile-meta").forEach(function (label) {
        label.after(document.createTextNode(" "));
      });
      var badge = document.createElement("span");
      badge.className = "demo-choice";
      badge.hidden = true;
      button.appendChild(badge);
      var limit = document.createElement("span");
      limit.className = "demo-limit";
      limit.textContent = "Above the allow limit";
      limit.hidden = true;
      button.appendChild(limit);
      tile.appendChild(button);
      tile.setAttribute("data-choice", "");
      button.addEventListener("click", function () {
        var name = tile.querySelector(".tile-title").textContent;
        var allow = !isAllowed(tile);
        if (allow && !withinLimit(tile)) {
          tile.setAttribute("data-limit-notice", "true");
          render(true, name + ": Above the allow limit. ");
          return;
        }
        tile.removeAttribute("data-limit-notice");
        // Returning to the default needs no override. In picked mode, the default is blocked.
        var choice = allow === defaultAllowed(tile) ? "" : (allow ? "allow" : "block");
        tile.setAttribute("data-choice", choice);
        render(true, name + ": " + (allow ? "Allowed" : "Blocked") + ". ");
      });
    });
    demo.querySelector("[data-title-controls]").hidden = false;

    // Swap the still picture's labels for real tabs.
    var tabs = Array.prototype.map.call(seg.querySelectorAll("[data-level]"), function (span) {
      var key = span.getAttribute("data-level");
      var tab = document.createElement("button");
      tab.type = "button";
      tab.id = panel.id + "-tab-" + key;
      tab.setAttribute("role", "tab");
      tab.setAttribute("aria-controls", panel.id);
      tab.setAttribute("data-level", key);
      tab.textContent = span.textContent;
      span.replaceWith(tab);
      return tab;
    });
    seg.setAttribute("role", "tablist");
    seg.setAttribute("aria-label", "Age level");
    seg.removeAttribute("aria-hidden");
    panel.setAttribute("role", "tabpanel");

    function render(announce, change) {
      var level = LEVELS[selectedLevel];
      tabs.forEach(function (tab) {
        var on = tab.getAttribute("data-level") === selectedLevel;
        tab.classList.toggle("on", on);
        tab.setAttribute("aria-selected", on ? "true" : "false");
        tab.tabIndex = on ? 0 : -1;
        if (on) panel.setAttribute("aria-labelledby", tab.id);
      });
      var blocked = [];
      tiles.forEach(function (tile) {
        var rating = tile.getAttribute("data-rating");
        var choice = tile.getAttribute("data-choice");
        // Match title_visible in app/rules.py: the ceiling wins, then block, then allow.
        var ok = isAllowed(tile);
        var overLimit = tile.hasAttribute("data-limit-notice") && !withinLimit(tile);
        var choiceLabel = choice === "allow" ? "You allowed" : choice === "block" ? "You blocked" : "Rating default";
        var name = tile.querySelector(".tile-title").textContent;
        tile.classList.toggle("off", !ok);
        var mark = tile.querySelector(".mark");
        var symbol = mark.querySelector("use");
        if (symbol) symbol.setAttribute("href", ok ? "#i-check" : "#i-x");
        else mark.textContent = ok ? "✓" : "×";
        tile.querySelector("[data-status]").textContent = ok ? "Allowed" : "Blocked";
        var badge = tile.querySelector(".demo-choice");
        badge.textContent = choiceLabel;
        badge.hidden = !choice;
        badge.classList.toggle("is-blocked", choice === "block");
        tile.querySelector(".demo-limit").hidden = !overLimit;
        tile.querySelector("button").setAttribute("aria-pressed", ok ? "true" : "false");
        tile.querySelector("button").setAttribute("aria-label", name + ", " + (rating || "unrated") +
          ". " + (tile.getAttribute("data-type") === "show" ? "Show" : "Movie") + " · " +
          (ok ? "Allowed" : "Blocked") + ". " + choiceLabel +
          (overLimit ? ". Above the allow limit" : "") + ". " + (ok ? "Block title." : "Allow title."));
        if (!ok) blocked.push(name + (overLimit ? " (Above the allow limit)" : ""));
      });
      list.setAttribute("aria-label", "Example titles at the " + level.label + " level" +
        (onlyAllowed.checked ? ", Only titles I allow" : ""));
      if (announce) {
        var allowed = tiles.length - blocked.length;
        status.textContent = (change || "") + level.label +
          (onlyAllowed.checked ? ", Only titles I allow" : "") + ": " + allowed + " of " + tiles.length + " titles allowed." +
          (blocked.length ? " " + joinTitles(blocked) + (blocked.length === 1 ? " is" : " are") + " blocked." : "");
      }
    }

    function select(key, announce) {
      selectedLevel = key;
      render(announce);
    }

    onlyAllowed.addEventListener("change", function () {
      render(true, "Only titles I allow " + (onlyAllowed.checked ? "on" : "off") + ". ");
    });

    tabs.forEach(function (tab, i) {
      tab.addEventListener("click", function () {
        select(tab.getAttribute("data-level"), true);
      });
      // Arrow keys move between tabs and select them; Home and End jump to the ends.
      tab.addEventListener("keydown", function (event) {
        var next = null;
        if (event.key === "ArrowRight") next = (i + 1) % tabs.length;
        else if (event.key === "ArrowLeft") next = (i - 1 + tabs.length) % tabs.length;
        else if (event.key === "Home") next = 0;
        else if (event.key === "End") next = tabs.length - 1;
        if (next === null) return;
        event.preventDefault();
        tabs[next].focus();
        select(tabs[next].getAttribute("data-level"), true);
      });
    });

    select("kids", false);
  }
})();
