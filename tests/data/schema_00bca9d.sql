CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS parents(
  plex_id INTEGER PRIMARY KEY,
  username TEXT NOT NULL,
  title TEXT,
  thumb TEXT,
  enabled INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  last_login TEXT
);
CREATE TABLE IF NOT EXISTS kids(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  parent_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  status TEXT NOT NULL,
  plex_id INTEGER UNIQUE,
  plex_username TEXT,
  age_level TEXT NOT NULL DEFAULT 'kids',
  mode TEXT NOT NULL DEFAULT 'age',
  sections TEXT NOT NULL DEFAULT '1,2',
  weekday_minutes INTEGER,
  weekend_minutes INTEGER,
  weekday_start TEXT NOT NULL DEFAULT '07:00',
  weekday_end TEXT NOT NULL DEFAULT '20:00',
  weekend_start TEXT NOT NULL DEFAULT '07:00',
  weekend_end TEXT NOT NULL DEFAULT '21:00',
  paused INTEGER NOT NULL DEFAULT 0,
  bonus_day TEXT,
  bonus_minutes INTEGER NOT NULL DEFAULT 0,
  block_online INTEGER NOT NULL DEFAULT 1,
  online_off INTEGER,
  plex_accepted INTEGER NOT NULL DEFAULT 0,
  titles_synced_at TEXT,
  titles_count INTEGER,
  sync_state TEXT NOT NULL DEFAULT 'ok',
  sync_error TEXT,
  applied_filters TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kid_titles(
  kid_id INTEGER NOT NULL REFERENCES kids(id) ON DELETE CASCADE,
  rating_key INTEGER NOT NULL,
  kind TEXT NOT NULL,
  section_key TEXT NOT NULL,
  item_type TEXT NOT NULL,
  title TEXT NOT NULL,
  year INTEGER,
  PRIMARY KEY(kid_id, rating_key, kind)
);
CREATE TABLE IF NOT EXISTS usage(
  kid_id INTEGER NOT NULL,
  day TEXT NOT NULL,
  seconds INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(kid_id, day)
);
CREATE TABLE IF NOT EXISTS stops(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kid_id INTEGER NOT NULL,
  at TEXT NOT NULL,
  reason TEXT NOT NULL,
  title TEXT,
  session_id TEXT
);
CREATE TABLE IF NOT EXISTS requests(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  parent_id INTEGER NOT NULL,
  kid_id INTEGER,
  kind TEXT NOT NULL,
  message TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',
  owner_note TEXT,
  created_at TEXT NOT NULL,
  resolved_at TEXT
);
CREATE TABLE IF NOT EXISTS audit(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  at TEXT NOT NULL,
  actor TEXT NOT NULL,
  action TEXT NOT NULL,
  detail TEXT
);
CREATE TABLE IF NOT EXISTS link_tokens(
  token TEXT PRIMARY KEY,
  kid_id INTEGER NOT NULL,
  parent_id INTEGER NOT NULL,
  expires_at REAL NOT NULL,
  used INTEGER NOT NULL DEFAULT 0
);
