import db, { generateAccessToken } from '../database/db.js';
import { authMiddleware } from '../middleware/auth.js';

export function setupAdminRoutes(app) {
  // Get all users
  app.get('/api/admin/users', authMiddleware, (req, res) => {
    const users = db.prepare('SELECT id, username, email, whatsapp, plan, max_channels, is_active, access_token, created_at, expires_at FROM users ORDER BY created_at DESC').all();
    res.json({ users });
  });

  // Create user
  app.post('/api/admin/users', authMiddleware, async (req, res) => {
    const { username, password, email, whatsapp, plan, max_channels, expires_days } = req.body;
    if (!username || !password) return res.status(400).json({ error: 'Username y password requeridos' });

    const existing = db.prepare('SELECT id FROM users WHERE username = ?').get(username);
    if (existing) return res.status(409).json({ error: 'Usuario ya existe' });

    const bcrypt = (await import('bcryptjs')).default;
    const hashedPassword = await bcrypt.hash(password, 10);
    const expiresAt = expires_days ? Math.floor(Date.now() / 1000) + (expires_days * 86400) : 0;
    const planChannels = { basico: 40, estandar: 70, premium: 100 }[plan] || 40;
    const accessToken = generateAccessToken();

    const result = db.prepare(
      'INSERT INTO users (username, password, email, whatsapp, plan, max_channels, access_token, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)'
    ).run(username, hashedPassword, email || '', whatsapp || '', plan || 'basico', max_channels || planChannels, accessToken, expiresAt);

    res.status(201).json({ id: result.lastInsertRowid, username, plan: plan || 'basico', access_token: accessToken });
  });

  // Delete user
  app.delete('/api/admin/users/:id', authMiddleware, (req, res) => {
    db.prepare('DELETE FROM users WHERE id = ?').run(req.params.id);
    res.json({ success: true });
  });

  // Get dashboard stats
  app.get('/api/admin/dashboard', authMiddleware, (req, res) => {
    const totalUsers = db.prepare('SELECT COUNT(*) as count FROM users').get().count;
    const activeUsers = db.prepare('SELECT COUNT(*) as count FROM users WHERE is_active = 1').get().count;
    const totalChannels = db.prepare('SELECT COUNT(*) as count FROM channels').get().count;
    const totalProviders = db.prepare('SELECT COUNT(*) as count FROM providers').get().count;
    const activeStreams = db.prepare('SELECT COUNT(*) as count FROM current_streams').get().count;
    const topChannels = db.prepare(`
      SELECT c.name, s.views, s.last_viewed 
      FROM stream_stats s 
      JOIN channels c ON c.id = s.channel_id 
      ORDER BY s.views DESC LIMIT 10
    `).all();

    res.json({
      stats: { totalUsers, activeUsers, totalChannels, totalProviders, activeStreams },
      topChannels
    });
  });

  // Run channel health check (sample mode for quick response)
  app.post('/api/admin/channels/check', authMiddleware, async (req, res) => {
    try {
      const { exec } = await import('child_process');
      const { promisify } = await import('util');
      const execAsync = promisify(exec);
      
      const { stdout } = await execAsync(
        'cd /root/streamflow && node scripts/check_channels.mjs --sample',
        { timeout: 120000 }
      );
      
      const working = stdout.match(/Funcionando: (\d+)/)?.[1] || '?';
      const failed = stdout.match(/Muertos: (\d+)/)?.[1] || '?';
      const total = stdout.match(/Verificados: (\d+)/)?.[1] || '?';
      
      res.json({ 
        success: true, 
        working: parseInt(working),
        failed: parseInt(failed),
        total: parseInt(total),
        output: stdout
      });
    } catch (err) {
      res.status(500).json({ error: err.message || 'Error running check' });
    }
  });

  // Import M3U playlist
  app.post('/api/admin/import-m3u', authMiddleware, async (req, res) => {
    const { provider_id, m3u_content } = req.body;
    if (!provider_id || !m3u_content) return res.status(400).json({ error: 'Provider ID y contenido M3U requeridos' });

    const provider = db.prepare('SELECT id FROM providers WHERE id = ?').get(provider_id);
    if (!provider) return res.status(404).json({ error: 'Provider no encontrado' });

    const lines = m3u_content.split('\n');
    let imported = 0;
    let channelName = '';
    let logo = '';
    let groupName = '';

    const insertChannel = db.prepare(
      'INSERT OR IGNORE INTO channels (provider_id, name, logo, group_name, stream_url) VALUES (?, ?, ?, ?, ?)'
    );

    const importMany = db.transaction(() => {
      for (let i = 0; i < lines.length; i++) {
        const line = lines[i].trim();
        if (line.startsWith('#EXTINF:')) {
          const nameMatch = line.match(/,(.+)$/);
          channelName = nameMatch ? nameMatch[1].trim() : `Channel ${imported + 1}`;
          const logoMatch = line.match(/tvg-logo="([^"]+)"/);
          logo = logoMatch ? logoMatch[1] : '';
          const groupMatch = line.match(/group-title="([^"]+)"/);
          groupName = groupMatch ? groupMatch[1] : '';
        } else if (line && !line.startsWith('#') && channelName) {
          insertChannel.run(provider_id, channelName, logo, groupName, line);
          imported++;
          channelName = '';
        }
      }
    });

    importMany();
    res.json({ imported, message: `${imported} canales importados` });
  });

  // Get all users with pagination
  app.get('/api/admin/users/:page', authMiddleware, (req, res) => {
    const page = parseInt(req.params.page) || 1;
    const limit = 20;
    const offset = (page - 1) * limit;
    const users = db.prepare('SELECT id, username, email, plan, is_active, created_at FROM users ORDER BY created_at DESC LIMIT ? OFFSET ?').all(limit, offset);
    const total = db.prepare('SELECT COUNT(*) as count FROM users').get().count;
    res.json({ users, total, page, totalPages: Math.ceil(total / limit) });
  });

  // Update user
  app.put('/api/admin/users/:id', authMiddleware, (req, res) => {
    const { plan, is_active, max_channels } = req.body;
    const fields = [];
    const values = [];
    if (plan !== undefined) { fields.push('plan = ?'); values.push(plan); }
    if (is_active !== undefined) { fields.push('is_active = ?'); values.push(is_active); }
    if (max_channels !== undefined) { fields.push('max_channels = ?'); values.push(max_channels); }
    if (fields.length === 0) return res.status(400).json({ error: 'No fields to update' });

    values.push(req.params.id);
    db.prepare(`UPDATE users SET ${fields.join(', ')} WHERE id = ?`).run(...values);
    res.json({ success: true });
  });
}
