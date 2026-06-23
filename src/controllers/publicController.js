import db, { getUserByToken, getBackupsForChannel, getAliveChannels } from '../database/db.js';
import streamManager from '../services/streamManager.js';
import { URL } from 'url';

// Public access middleware - validates token from query param or header
function publicAuth(req, res, next) {
  const token = req.query.token || req.headers['x-access-token'] || req.headers['authorization']?.replace('Bearer ', '');
  if (!token) return res.status(401).json({ error: 'Token requerido' });

  const user = getUserByToken(token);
  if (!user) return res.status(401).json({ error: 'Token inválido o usuario inactivo' });

  // Check expiration
  if (user.expires_at && user.expires_at > 0 && user.expires_at < Math.floor(Date.now() / 1000)) {
    return res.status(401).json({ error: 'Cuenta expirada' });
  }

  req.user = user;
  next();
}

export function setupPublicRoutes(app) {
  const BASE_URL = process.env.BASE_URL || 'http://72.60.243.73:5000';

  // Get M3U playlist for user (for IPTV apps like TiviMate, IPTV Smarters, etc.)
  app.get('/api/public/m3u', publicAuth, (req, res) => {
    const user = req.user;

    // Get channels for user's plan - only alive channels
    let channels;
    if (user.plan === 'premium') {
      // Premium gets all active+alive channels
      channels = db.prepare(`
        SELECT c.id, c.name, c.logo, c.group_name, c.stream_url, p.name as provider_name
        FROM channels c
        JOIN providers p ON p.id = c.provider_id
        LEFT JOIN channel_health h ON h.channel_id = c.id
        WHERE c.is_active = 1 AND p.is_active = 1
        AND (h.is_alive IS NULL OR h.is_alive = 1)
        ORDER BY c.group_name, c.name
        LIMIT ?
      `).all(user.max_channels || 500);
    } else {
      // Get channels assigned to user's plan that are alive
      channels = db.prepare(`
        SELECT c.id, c.name, c.logo, c.group_name, c.stream_url, p.name as provider_name
        FROM plan_channels pc
        JOIN channels c ON c.id = pc.channel_id
        JOIN plans pl ON pl.id = pc.plan_id
        JOIN providers p ON p.id = c.provider_id
        LEFT JOIN channel_health h ON h.channel_id = c.id
        WHERE pl.name = ? AND c.is_active = 1 AND p.is_active = 1
        AND (h.is_alive IS NULL OR h.is_alive = 1)
        ORDER BY c.group_name, c.name
        LIMIT ?
      `).all(user.plan, user.max_channels || 100);
    }

    // If not enough alive channels, fill with backups
    if (channels.length < (user.max_channels || 40)) {
      const needed = (user.max_channels || 40) - channels.length;
      const existingIds = channels.map(c => c.id).join(',');

      let backupChannels = [];
      if (existingIds.length > 0) {
        backupChannels = db.prepare(`
          SELECT DISTINCT c.id, c.name, c.logo, c.group_name, c.stream_url, p.name as provider_name
          FROM channel_backups cb
          JOIN channels c ON c.id = cb.backup_channel_id
          JOIN providers p ON p.id = c.provider_id
          LEFT JOIN channel_health h ON h.channel_id = c.id
          WHERE cb.channel_id IN (SELECT channel_id FROM plan_channels WHERE plan_id = (
            SELECT id FROM plans WHERE name = ?
          ))
          AND c.is_active = 1 AND p.is_active = 1
          AND (h.is_alive IS NULL OR h.is_alive = 1)
          AND c.id NOT IN (${existingIds})
          ORDER BY cb.priority
          LIMIT ?
        `).all(user.plan, needed);
      }

      const existingSet = new Set(channels.map(c => c.id));
      for (const bc of backupChannels) {
        if (!existingSet.has(bc.id)) {
          channels.push(bc);
          existingSet.add(bc.id);
        }
      }
    }

    // If still no channels, fall back to any alive channels
    if (!channels || channels.length === 0) {
      channels = db.prepare(`
        SELECT c.id, c.name, c.logo, c.group_name, c.stream_url, p.name as provider_name
        FROM channels c
        JOIN providers p ON p.id = c.provider_id
        LEFT JOIN channel_health h ON h.channel_id = c.id
        WHERE c.is_active = 1 AND p.is_active = 1
        AND (h.is_alive IS NULL OR h.is_alive = 1)
        ORDER BY c.group_name, c.name
        LIMIT ?
      `).all(user.max_channels || 40);
    }

    // Build M3U
    let m3u = '#EXTM3U\n';
    for (const ch of channels) {
      const streamUrl = `${BASE_URL}/api/public/stream/${ch.id}?token=${user.access_token}`;
      m3u += `#EXTINF:-1 tvg-id="${ch.id}" tvg-name="${ch.name}" tvg-logo="${ch.logo || ''}" group-title="${ch.group_name || 'General'}",${ch.name}\n`;
      m3u += `${streamUrl}\n`;
    }

    res.setHeader('Content-Type', 'application/x-mpegurl');
    res.setHeader('Content-Disposition', `attachment; filename="streamflow_${user.username}.m3u"`);
    res.send(m3u);
  });

  // Get user info by token
  app.get('/api/public/me', publicAuth, (req, res) => {
    res.json({
      username: req.user.username,
      plan: req.user.plan,
      max_channels: req.user.max_channels,
      expires_at: req.user.expires_at
    });
  });

  // Public stream endpoint (token-based, no JWT needed)
  app.get('/api/public/stream/:channelId', publicAuth, async (req, res) => {
    try {
      const { channelId } = req.params;
      const user = req.user;
      const ip = req.ip || req.connection.remoteAddress;

      const channel = db.prepare(`
        SELECT c.*, p.url as provider_url, p.username as provider_user, p.password as provider_pass
        FROM channels c
        JOIN providers p ON p.id = c.provider_id
        WHERE c.id = ? AND c.is_active = 1 AND p.is_active = 1
      `).get(channelId);

      if (!channel) {
        return res.status(404).json({ error: 'Canal no encontrado' });
      }

      // Build the real stream URL from provider
      let streamUrl = channel.stream_url;
      if (streamUrl && !streamUrl.startsWith('http') && channel.provider_url) {
        const base = channel.provider_url.replace(/\/+$/, '');
        streamUrl = `${base}/${streamUrl}`;
      }
      if (!streamUrl || !streamUrl.startsWith('http')) {
        return res.status(400).json({ error: 'URL de stream no válida' });
      }

      const result = await streamManager.startStream({
        userId: user.id,
        username: user.username,
        channelId: channel.id,
        channelName: channel.name,
        streamUrl,
        ip
      });

      if (result.status === 'error') {
        return res.status(500).json({ error: 'Error al iniciar stream' });
      }

      // Redirect to stream data endpoint (also public)
      res.redirect(`/api/public/stream/${channelId}/data?token=${user.access_token}`);
    } catch (err) {
      console.error('Public stream error:', err);
      res.status(500).json({ error: 'Error de stream' });
    }
  });

  // Public stream data (HLS/TS data)
  app.get('/api/public/stream/:channelId/data', publicAuth, (req, res) => {
    const { channelId } = req.params;
    const stream = streamManager.findExistingStream(channelId, req.ip);

    if (!stream || !stream.output) {
      return res.status(404).json({ error: 'Stream no activo' });
    }

    res.setHeader('Content-Type', 'video/MP2T');
    res.setHeader('Cache-Control', 'no-cache');
    res.setHeader('Connection', 'keep-alive');

    const output = stream.output;
    output.pipe(res);

    req.on('close', () => {
      stream.lastActivity = Date.now();
    });
  });
}
