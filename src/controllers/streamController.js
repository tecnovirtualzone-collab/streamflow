import db from '../database/db.js';
import streamManager from '../services/streamManager.js';
import { authMiddleware } from '../middleware/auth.js';

export function setupStreamRoutes(app) {
  // Get stream URL for a channel (main endpoint for players)
  app.get('/api/stream/:channelId', authMiddleware, async (req, res) => {
    try {
      const { channelId } = req.params;
      const userId = req.user.id;
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
      const streamUrl = buildStreamUrl(channel);
      if (!streamUrl) {
        return res.status(400).json({ error: 'URL de stream no válida' });
      }

      const result = await streamManager.startStream({
        userId,
        username: req.user.username,
        channelId: channel.id,
        channelName: channel.name,
        streamUrl,
        ip
      });

      if (result.status === 'error') {
        return res.status(500).json({ error: 'Error al iniciar stream' });
      }

      // Return the stream ID for the client to connect
      res.json({
        streamId: result.streamId,
        status: result.status,
        channel: channel.name
      });
    } catch (err) {
      console.error('Stream error:', err);
      res.status(500).json({ error: 'Error de stream' });
    }
  });

  // Get stream data (for HLS/player to connect)
  app.get('/api/stream/:channelId/data', authMiddleware, (req, res) => {
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
      // Client disconnected, update last activity
      stream.lastActivity = Date.now();
    });
  });

  // Stop stream
  app.delete('/api/stream/:streamId', authMiddleware, async (req, res) => {
    const result = await streamManager.stopStream(req.params.streamId);
    res.json({ success: result });
  });

  // Get active streams
  app.get('/api/streams/active', authMiddleware, (req, res) => {
    const streams = streamManager.getAllStreams();
    res.json({ streams, count: streams.length });
  });

  // Get user's active streams
  app.get('/api/streams/my', authMiddleware, (req, res) => {
    const streams = streamManager.getUserStreams(req.user.id);
    res.json({ streams: streams.map(s => ({ id: s.id, channelName: s.channelName, startTime: s.startTime })) });
  });
}

export function setupChannelRoutes(app) {
  // Get all channels (paginated)
  app.get('/api/channels', authMiddleware, (req, res) => {
    const page = parseInt(req.query.page) || 1;
    const limit = Math.min(parseInt(req.query.limit) || 100, 500);
    const offset = (page - 1) * limit;
    
    const channels = db.prepare(`
      SELECT c.id, c.name, c.logo, c.group_name, p.name as provider_name
      FROM channels c
      JOIN providers p ON p.id = c.provider_id
      WHERE c.is_active = 1
      ORDER BY c.group_name, c.name
      LIMIT ? OFFSET ?
    `).all(limit, offset);
    
    const total = db.prepare('SELECT COUNT(*) as count FROM channels WHERE is_active = 1').get().count;
    
    res.json({ channels, count: channels.length, total, page, totalPages: Math.ceil(total / limit) });
  });

  // Get channels grouped by category
  app.get('/api/channels/grouped', authMiddleware, (req, res) => {
    const channels = db.prepare(`
      SELECT c.id, c.name, c.logo, c.group_name, p.name as provider_name
      FROM channels c
      JOIN providers p ON p.id = c.provider_id
      WHERE c.is_active = 1
      ORDER BY c.group_name, c.name
    `).all();

    const grouped = {};
    for (const ch of channels) {
      const group = ch.group_name || 'Sin categoría';
      if (!grouped[group]) grouped[group] = [];
      grouped[group].push(ch);
    }

    res.json({ groups: grouped });
  });

  // Search channels
  app.get('/api/channels/search', authMiddleware, (req, res) => {
    const { q } = req.query;
    if (!q) return res.json({ channels: [] });

    const channels = db.prepare(`
      SELECT c.id, c.name, c.logo, c.group_name
      FROM channels c
      WHERE c.is_active = 1 AND c.name LIKE ?
      ORDER BY c.name
      LIMIT 50
    `).all(`%${q}%`);

    res.json({ channels });
  });

  // Get channel stats
  app.get('/api/channels/:id/stats', authMiddleware, (req, res) => {
    const stats = db.prepare('SELECT * FROM stream_stats WHERE channel_id = ?').get(req.params.id);
    res.json({ stats: stats || { views: 0, last_viewed: 0 } });
  });
}

export function setupProviderRoutes(app) {
  // Get all providers (admin only)
  app.get('/api/providers', authMiddleware, (req, res) => {
    const providers = db.prepare('SELECT id, name, url, max_connections, is_active, added_at FROM providers ORDER BY name').all();
    res.json({ providers });
  });

  // Add provider
  app.post('/api/providers', authMiddleware, (req, res) => {
    const { name, url, username, password, max_connections } = req.body;
    if (!name || !url || !username || !password) {
      return res.status(400).json({ error: 'Todos los campos requeridos' });
    }

    const result = db.prepare(
      'INSERT INTO providers (name, url, username, password, max_connections) VALUES (?, ?, ?, ?, ?)'
    ).run(name, url, username, password, max_connections || 3);

    res.status(201).json({ id: result.lastInsertRowid, name });
  });

  // Delete provider
  app.delete('/api/providers/:id', authMiddleware, (req, res) => {
    db.prepare('DELETE FROM channels WHERE provider_id = ?').run(req.params.id);
    db.prepare('DELETE FROM providers WHERE id = ?').run(req.params.id);
    res.json({ success: true });
  });
}

function buildStreamUrl(channel) {
  if (channel.stream_url && channel.stream_url.startsWith('http')) {
    return channel.stream_url;
  }
  if (channel.provider_url) {
    const base = channel.provider_url.replace(/\/+$/, '');
    return `${base}/${channel.stream_url || ''}`;
  }
  return null;
}
