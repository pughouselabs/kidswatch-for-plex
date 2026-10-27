"use strict";
// Progressive enhancements. Every page works without this file; it only adds live updates and smoother controls.

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

async function getJSON(url) {
  const r = await fetch(url, { credentials: "same-origin" });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

// ----- missing posters get a clean placeholder -----
document.addEventListener("error", (e) => {
  const img = e.target;
  if (img.tagName === "IMG" && img.src.includes("/poster/") && !img.dataset.fallback) {
    img.dataset.fallback = "1";
    img.src = "/static/poster-missing.svg";
  }
}, true);

// ----- confirm dialog (styled, instead of the browser's own) -----
function askConfirm(text, yes = "Continue") {
  const dlg = $("[data-confirm]");
  if (!dlg || typeof dlg.showModal !== "function") return Promise.resolve(true);
  $("[data-confirm-text]", dlg).textContent = text;
  $("[data-confirm-yes]", dlg).textContent = yes;
  return new Promise((resolve) => {
    dlg.addEventListener("close", () => resolve(dlg.returnValue === "yes"), { once: true });
    dlg.returnValue = "";
    dlg.showModal();
  });
}

async function postForm(url, data) {
  const r = await fetch(url, { method: "POST", body: new URLSearchParams(data), credentials: "same-origin",
                               headers: { "x-requested-with": "fetch" } });
  if (!r.ok) {
    let msg = "That didn't save. Try again.";
    try { msg = (await r.json()).error || msg; } catch (x) { /* not JSON */ }
    throw new Error(msg);
  }
  return r.json();
}

// ----- schedule presets fill the form; the parent still presses Save -----
$$("[data-preset]").forEach((btn) => btn.addEventListener("click", () => {
  const form = btn.closest("form");
  const p = JSON.parse(btn.dataset.preset);
  for (const [name, value] of Object.entries(p)) {
    const field = form.elements[name];
    if (field) field.value = value;
  }
  ["weekday", "weekend"].forEach((d) => {
    const box = form.elements[`any_time_${d}`];
    if (box) box.checked = !!p.any;
  });
  form.dispatchEvent(new Event("change"));
  toast(`${btn.textContent.trim()} filled in. Press Save schedule to keep it.`);
}));

// ----- forms: no double submits -----
document.addEventListener("submit", (e) => {
  const form = e.target;
  if (form.method === "dialog" || e.defaultPrevented) return;
  setTimeout(() => $$("button", form).forEach((b) => { b.disabled = true; }), 0);
  form.setAttribute("aria-busy", "true");
});
window.addEventListener("pageshow", () => {
  $$("form[aria-busy] button").forEach((b) => { b.disabled = false; });
  $$("form[data-confirmed]").forEach((f) => { delete f.dataset.confirmed; });  // back button: ask again next time
});

// ----- Ctrl/Cmd+K jumps to title search without a single-character shortcut -----
document.addEventListener("keydown", (e) => {
  const search = $("[data-search]");
  if (e.key.toLowerCase() === "k" && (e.ctrlKey || e.metaKey) && search) {
    e.preventDefault();
    search.focus();
  }
});

// ----- warn before leaving a form with unsaved changes -----
$$("[data-dirty-guard]").forEach((form) => {
  let dirty = false;
  form.addEventListener("input", () => { dirty = true; });
  form.addEventListener("change", () => { dirty = true; });
  form.addEventListener("submit", () => { dirty = false; });
  window.addEventListener("beforeunload", (e) => { if (dirty) { e.preventDefault(); e.returnValue = ""; } });
});

// ----- persistent server errors and accessible notifications -----
// Restore only ordinary parent forms after a validation redirect. Never retain sign-in codes or CSRF values.
const draftKey = "kidswatch-form-draft";
let failedForm = null;
try {
  const draft = JSON.parse(sessionStorage.getItem(draftKey) || "null");
  sessionStorage.removeItem(draftKey);
  if ($("[data-error-summary]") && draft) {
    const form = $$("form").find((f) => f.getAttribute("action") === draft.action);
    failedForm = form;
    if (form) for (const field of form.elements) {
      if (!(field.name in draft.values)) continue;
      const values = draft.values[field.name];
      if (field.type === "checkbox" || field.type === "radio") field.checked = values.includes(field.value);
      else if (!/^(submit|button|hidden)$/.test(field.type)) field.value = values[0] || "";
    }
  }
} catch (e) { /* storage may be disabled; server-rendered errors still work */ }
document.addEventListener("submit", (e) => {
  const form = e.target;
  if (form.method === "dialog" || form.querySelector('[name="code"]') || e.defaultPrevented) return;
  try {
    const values = {};
    for (const field of form.elements) {
      if (!field.name || /^(hidden|submit|button|password)$/.test(field.type)) continue;
      if (!(field.name in values)) values[field.name] = [];
      if (!/^(checkbox|radio)$/.test(field.type) || field.checked) values[field.name].push(field.value);
    }
    sessionStorage.setItem(draftKey, JSON.stringify({ action: form.getAttribute("action"), values }));
  } catch (e) { /* optional convenience */ }
});
const errorSummary = $("[data-error-summary]");
if (errorSummary) {
  // A service failure belongs to the submitted action, not every request on the page.
  if (failedForm && /^\/admin\/requests\//.test(failedForm.getAttribute("action"))) {
    const message = document.createElement("p");
    message.className = "form-error";
    message.id = "request-action-error";
    message.dataset.fieldError = "";
    message.textContent = $("li", errorSummary).textContent;
    failedForm.prepend(message);
    failedForm.setAttribute("aria-describedby", message.id);
  }
  $$("[data-field-error]").forEach((message) => {
    const field = $$('[aria-describedby]').find((f) => f.getAttribute("aria-describedby").split(/\s+/).includes(message.id));
    if (!field) return;
    const details = field.closest("details");
    if (details) details.open = true;
    const item = $$("li", errorSummary).find((li) => li.textContent.trim() === message.textContent.trim());
    if (item && field.id && !$("a", item)) {
      const link = document.createElement("a");
      link.href = `#${field.id}`;
      link.textContent = item.textContent;
      link.addEventListener("click", (e) => { e.preventDefault(); field.focus(); field.scrollIntoView({ block: "center" }); });
      item.replaceChildren(link);
    }
  });
  errorSummary.focus();
  // A redirect fragment can take focus after deferred scripts run. Reapply it after navigation finishes.
  window.addEventListener("pageshow", () => setTimeout(() => errorSummary.focus(), 0));
}

function announce(text, error = false) {
  const region = $(error ? "#error-announcer" : "#status-announcer");
  if (!region) return;
  region.textContent = "";
  setTimeout(() => { region.textContent = text; }, 100);
}
function dismiss(notification) {
  if (notification.contains(document.activeElement)) {
    const origin = notification.returnFocus;
    (origin?.isConnected && origin !== document.body && !origin.disabled && !origin.hidden ? origin : $("#main"))?.focus();
  }
  notification.classList.add("hide");
  setTimeout(() => {
    notification.remove();
    const link = $("[data-notification-link]");
    if (link) link.hidden = !$(".toast");
  }, 300);
}
function prepareToast(t) {
  t.returnFocus = document.activeElement;
  $("[data-dismiss]", t)?.addEventListener("click", () => dismiss(t));
  const action = $(".toast-action", t);
  const error = t.classList.contains("error");
  // Errors and Undo have no deadline. Ordinary updates pause while being read or focused.
  if (!error && !action) {
    let timer;
    const pause = () => clearTimeout(timer);
    const resume = () => {
      pause();
      if (!t.matches(":hover, :focus-within")) timer = setTimeout(() => dismiss(t), 6000);
    };
    t.addEventListener("mouseenter", pause);
    t.addEventListener("mouseleave", resume);
    t.addEventListener("focusin", pause);
    t.addEventListener("focusout", resume);
    resume();
  }
  if (action) {
    const hint = document.createElement("span");
    hint.className = "visually-hidden";
    hint.textContent = "Press Alt+Shift+U to reach Undo.";
    t.append(hint);
  }
  const link = $("[data-notification-link]");
  if (link) link.hidden = false;
  announce(t.textContent.trim(), error);
}
$$(".toast").forEach(prepareToast);
document.addEventListener("keydown", (e) => {
  if (e.altKey && e.shiftKey && e.key.toLowerCase() === "u") {
    const actions = $$(".toast:not(.hide) .toast-action");
    if (actions.length) { e.preventDefault(); actions[actions.length - 1].focus(); }
  }
});
function toast(text, error = false, action = null) {
  const box = $(".toasts");
  if (!box) return;
  const t = document.createElement("div");
  t.className = "toast" + (error ? " error" : "");
  const span = document.createElement("span");
  span.textContent = text;
  t.append(span);
  if (action) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "toast-action";
    b.textContent = action.label;
    b.addEventListener("click", () => { dismiss(t); action.run(); });
    t.append(b);
  }
  const close = document.createElement("button");
  close.type = "button";
  close.textContent = "×";
  close.setAttribute("aria-label", "Dismiss");
  close.dataset.dismiss = "";
  t.append(close);
  box.append(t);
  prepareToast(t);
  return t;
}

// ----- menus: one open at a time, close on outside click or Escape -----
document.addEventListener("click", (e) => {
  $$("details.menu[open]").forEach((m) => { if (!m.contains(e.target)) m.removeAttribute("open"); });
});
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  $$("details.menu[open]").forEach((m) => {
    const inside = m.contains(document.activeElement);
    m.removeAttribute("open");
    if (inside) $("summary", m).focus();  // keyboard users land back on the menu button
  });
});
$$("details.menu").forEach((m) => m.addEventListener("toggle", () => {
  if (m.open) $$("details.menu[open]").forEach((o) => { if (o !== m) o.removeAttribute("open"); });
}));

// ----- live "today" panels -----
const STATE_BADGE = {
  watching: ["live", "Watching"], paused: ["warn", "Paused"], hours: ["info", "Closed"],
  limit: ["stop", "Time's up"], idle: ["plain", "Idle"],
};
function paintToday(box, st) {
  const set = (f, v) => { const n = $(`[data-field="${f}"]`, box); if (n) n.textContent = v; };
  set("used_text", st.used_text);
  set("of_limit", st.of_limit);
  set("summary_line", st.summary_line + (st.bonus ? ` · includes ${st.bonus} min extra` : ""));
  const bar = $('[data-field="percent"]', box);
  if (bar) { bar.value = st.percent; bar.classList.toggle("over", st.over); }
  const state = $('[data-field="state"]', box);
  if (state && state.dataset.state !== st.state) {
    // pausing or resuming changes the buttons too, so reload when the state flips between paused and not
    if ((state.dataset.state === "paused") !== (st.state === "paused")) {
      if (document.activeElement !== document.body || $("dialog[open], details[open]")) return;
      location.reload(); return;
    }
    state.dataset.state = st.state;
  }
  const [cls, label] = STATE_BADGE[st.state] || ["plain", ""];
  const badge = $("[data-state-badge]", box);
  if (badge) { badge.className = `badge ${cls}`; badge.textContent = label; }
  const text = $("[data-state-text]", box);
  if (text) text.textContent = st.state_text;
  const add = $("[data-limit-add]", box);
  if (add) add.hidden = st.state !== "limit";
  const later = $("[data-later-add]", box);
  if (later) later.hidden = !(st.state === "hours" && st.evening);
}
$$(".today[data-status-url]").forEach((box) => {
  setInterval(async () => {
    if (document.hidden || $("details.menu[open]")) return;
    try { paintToday(box, await getJSON(box.dataset.statusUrl)); } catch (e) { /* try again next tick */ }
  }, 15000);
});

// ----- "Updating Plex" lines -----
$$("[data-sync-text]").forEach((line) => {
  setInterval(async () => {
    if (document.hidden) return;
    try { line.textContent = (await getJSON(line.dataset.statusUrl)).sync_text || ""; } catch (e) { /* ignore */ }
  }, 4000);
});

// ----- wait for the child's account to connect -----
$$("[data-wait-url]").forEach((box) => {
  setInterval(async () => {
    if (document.hidden) return;
    try {
      const st = await getJSON(box.dataset.waitUrl);
      if (st.status !== box.dataset.waitStatus || (st.accepted ? "1" : "0") !== box.dataset.waitAccepted) location.reload();
    } catch (e) { /* ignore */ }
  }, 4000);
});

// ----- copy buttons -----
$$("[data-copy]").forEach((btn) => {
  btn.hidden = false;
  btn.addEventListener("click", async () => {
    const input = $(btn.dataset.copy);
    if (!input) return;
    input.focus();
    input.select();
    let copied = false;
    try {
      await navigator.clipboard.writeText(input.value);
      copied = true;
    } catch (e) {
      try { copied = document.execCommand("copy"); } catch (x) { /* keep the selected text for manual copying */ }
    }
    if (copied) {
      btn.focus();
      toast(btn.dataset.copyLabel || btn.dataset.copyMessage || "Link copied");
    } else {
      toast("Select and copy the text above.", true);
    }
  });
});

// ----- admin switches submit right away -----
$$("[data-autosubmit]").forEach((sw) => sw.addEventListener("change", () => {
  try { sessionStorage.setItem("kidswatch-switch-focus", sw.id); } catch (e) { /* optional focus restoration */ }
  sw.form.requestSubmit();
}));
try {
  const id = sessionStorage.getItem("kidswatch-switch-focus");
  sessionStorage.removeItem("kidswatch-switch-focus");
  if (id && !errorSummary) document.getElementById(id)?.focus();
} catch (e) { /* storage may be disabled */ }

// ----- movies & shows grid -----
const grid = $("[data-grid]");
if (grid) {
  const tiles = $$(".tile", grid);
  const byKey = new Map(tiles.map((t) => [t.dataset.key, t]));
  const search = $("[data-search]");
  const state = $("[data-state]");
  const count = $("[data-count]");
  const empty = $("[data-empty]");
  const sheet = $("[data-sheet]");
  let type = "all";
  let sheetKey = null;
  let batchUndo = null;
  let mutation = 0;
  const resetLabel = grid.dataset.resetLabel;
  const titleName = (t) => $(".tile-title", t).textContent;
  const invalidateBatchUndo = () => {
    mutation += 1;
    if (batchUndo) dismiss(batchUndo);
    batchUndo = null;
  };

  const visible = (t) => (t.dataset.choice === "block" ? false : t.dataset.choice === "allow" ? true : t.dataset.base === "1");

  const label = (t) => (visible(t) ? "Block" : "Allow");
  const why = (t) => (t.dataset.choice === "allow" ? "you allowed it" : t.dataset.choice === "block" ? "you blocked it"
    : t.dataset.base === "1" ? "it fits the age level" : "it's above the age level");
  const paintSheet = (t) => {
    $("[data-sheet-state]", sheet).textContent = (visible(t) ? "Can watch: " : "Hidden: ") + why(t) + ".";
    $("[data-sheet-toggle]", sheet).textContent = label(t);
    $("[data-sheet-toggle]", sheet).setAttribute("aria-label", `${label(t)} ${titleName(t)} for ${grid.dataset.child}`);
    $("[data-sheet-reset]", sheet).setAttribute("aria-label", `Reset pick for ${titleName(t)} to ${resetLabel}`);
    $("[data-sheet-reset]", sheet).hidden = !t.dataset.choice;
  };

  const paint = (t) => {
    const on = visible(t);
    t.classList.toggle("on", on);
    t.classList.toggle("off", !on);
    $("[data-title-state]", t).textContent = on ? "Can watch" : "Hidden";
    $("[data-reset]", t).hidden = !t.dataset.choice;
    $("[data-toggle]", t).setAttribute("aria-label", `${label(t)} ${titleName(t)} for ${grid.dataset.child}`);
    if (sheet && sheet.open && sheetKey === t.dataset.key) paintSheet(t);
  };

  const filter = () => {
    const q = search.value.trim().toLowerCase();
    const byRating = !!q && tiles.some((t) => t.dataset.rating === q);  // "PG-13" means that rating, not a title search
    const want = state.value;
    let shown = 0;
    for (const t of tiles) {
      const ok = (type === "all" || t.dataset.type === type)
        && (!q || (byRating ? t.dataset.rating === q : t.dataset.title.includes(q) || t.dataset.genres.includes(q)))
        && (want === "all" || (want === "visible" && visible(t)) || (want === "hidden" && !visible(t))
            || (want === "new" && t.dataset.new === "1") || want === t.dataset.choice);
      t.hidden = !ok;
      if (ok) shown += 1;
    }
    const total = tiles.filter(visible).length;
    count.textContent = `${total} of ${tiles.length} can watch` + (shown !== tiles.length ? ` · showing ${shown}` : "");
    empty.hidden = shown !== 0;
    const ask = $("[data-ask-link]");
    if (ask) {
      ask.hidden = !(shown === 0 && q);
      if (q && "askExternal" in ask.dataset) {  // search the owner's request site in a new tab
        const url = new URL(ask.href);
        url.searchParams.set("query", search.value.trim());
        ask.href = url.href;
        $("[data-ask-text]", ask).textContent = `Request "${search.value.trim()}"`;
      } else if (q) {
        const url = new URL(ask.href, location.href);
        url.searchParams.set("message", `Could you add "${search.value.trim()}"?`);
        ask.href = url.pathname + url.search + "#new";
        $("[data-ask-text]", ask).textContent = `Ask to add "${search.value.trim()}"`;
      }
    }
  };

  const setChoice = async (t, next) => {
    const before = t.dataset.choice;
    if (before === next) return;
    invalidateBatchUndo();
    t.dataset.choice = next;
    paint(t);
    filter();
    try {
      await postForm(grid.dataset.post, { csrf: grid.dataset.csrf, key: t.dataset.key, kind: next });
    } catch (err) {
      t.dataset.choice = before;
      paint(t);
      filter();
      toast(err.message, true);
    }
  };
  // flip what the child can see; if the age level already gives that answer, just clear the parent's pick
  const toggle = (t) => {
    const want = !visible(t);
    const base = t.dataset.base === "1";
    setChoice(t, base === want ? "" : (want ? "allow" : "block"));
  };

  const openSheet = async (t) => {
    if (!sheet || typeof sheet.showModal !== "function") return;
    sheetKey = t.dataset.key;
    $("[data-sheet-img]", sheet).src = `/poster/${t.dataset.key}`;
    $("[data-sheet-title]", sheet).textContent = $(".tile-title", t).textContent;
    $("[data-sheet-meta]", sheet).textContent = $(".tile-meta", t).textContent;
    $("[data-sheet-summary]", sheet).textContent = "Loading...";
    $("[data-sheet-genres]", sheet).hidden = true;
    paintSheet(t);
    sheet.showModal();
    try {
      const d = await getJSON(`${grid.dataset.detail}${t.dataset.key}.json`);
      if (sheetKey !== t.dataset.key) return;
      $("[data-sheet-meta]", sheet).textContent = [d.year, d.rating, d.type, d.length].filter(Boolean).join(" · ");
      // genres are chips: one tap shows every title in that genre
      const box = $("[data-sheet-genres]", sheet);
      box.replaceChildren(...(d.genres ? d.genres.split(", ") : []).map((g) => {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "chip";
        b.textContent = g;
        b.addEventListener("click", () => {
          search.value = g.toLowerCase();
          search.dispatchEvent(new Event("input", { bubbles: true }));
          sheet.close();
        });
        return b;
      }));
      box.hidden = !box.children.length;
      $("[data-sheet-summary]", sheet).textContent = d.summary || "No description available.";
    } catch (e) {
      $("[data-sheet-summary]", sheet).textContent = "Couldn't load the description.";
    }
  };
  if (sheet) {
    $("[data-close]", sheet).addEventListener("click", () => sheet.close());
    sheet.addEventListener("click", (e) => { if (e.target === sheet) sheet.close(); });
    $("[data-sheet-toggle]", sheet).addEventListener("click", () => { const t = byKey.get(sheetKey); if (t) toggle(t); });
    $("[data-sheet-reset]", sheet).addEventListener("click", () => { const t = byKey.get(sheetKey); if (t) setChoice(t, ""); });
  }

  grid.addEventListener("click", (e) => {
    const t = e.target.closest(".tile");
    if (!t) return;
    if (e.target.closest("[data-toggle]")) toggle(t);
    else if (e.target.closest("[data-reset]")) setChoice(t, "");
    else if (e.target.closest("[data-details]")) openSheet(t);
  });
  search.addEventListener("input", filter);
  search.addEventListener("keydown", (e) => { if (e.key === "Enter") e.preventDefault(); });
  state.addEventListener("change", filter);
  // sorting
  const sorter = $("[data-sort]");
  sorter?.addEventListener("change", () => {
    const by = sorter.value;
    const sorted = [...tiles].sort((a, b) => by === "title" ? a.dataset.title.localeCompare(b.dataset.title)
      : (Number(b.dataset[by]) - Number(a.dataset[by])) || a.dataset.title.localeCompare(b.dataset.title));
    sorted.forEach((t) => grid.append(t));
  });

  // change every title currently shown
  $$("[data-bulk]").forEach((btn) => btn.addEventListener("click", async () => {
    const menu = btn.closest("details");
    menu?.removeAttribute("open");
    $("summary", menu)?.focus();
    const shown = tiles.filter((t) => !t.hidden);
    const kind = btn.dataset.bulk;
    if (!shown.length) return;
    const verb = kind === "allow" ? "Allow" : kind === "block" ? "Block" : "Reset";
    const ok = await askConfirm(`${verb} all ${shown.length} titles shown?` +
      (kind === "" ? ` They'll return to ${resetLabel}.` : ""), `${verb} ${shown.length}`);
    if (!ok) return;
    invalidateBatchUndo();
    const version = mutation;
    const before = new Map(shown.map((t) => [t, t.dataset.choice]));
    const apply = async (groups) => {
      for (const [k, list] of groups) {
        await postForm(grid.dataset.batch, { csrf: grid.dataset.csrf, kind: k, keys: list.map((t) => t.dataset.key).join(",") });
        list.forEach((t) => { t.dataset.choice = k; paint(t); });
      }
      filter();
    };
    try {
      await apply([[kind, shown]]);
      if (mutation !== version) return;
      batchUndo = toast(`${shown.length} titles updated. Undo is available until your next title change or until you leave this page.`, false, { label: "Undo", run: async () => {
        invalidateBatchUndo();
        const groups = new Map();
        before.forEach((k, t) => { if (!groups.has(k)) groups.set(k, []); groups.get(k).push(t); });
        try { await apply([...groups]); toast("Changes undone"); } catch (err) { toast(err.message, true); }
      } });
    } catch (err) {
      toast(err.message, true);
    }
  }));

  const typeButtons = $$(".segmented [data-type]");
  typeButtons.forEach((b) => b.addEventListener("click", () => {
    type = b.dataset.type;
    typeButtons.forEach((x) => x.setAttribute("aria-pressed", x === b ? "true" : "false"));
    filter();
  }));
  $("[data-clear]")?.addEventListener("click", () => {
    search.value = "";
    state.value = "all";
    type = "all";
    typeButtons.forEach((x) => x.setAttribute("aria-pressed", x.dataset.type === "all" ? "true" : "false"));
    filter();
    search.focus();
  });
  filter();
}

// ----- quick Block from the watch history: one tap, with Undo -----
$$("[data-quick-block]").forEach((btn) => btn.addEventListener("click", async () => {
  const list = btn.closest("[data-post]");
  const key = btn.dataset.quickBlock;
  const same = $$(`[data-quick-block="${key}"]`, list);
  if (btn.getAttribute("aria-disabled") === "true") return;
  const original = btn.innerHTML;
  const originalLabels = new Map(same.map((b) => [b, b.getAttribute("aria-label")]));
  const mark = (blocked) => same.forEach((b) => {
    b.setAttribute("aria-disabled", String(blocked));
    b.setAttribute("aria-label", blocked ? `${btn.dataset.name} is blocked` : originalLabels.get(b));
    b.innerHTML = blocked ? original.replace(/Block\s*$/, "Blocked") : original;
  });
  mark(true);
  try {
    await postForm(list.dataset.post, { csrf: list.dataset.csrf, key, kind: "block" });
    toast(`${btn.dataset.name} is blocked`, false, { label: "Undo", run: async () => {
      try {
        await postForm(list.dataset.post, { csrf: list.dataset.csrf, key, kind: btn.dataset.prev });
        mark(false);
        toast("Block undone");
      } catch (err) { toast(err.message, true); }
    } });
  } catch (err) {
    mark(false);
    toast(err.message, true);
  }
}));

// ----- Schedule: "Next 7 days" follows the form as the parent edits it (same rules as the server) -----
(() => {
  const form = $("[data-schedule]"), list = $("[data-ahead]");
  if (!form || !list) return;
  const el = form.elements;
  const hint = $("[data-ahead-hint]");
  const mins = (m) => (m < 60 ? `${m} min` : `${Math.floor(m / 60)} hr` + (m % 60 ? ` ${m % 60} min` : ""));
  const clock = (hhmm) => {
    const [h, m] = hhmm.split(":").map(Number);
    return `${h % 12 || 12}:${String(m).padStart(2, "0")} ${h < 12 ? "AM" : "PM"}`;
  };
  const addMinutes = (hhmm, extra) => {
    const [h, m] = hhmm.split(":").map(Number);
    const t = (h * 60 + m + extra) % 1440;
    return `${String(Math.floor(t / 60)).padStart(2, "0")}:${String(t % 60).padStart(2, "0")}`;
  };
  const update = () => {
    if (hint) hint.textContent = "Preview of your changes. Press Save schedule to keep them.";
    const brk = el.break_until.value;
    for (const li of list.children) {
      const date = li.dataset.date, dow = Number(li.dataset.dow);
      const onBreak = brk && date <= brk;
      const weekend = onBreak || (dow === 4 && el.friday_weekend.checked) ? true
        : dow === 6 && el.sunday_school.checked ? false : dow >= 5;
      const kind = weekend ? "weekend" : "weekday";
      const raw = el[`${kind}_minutes`].value;
      const limit = raw === "none" ? null : Number(raw) + Number(li.dataset.bonus || 0);
      let start = el[`${kind}_start`].value, end = el[`${kind}_end`].value;
      if (el[`any_time_${kind}`].checked || !start || !end) start = end = "any";
      else if (Number(li.dataset.later) && start !== end) end = addMinutes(end, Number(li.dataset.later));
      const none = limit === 0;
      li.classList.toggle("none", none);
      const invalid = start === end && !el[`any_time_${kind}`].checked;
      $("[data-hours]", li).textContent = none ? "No watching" : invalid ? "Choose different times, or turn on Any time."
        : start === end ? "Any time" : `${clock(start)} to ${clock(end)}`;
      const num = $("[data-limit]", li);
      num.hidden = none;
      num.textContent = limit === null ? "No limit" : mins(limit);
      const note = $("[data-note]", li);
      note.textContent = onBreak && dow < 5 ? (li === list.firstElementChild && date === brk ? "Day off" : "School break") : dow === 4 && el.friday_weekend.checked ? "Weekend rules"
        : dow === 6 && el.sunday_school.checked ? "School night" : "";
      note.hidden = !note.textContent;
    }
  };
  form.addEventListener("input", update);
  form.addEventListener("change", update);
})();

// ----- forms that ask first (styled dialog); "{name}" is the chosen option -----
$$("form[data-confirm-submit]").forEach((form) => form.addEventListener("submit", async (e) => {
  if (form.dataset.confirmed) return;
  e.preventDefault();
  const sel = $("select", form);
  const text = form.dataset.confirmSubmit.replace("{name}", sel ? sel.selectedOptions[0].textContent : "");
  if (await askConfirm(text, $("button", form).textContent.trim())) {
    form.dataset.confirmed = "1";
    if (form.requestSubmit) form.requestSubmit(); else form.submit();
  }
}));

// ----- admin quick replies fill the reply box -----
$$("[data-fill]").forEach((chip) => chip.addEventListener("click", () => {
  const box = $("input[name=note]", chip.closest("form"));
  if (box) { box.value = chip.dataset.fill; box.focus(); }
}));

// ----- Requests: the message example follows the kind of request -----
$$("input[name=kind][data-example]").forEach((r) => r.addEventListener("change", () => {
  const box = $("[data-message]");
  if (box && r.checked && r.dataset.example) box.placeholder = `For example: ${r.dataset.example}`;
}));

// ----- Requests: optional, tab-scoped draft recovery; never replay a rejected POST -----
(() => {
  const form = $("[data-request-draft]");
  if (!form) return;
  const key = `kidswatch-request-draft-${form.dataset.requestDraft}`;
  const notice = $("[data-draft-notice]", form);
  let saved;
  try {
    saved = JSON.parse(sessionStorage.getItem(key) || "null");
    if (saved && saved.id && form.dataset.draftSent === saved.id) {
      sessionStorage.removeItem(key);
      saved = null;
    }
    if (saved && typeof saved.message === "string" && saved.message.trim()) notice.hidden = false;
    else saved = null;
  } catch (e) { /* draft recovery is optional when storage is unavailable */ }
  const draftId = document.createElement("input");
  draftId.type = "hidden";
  draftId.name = "draft_id";
  draftId.value = saved && /^[a-f0-9]{32}$/.test(saved.id) ? saved.id
    : Array.from(crypto.getRandomValues(new Uint8Array(16)), (v) => v.toString(16).padStart(2, "0")).join("");
  form.append(draftId);
  const save = () => {
    const message = form.elements.message.value.slice(0, 1000);
    try {
      if (message.trim()) sessionStorage.setItem(key, JSON.stringify({
        id: draftId.value, message, kind: form.elements.kind.value, kid: form.elements.kid?.value || "",
      }));
      else sessionStorage.removeItem(key);
    } catch (e) { /* keep the form usable without storage */ }
    notice.hidden = true;
  };
  form.addEventListener("input", save);
  form.addEventListener("change", save);
  $("[data-draft-restore]", form).addEventListener("click", () => {
    if (!saved) return;
    const choice = $$("input[name=kind]", form).find((r) => r.value === saved.kind);
    if (choice) { choice.checked = true; choice.dispatchEvent(new Event("change", { bubbles: true })); }
    if (form.elements.kid && Array.from(form.elements.kid.options).some((o) => o.value === saved.kid)) {
      form.elements.kid.value = saved.kid;
    }
    form.elements.message.value = saved.message.slice(0, 1000);
    save();
    form.elements.message.focus();
    announce("Unsent request restored. Review it before sending.");
  });
  $("[data-draft-discard]", form).addEventListener("click", () => {
    try { sessionStorage.removeItem(key); } catch (e) { /* optional storage */ }
    notice.hidden = true;
    saved = null;
    form.elements.message.focus();
  });
})();
// Drafts stay out of the next signed-in person's view, including on shared devices.
$("form[action='/logout']")?.addEventListener("submit", () => {
  try {
    Object.keys(sessionStorage).filter((k) => k.startsWith("kidswatch-request-draft-")).forEach((k) => sessionStorage.removeItem(k));
  } catch (e) { /* optional storage */ }
});
