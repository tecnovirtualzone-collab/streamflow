import Database from 'better-sqlite3';
import path from 'path';
import fs from 'fs';
import crypto from 'crypto';
import { DATA_DIR } from '../config/constants.js';

if (!fs.existsSync(DATA_DIR)) fs.mkdirSync(DATA_DIR, { recursive: true });

const db = new Database(path.join(DATA_DIR, 'streamflow.sqlite'), { timeout: 5000 });
db.pragma('foreign_keys = ON');
db.pragma('busy_timeout = 5000');
db.pragma('journal_mode = WAL');
db.pragma('synchronous = NORMAL');

export function runMigrations() {
  db.exec(`
    CREATE TABLE IF NOT EXISTS admin_users (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      username TEXT UNIQUE NOT NULL,
      password TEXT NOT NULL,
      is_active INTEGER DEFAULT 1,
      created_at INTEGER DEFAULT (strftime('%s','now'))
    );

    CREATE TABLE IF NOT EXISTS users (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      username TEXT UNIQUE NOT NULL,
      password TEXT NOT NULL,
      email TEXT DEFAULT '',
      plan TEXT DEFAULT 'basico',
      max_channels INTEGER DEFAULT 40,
      is_active INTEGER DEFAULT 1,
      created_at INTEGER DEFAULT (strftime('%s','now')),
      expires_at INTEGER DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS providers (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL,
      url TEXT NOT NULL,
      username TEXT NOT NULL,
      password TEXT NOT NULL,
      max_connections INTEGER DEFAULT 3,
      is_active INTEGER DEFAULT 1,
      added_at INTEGER DEFAULT (strftime('%s','now'))
    );

    CREATE TABLE IF NOT EXISTS channels (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      provider_id INTEGER NOT NULL,
      name TEXT NOT NULL,
      logo TEXT DEFAULT '',
      group_name TEXT DEFAULT '',
      stream_url TEXT NOT NULL,
      is_active INTEGER DEFAULT 1,
      created_at INTEGER DEFAULT (strftime('%s','now')),
      FOREIGN KEY (provider_id) REFERENCES providers(id)
    );

    CREATE TABLE IF NOT EXISTS current_streams (
      id TEXT PRIMARY KEY,
      user_id INTEGER,
      username TEXT,
      channel_name TEXT,
      start_time INTEGER,
      last_activity INTEGER,
      ip TEXT,
      worker_pid INTEGER,
      channel_id INTEGER
    );

    CREATE TABLE IF NOT EXISTS stream_stats (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      channel_id INTEGER,
      views INTEGER DEFAULT 0,
      last_viewed INTEGER DEFAULT 0,
      FOREIGN KEY (channel_id) REFERENCES channels(id)
    );

    CREATE TABLE IF NOT EXISTS sessions (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER NOT NULL,
      token TEXT NOT NULL,
      ip TEXT DEFAULT '',
      created_at INTEGER DEFAULT (strftime('%s','now')),
      expires_at INTEGER NOT NULL,
      FOREIGN KEY (user_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS settings (
      key TEXT PRIMARY KEY,
      value TEXT NOT NULL,
      updated_at INTEGER DEFAULT (strftime('%s','now'))
    );

    CREATE INDEX IF NOT EXISTS idx_channels_provider ON channels(provider_id);
    CREATE INDEX IF NOT EXISTS idx_streams_user ON current_streams(user_id);
    CREATE INDEX IF NOT EXISTS idx_sessions_token ON sessions(token);
    CREATE INDEX IF NOT EXISTS idx_stats_channel ON stream_stats(channel_id);
  `);

  // Default settings
  const defaultSettings = [
    ['app_name', 'StreamFlow'],
    ['stream_method', 'ffmpeg'],
    ['stream_buffer', '2'],
    ['max_connections', '3'],
    ['epg_enabled', '0'],
    ['welcome_msg', 'Bienvenido a StreamFlow']
  ];
  
  const insertSetting = db.prepare(
    'INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)'
  );
  for (const [key, value] of defaultSettings) {
    insertSetting.run(key, value);
  }

  console.log('✅ Database migrations completed');
}

export function getSetting(key) {
  const row = db.prepare('SELECT value FROM settings WHERE key = ?').get(key);
  return row ? row.value : null;
}

export function setSetting(key, value) {
  db.prepare(
    'INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, strftime(\'%s\',\'now\'))'
  ).run(key, value);
}

export default db;
