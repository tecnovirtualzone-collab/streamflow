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
      username TEXT UNIQUE,
      password TEXT,
      email TEXT DEFAULT '',
      whatsapp TEXT DEFAULT '',
      plan TEXT DEFAULT 'basico',
      max_channels INTEGER DEFAULT 40,
      is_active INTEGER DEFAULT 1,
      access_token TEXT DEFAULT '',
      expires_at INTEGER,
      created_at INTEGER DEFAULT (strftime('%s', 'now'))
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

    CREATE TABLE IF NOT EXISTS plans (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL,
      description TEXT DEFAULT '',
      price_cop INTEGER DEFAULT 0,
      max_channels INTEGER DEFAULT 40,
      max_connections INTEGER DEFAULT 1,
      is_active INTEGER DEFAULT 1,
      created_at INTEGER DEFAULT (strftime('%s','now'))
    );

    CREATE TABLE IF NOT EXISTS plan_channels (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      plan_id INTEGER NOT NULL,
      channel_id INTEGER NOT NULL,
      FOREIGN KEY (plan_id) REFERENCES plans(id),
      FOREIGN KEY (channel_id) REFERENCES channels(id),
      UNIQUE(plan_id, channel_id)
    );

    CREATE TABLE IF NOT EXISTS whatsapp_sessions (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      session_id TEXT UNIQUE NOT NULL,
      qr_code TEXT DEFAULT '',
      status TEXT DEFAULT 'disconnected',
      phone TEXT DEFAULT '',
      connected_at INTEGER DEFAULT 0,
      last_seen INTEGER DEFAULT 0,
      created_at INTEGER DEFAULT (strftime('%s','now'))
    );

    CREATE TABLE IF NOT EXISTS whatsapp_messages (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      session_id TEXT NOT NULL,
      direction TEXT NOT NULL,
      from_number TEXT DEFAULT '',
      to_number TEXT DEFAULT '',
      message TEXT DEFAULT '',
      message_type TEXT DEFAULT 'text',
      status TEXT DEFAULT 'pending',
      created_at INTEGER DEFAULT (strftime('%s','now'))
    );

    CREATE INDEX IF NOT EXISTS idx_channels_provider ON channels(provider_id);
    CREATE INDEX IF NOT EXISTS idx_streams_user ON current_streams(user_id);
    CREATE INDEX IF NOT EXISTS idx_sessions_token ON sessions(token);
    CREATE INDEX IF NOT EXISTS idx_stats_channel ON stream_stats(channel_id);
    CREATE INDEX IF NOT EXISTS idx_plan_channels_plan ON plan_channels(plan_id);
    CREATE INDEX IF NOT EXISTS idx_plan_channels_ch ON plan_channels(channel_id);
    CREATE INDEX IF NOT EXISTS idx_wa_session ON whatsapp_sessions(session_id);
    CREATE INDEX IF NOT EXISTS idx_channels_name ON channels(name);

    -- Channel health tracking
    CREATE TABLE IF NOT EXISTS channel_health (
      channel_id INTEGER PRIMARY KEY,
      last_check INTEGER DEFAULT 0,
      last_success INTEGER DEFAULT 0,
      is_alive INTEGER DEFAULT 1,
      fail_count INTEGER DEFAULT 0,
      response_time_ms INTEGER DEFAULT 0,
      FOREIGN KEY (channel_id) REFERENCES channels(id)
    );

    -- Backup channels: for each channel, store ordered replacements
    CREATE TABLE IF NOT EXISTS channel_backups (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      channel_id INTEGER NOT NULL,
      backup_channel_id INTEGER NOT NULL,
      priority INTEGER DEFAULT 0,
      FOREIGN KEY (channel_id) REFERENCES channels(id),
      FOREIGN KEY (backup_channel_id) REFERENCES channels(id),
      UNIQUE(channel_id, backup_channel_id)
    );

    CREATE INDEX IF NOT EXISTS idx_health_alive ON channel_health(is_alive);
    CREATE INDEX IF NOT EXISTS idx_health_check ON channel_health(last_check);
    CREATE INDEX IF NOT EXISTS idx_backups_channel ON channel_backups(channel_id);
    CREATE INDEX IF NOT EXISTS idx_backups_priority ON channel_backups(channel_id, priority);
  `);

  // ── MIGRATIONS (run every time, safe to re-run) ──
  // Add access_token to users if not exists
  const userCols = db.prepare("PRAGMA table_info(users)").all();
  if (!userCols.find(c => c.name === 'access_token')) {
    db.exec("ALTER TABLE users ADD COLUMN access_token TEXT DEFAULT ''");
    console.log('✅ Migration: added access_token to users');
  }
  // Generate access_token for existing users that don't have one
  const usersWithoutToken = db.prepare("SELECT id FROM users WHERE access_token = '' OR access_token IS NULL").all();
  if (usersWithoutToken.length > 0) {
    const updateToken = db.prepare("UPDATE users SET access_token = ? WHERE id = ?");
    const gen = db.transaction(() => {
      for (const u of usersWithoutToken) {
        updateToken.run(crypto.randomBytes(32).toString('hex'), u.id);
      }
    });
    gen();
    console.log(`✅ Migration: generated access_token for ${usersWithoutToken.length} existing users`);
  }
  const defaultSettings = [
    ['app_name', 'StreamFlow'],
    ['stream_method', 'ffmpeg'],
    ['stream_buffer', '2'],
    ['max_connections', '3'],
    ['epg_enabled', '0'],
    ['welcome_msg', 'Bienvenido a StreamFlow'],
    ['whatsapp_enabled', '0'],
    ['whatsapp_session_id', '']
  ];
  
  const insertSetting = db.prepare(
    'INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)'
  );
  for (const [key, value] of defaultSettings) {
    insertSetting.run(key, value);
  }

  // Default plans
  const existingPlans = db.prepare('SELECT COUNT(*) as count FROM plans').get();
  if (existingPlans.count === 0) {
    const insertPlan = db.prepare('INSERT INTO plans (name, description, price_cop, max_channels, max_connections) VALUES (?, ?, ?, ?, ?)');
    insertPlan.run('Básico', 'Plan básico con canales esenciales', 10000, 40, 1);
    insertPlan.run('Estándar', 'Plan estándar con más canales y contenido premium', 18000, 70, 2);
    insertPlan.run('Premium', 'Plan premium con todos los canales disponibles', 25000, 100, 3);
    console.log('✅ Default plans created');
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

// Generate unique access token for a user
export function generateAccessToken() {
  return crypto.randomBytes(32).toString('hex');
}

export function getUserByToken(token) {
  if (!token) return null;
  return db.prepare('SELECT * FROM users WHERE access_token = ? AND is_active = 1').get(token);
}

// ── CHANNEL HEALTH ──

export function getChannelHealth(channelId) {
  return db.prepare('SELECT * FROM channel_health WHERE channel_id = ?').get(channelId);
}

export function setChannelHealth(channelId, { isAlive, responseTimeMs }) {
  const now = Math.floor(Date.now() / 1000);
  const existing = db.prepare('SELECT * FROM channel_health WHERE channel_id = ?').get(channelId);
  if (existing) {
    if (isAlive) {
      db.prepare('UPDATE channel_health SET last_check = ?, last_success = ?, is_alive = 1, fail_count = 0, response_time_ms = ? WHERE channel_id = ?')
        .run(now, now, responseTimeMs || 0, channelId);
    } else {
      db.prepare('UPDATE channel_health SET last_check = ?, is_alive = 0, fail_count = fail_count + 1 WHERE channel_id = ?')
        .run(now, channelId);
    }
  } else {
    db.prepare('INSERT INTO channel_health (channel_id, last_check, last_success, is_alive, fail_count, response_time_ms) VALUES (?, ?, ?, ?, 0, ?)')
      .run(channelId, now, isAlive ? now : 0, isAlive ? 1 : 0, responseTimeMs || 0);
  }
}

export function getAliveChannels(limit = 500) {
  return db.prepare(`
    SELECT c.id, c.name, c.logo, c.group_name, c.stream_url
    FROM channels c
    LEFT JOIN channel_health h ON h.channel_id = c.id
    WHERE c.is_active = 1
    AND (h.is_alive IS NULL OR h.is_alive = 1)
    ORDER BY c.group_name, c.name
    LIMIT ?
  `).all(limit);
}

// ── BACKUP CHANNELS ──

export function getBackupsForChannel(channelId) {
  return db.prepare(`
    SELECT cb.backup_channel_id, cb.priority, c.name, c.logo, c.group_name, c.stream_url,
           h.is_alive as backup_alive
    FROM channel_backups cb
    JOIN channels c ON c.id = cb.backup_channel_id
    LEFT JOIN channel_health h ON h.channel_id = cb.backup_channel_id
    WHERE cb.channel_id = ?
    ORDER BY cb.priority ASC
  `).all(channelId);
}

export function addBackupChannel(channelId, backupChannelId, priority = 0) {
  db.prepare('INSERT OR IGNORE INTO channel_backups (channel_id, backup_channel_id, priority) VALUES (?, ?, ?)')
    .run(channelId, backupChannelId, priority);
}

export function removeBackupChannel(channelId, backupChannelId) {
  db.prepare('DELETE FROM channel_backups WHERE channel_id = ? AND backup_channel_id = ?')
    .run(channelId, backupChannelId);
}

export function autoGenerateBackups() {
  // For every channel in plans, find 2-3 backup candidates from the same group
  const planChannels = db.prepare(`
    SELECT DISTINCT pc.channel_id, c.name, c.group_name
    FROM plan_channels pc
    JOIN channels c ON c.id = pc.channel_id
  `).all();

  let created = 0;
  const insert = db.prepare('INSERT OR IGNORE INTO channel_backups (channel_id, backup_channel_id, priority) VALUES (?, ?, ?)');

  for (const pc of planChannels) {
    // Check if already has backups
    const existing = db.prepare('SELECT COUNT(*) as c FROM channel_backups WHERE channel_id = ?').get(pc.channel_id);
    if (existing.c >= 2) continue; // Already has enough

    // Find candidates: same group, not already a backup, different channel
    const candidates = db.prepare(`
      SELECT c.id
      FROM channels c
      LEFT JOIN channel_health h ON h.channel_id = c.id
      WHERE c.group_name = ?
      AND c.id != ?
      AND c.is_active = 1
      AND (h.is_alive IS NULL OR h.is_alive = 1)
      AND c.id NOT IN (SELECT backup_channel_id FROM channel_backups WHERE channel_id = ?)
      ORDER BY RANDOM()
      LIMIT 3
    `).all(pc.group_name, pc.channel_id, pc.channel_id);

    let priority = (existing.c || 0);
    for (const cand of candidates) {
      insert.run(pc.channel_id, cand.id, priority);
      priority++;
      created++;
    }
  }

  return created;
}

export function getDeadPlanChannelsWithReplacements() {
  // Returns channels in plans that are dead, with their best replacement
  return db.prepare(`
    SELECT pc.channel_id as dead_channel_id, c.name as dead_name, c.group_name,
           cb.backup_channel_id, bc.name as backup_name, bc.stream_url as backup_url,
           bc.logo as backup_logo, bc.group_name as backup_group
    FROM plan_channels pc
    JOIN channels c ON c.id = pc.channel_id
    LEFT JOIN channel_health h ON h.channel_id = c.id
    JOIN channel_backups cb ON cb.channel_id = c.id
    JOIN channels bc ON bc.id = cb.backup_channel_id
    LEFT JOIN channel_health bh ON bh.channel_id = cb.backup_channel_id
    WHERE (h.is_alive = 0 OR h.is_alive IS NULL)
    AND (bh.is_alive = 1 OR bh.is_alive IS NULL)
    AND bc.is_active = 1
    ORDER BY pc.channel_id, cb.priority
  `).all();
}

export default db;
