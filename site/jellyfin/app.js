'use strict';

// Every preview is browser-local, uses fictional data, and makes no network calls.
const all = (selector, root = document) => [...root.querySelectorAll(selector)];
const one = (selector, root = document) => root.querySelector(selector);
const enable = root => all('button, input, select', root).forEach(el => { el.disabled = false; });

const themeButton = one('[data-theme-toggle]');
if (themeButton) {
  const systemTheme = matchMedia('(prefers-color-scheme: dark)');
  let dark = systemTheme.matches;
  let manualTheme = false;
  const render = () => {
    document.documentElement.dataset.theme = dark ? 'dark' : 'light';
    themeButton.setAttribute('aria-pressed', String(dark));
    one('link[rel="icon"]').setAttribute('href', dark ? '/jellyfin/assets/icon-dark.svg' : '/jellyfin/assets/icon.svg');
  };
  themeButton.hidden = false;
  render();
  systemTheme.addEventListener('change', event => {
    if (!manualTheme) { dark = event.matches; render(); }
  });
  themeButton.addEventListener('click', () => { manualTheme = true; dark = !dark; render(); });
}

// Keep these type-specific rating lists aligned with app/rules.py.
const AGE_LEVELS = {
  little: {
    movies: ['G', 'TV-Y', 'TV-G'],
    tv: ['TV-Y', 'TV-G']
  },
  kids: {
    movies: ['G', 'PG', 'TV-Y', 'TV-Y7', 'TV-Y7-FV', 'TV-G', 'TV-PG'],
    tv: ['TV-Y', 'TV-Y7', 'TV-Y7-FV', 'TV-G', 'TV-PG']
  },
  teens: {
    movies: ['G', 'PG', 'PG-13', 'TV-Y', 'TV-Y7', 'TV-Y7-FV', 'TV-G', 'TV-PG', 'TV-14'],
    tv: ['TV-Y', 'TV-Y7', 'TV-Y7-FV', 'TV-G', 'TV-PG', 'TV-14']
  }
};
function ratingAllowed(level, itemType, rating) {
  const rules = AGE_LEVELS[level] || AGE_LEVELS.kids;
  return Boolean(rating) && rules[itemType === 'show' ? 'tv' : 'movies'].includes(rating);
}
function titleVisible(level, mode, itemType, rating, choice) {
  // The Teens eligibility boundary applies before explicit allows in every mode.
  if (!ratingAllowed('teens', itemType, rating) || choice === 'block') return false;
  if (choice === 'allow') return true;
  return mode !== 'picked' && ratingAllowed(level, itemType, rating);
}

all('[data-titles]').forEach(root => {
  let level = 'kids';
  const choices = new Map();
  const tiles = all('[data-title]', root);
  const labels = {little: 'Little kids', kids: 'Kids', teens: 'Teens'};
  const picked = one('[data-picked]', root);
  const status = one('[data-titles-status]', root);
  const eligible = tile => ratingAllowed('teens', tile.dataset.type, tile.dataset.rating);
  const visible = tile => titleVisible(level, picked.checked ? 'picked' : 'age',
    tile.dataset.type, tile.dataset.rating, choices.get(tile.dataset.title));
  const render = (message = '') => {
    let count = 0;
    tiles.forEach(tile => {
      const title = tile.dataset.title;
      const allowed = visible(tile);
      const choice = choices.get(title);
      if (allowed) count++;
      const state = !eligible(tile) ? 'Rating not eligible' : choice === 'allow' ? 'Always allow' : choice === 'block' ? 'Always block' : allowed ? 'Age level' : 'Hidden';
      tile.dataset.visible = String(allowed);
      one('[data-title-status]', tile).textContent = state;
      one('.mark', tile).textContent = allowed ? '✓' : '×';
      const next = !eligible(tile) ? 'review the rating limit' : choice === 'block' ? 'use age rule' : allowed ? 'block' : 'allow';
      tile.setAttribute('aria-label', `${title}, ${tile.dataset.rating || 'unrated'}, ${tile.dataset.type === 'show' ? 'whole series' : 'movie'}: ${allowed ? 'available' : 'hidden'}, ${state}. Activate to ${next}.`);
    });
    all('[data-level]', root).forEach(button => button.setAttribute('aria-pressed', String(button.dataset.level === level)));
    status.textContent = `${message}${message ? ' ' : ''}${picked.checked ? 'Picked-only' : labels[level] + ' level'}: ${count} of ${tiles.length} example titles available.`;
  };
  all('[data-level]', root).forEach(button => button.addEventListener('click', () => {
    level = button.dataset.level;
    render('Age level changed. Explicit choices are kept.');
  }));
  picked.addEventListener('change', () => render('Library rule changed.'));
  tiles.forEach(tile => tile.addEventListener('click', () => {
    const title = tile.dataset.title;
    if (!eligible(tile)) {
      choices.delete(title);
      render(`${title} stays hidden. This rating is not eligible for an allow.`);
      return;
    }
    const choice = choices.get(title);
    if (choice === 'block') choices.delete(title);
    else choices.set(title, visible(tile) ? 'block' : 'allow');
    const action = choices.get(title) === 'allow' ? 'allowed' : choices.get(title) === 'block' ? 'blocked' : 'returned to age rule';
    render(`${title} ${action}${tile.dataset.type === 'show' ? ' as a whole series' : ''}.`);
  }));
  one('[data-titles-reset]', root).addEventListener('click', () => {
    choices.clear(); level = 'kids'; picked.checked = false;
    render('Title choices reset.');
  });
  enable(root);
  render();
});

all('[data-dashboard]').forEach(root => {
  let paused = false;
  let bonus = 0;
  let extension = 0;
  let atBedtime = false;
  const status = one('[data-dashboard-status]', root);
  const pause = one('[data-pause]', root);
  const events = one('[data-event-list]', root);
  const addEvent = text => {
    const item = document.createElement('li');
    item.textContent = text;
    const caption = document.createElement('small');
    caption.textContent = 'Fictional change by Jamie · This browser tab only';
    item.append(caption);
    events.prepend(item);
    while (events.children.length > 5) events.lastElementChild.remove();
  };
  const render = message => {
    const total = 120 + bonus;
    one('[data-total]', root).textContent = String(total);
    one('[data-remaining]', root).textContent = String(total - 47);
    const progress = one('[data-progress]', root);
    progress.max = total;
    progress.textContent = `47 of ${total} example minutes`;
    one('[data-bedtime]', root).textContent = extension ? '8:30 PM' : '8:00 PM';
    const watch = one('[data-watch-state]', root);
    watch.textContent = paused ? 'Paused by parent' : atBedtime && !extension ? 'Watch hours ended' : 'Watching';
    watch.classList.toggle('red', paused || atBedtime && !extension);
    pause.textContent = paused ? '▶ Resume' : 'Ⅱ Pause';
    pause.setAttribute('aria-pressed', String(paused));
    one('[data-bonus]', root).disabled = bonus >= 60;
    one('[data-later]', root).disabled = extension > 0;
    one('[data-bedtime-test]', root).disabled = atBedtime;
    if (message) status.textContent = message;
  };
  pause.addEventListener('click', () => {
    paused = !paused;
    const message = paused ? 'Example paused. We asked the player to stop; playback is not confirmed.'
      : atBedtime && !extension ? 'Example pause lifted. Watch hours have ended. The rule still says no watching, but Jellyfin 12.3 can still serve video.'
        : 'Example resumed. The schedule permits watching; this does not verify a player response.';
    addEvent(paused ? 'Parent pause · Stop requested, not confirmed' : 'Parent pause lifted');
    render(message);
  });
  one('[data-bonus]', root).addEventListener('click', () => {
    bonus += 15;
    addEvent('15 bonus minutes added for today');
    render(`15 example minutes added. ${120 + bonus - 47} min left. Bedtime is unchanged${atBedtime && !extension ? '; watch hours have ended' : ''}.`);
  });
  one('[data-later]', root).addEventListener('click', () => {
    extension = 30;
    addEvent('Bedtime moved to 8:30 PM for tonight');
    render(`Example bedtime is now 8:30 PM. Daily minutes are unchanged${paused ? '; the parent pause remains on' : ''}.`);
  });
  one('[data-bedtime-test]', root).addEventListener('click', () => {
    atBedtime = true;
    addEvent(extension ? '8:00 PM · Within the extended watch window' : 'Watch hours ended · Stop requested, not confirmed');
    render(extension ? 'Example time is 8:00 PM. The extended window stays open to 8:30 PM; a parent pause still applies.'
      : 'Example time is 8:00 PM. Watch hours ended. We asked the player to stop. The current video may continue. The playback-off setting did not reliably block new streams in our Jellyfin 12.3 lab.');
  });
  one('[data-dashboard-reset]', root).addEventListener('click', () => {
    paused = false; bonus = 0; extension = 0; atBedtime = false;
    events.replaceChildren();
    addEvent('Example reset · 47 minutes watched today');
    render('Dashboard example reset. No server or player was changed.');
  });
  enable(root);
  render();
});

all('[data-schedule]').forEach(root => {
  const preset = one('[data-preset]', root);
  const minutes = one('[data-weekday-min]', root);
  const friday = one('[data-friday]', root);
  const sunday = one('[data-sunday]', root);
  const status = one('[data-schedule-status]', root);
  const render = () => {
    if (!minutes.checkValidity() || minutes.value === '') {
      status.textContent = 'Enter school-day minutes from 0 to 240, in steps of 15. The last valid week is still shown.';
      minutes.setAttribute('aria-invalid', 'true');
      return;
    }
    minutes.removeAttribute('aria-invalid');
    const schoolMinutes = Number(minutes.value);
    const daily = [];
    for (let i = 0; i < 7; i++) {
      const weekend = i === 4 || i === 5 && !sunday.checked || i === 3 && friday.checked;
      const summer = preset.value === 'summer';
      const off = preset.value === 'weekends' && !weekend;
      const amount = off ? 0 : summer || weekend ? 180 : schoolMinutes;
      daily.push(amount);
      one(`[data-day-hours="${i}"]`, root).textContent = off || !amount ? 'No watching' : summer || weekend ? '8:00 AM to 8:30 PM' : '3:00 PM to 8:00 PM';
      one(`[data-day-minutes="${i}"]`, root).textContent = `${amount} min`;
    }
    const label = preset.options[preset.selectedIndex].text;
    status.textContent = `${label}: ${daily[0]} min for the example Tuesday. ${friday.checked ? 'Friday uses weekend rules. ' : ''}${sunday.checked ? 'Sunday is a school night. ' : ''}${preset.value === 'summer' ? 'School break uses weekend limits every day.' : 'See the example week above.'}`;
  };
  [preset, minutes, friday, sunday].forEach(control => control.addEventListener('input', render));
  one('[data-schedule-reset]', root).addEventListener('click', () => {
    preset.value = 'school'; minutes.value = '120'; friday.checked = false; sunday.checked = false; render();
    status.textContent = 'Schedule reset. School days: 120 min; weekends: 180 min.';
  });
  enable(root);
  render();
});

all('[data-activity]').forEach(root => {
  const buttons = all('[data-activity-day]', root);
  const status = one('[data-activity-status]', root);
  const descriptions = {
    Wed: '42 of 120 minutes. Toy Story on the example TV.',
    Thu: '60 of 120 minutes. Sesame Street on the example tablet.',
    Fri: '38 of 120 minutes. Frozen on the example TV.',
    Sat: '95 of 180 minutes. Two example viewing sessions.',
    Sun: '80 of 180 minutes. Outside watch hours: stop requested, not confirmed.',
    Mon: '55 of 135 minutes, including 15 bonus minutes added by Jamie.',
    Today: '47 of 120 minutes. Sesame Street on the example TV.'
  };
  buttons.forEach(button => button.addEventListener('click', () => {
    buttons.forEach(item => item.setAttribute('aria-pressed', String(item === button)));
    status.textContent = `${button.dataset.activityDay}: ${descriptions[button.dataset.activityDay]}`;
  }));
  enable(root);
});

all('[data-owner]').forEach(root => {
  const approve = one('[data-approve]', root);
  const trusted = one('[data-trusted]', root);
  const status = one('[data-owner-status]', root);
  const child = one('[data-child-state]', root);
  approve.addEventListener('click', () => {
    approve.disabled = true;
    approve.textContent = 'Example child approved';
    child.textContent = 'Example ready for device setup';
    child.classList.add('accent');
    status.textContent = 'Rowan approved in this example. Restrictions were checked in the fictional workflow; a real installation still needs verification.';
    // Return keyboard focus to the request status when its action becomes disabled.
    child.tabIndex = -1;
    child.focus();
  });
  trusted.addEventListener('change', () => {
    status.textContent = trusted.checked
      ? 'Trust on for future children from approved parents. Existing pending requests still need owner approval.'
      : 'Trust off. Future children stay disabled until owner approval. Existing approvals are unchanged.';
  });
  one('[data-owner-reset]', root).addEventListener('click', () => {
    approve.disabled = false;
    approve.textContent = 'Approve example child';
    child.textContent = 'Waiting for owner';
    child.classList.remove('accent');
    child.removeAttribute('tabindex');
    trusted.checked = true;
    status.textContent = 'Owner example reset. Trusted parents can activate future verified children; this pending request still needs approval.';
  });
  enable(root);
});

