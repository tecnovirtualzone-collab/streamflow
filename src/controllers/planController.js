import db from '../database/db.js';
import { authMiddleware, adminAuthMiddleware } from '../middleware/auth.js';

export function setupPlanRoutes(app) {
  // Get all plans (public - for user registration)
  app.get('/api/plans', (req, res) => {
    const plans = db.prepare('SELECT id, name, description, price_cop, max_channels, max_connections, is_active FROM plans WHERE is_active = 1 ORDER BY price_cop ASC').all();
    res.json({ plans });
  });

  // Get all plans (admin)
  app.get('/api/admin/plans', authMiddleware, (req, res) => {
    const plans = db.prepare(`
      SELECT p.*, COUNT(pc.channel_id) as channel_count
      FROM plans p
      LEFT JOIN plan_channels pc ON pc.plan_id = p.id
      GROUP BY p.id
      ORDER BY p.price_cop ASC
    `).all();
    res.json({ plans });
  });

  // Create plan
  app.post('/api/admin/plans', authMiddleware, (req, res) => {
    const { name, description, price_cop, max_channels, max_connections, channel_ids } = req.body;
    if (!name) return res.status(400).json({ error: 'Nombre requerido' });

    const result = db.prepare(
      'INSERT INTO plans (name, description, price_cop, max_channels, max_connections) VALUES (?, ?, ?, ?, ?)'
    ).run(name, description || '', price_cop || 0, max_channels || 40, max_connections || 1);

    const planId = result.lastInsertRowid;

    // Assign channels to plan
    if (channel_ids && Array.isArray(channel_ids) && channel_ids.length > 0) {
      const insertPC = db.prepare('INSERT OR IGNORE INTO plan_channels (plan_id, channel_id) VALUES (?, ?)');
      const assign = db.transaction(() => {
        for (const chId of channel_ids) {
          insertPC.run(planId, chId);
        }
      });
      assign();
    }

    res.status(201).json({ id: planId, name, price_cop: price_cop || 0 });
  });

  // Update plan
  app.put('/api/admin/plans/:id', authMiddleware, (req, res) => {
    const { name, description, price_cop, max_channels, max_connections, is_active, channel_ids } = req.body;
    const fields = [];
    const values = [];
    if (name !== undefined) { fields.push('name = ?'); values.push(name); }
    if (description !== undefined) { fields.push('description = ?'); values.push(description); }
    if (price_cop !== undefined) { fields.push('price_cop = ?'); values.push(price_cop); }
    if (max_channels !== undefined) { fields.push('max_channels = ?'); values.push(max_channels); }
    if (max_connections !== undefined) { fields.push('max_connections = ?'); values.push(max_connections); }
    if (is_active !== undefined) { fields.push('is_active = ?'); values.push(is_active); }
    if (fields.length === 0) return res.status(400).json({ error: 'No fields to update' });

    values.push(req.params.id);
    db.prepare(`UPDATE plans SET ${fields.join(', ')} WHERE id = ?`).run(...values);

    // Update channel assignments
    if (channel_ids && Array.isArray(channel_ids)) {
      db.prepare('DELETE FROM plan_channels WHERE plan_id = ?').run(req.params.id);
      if (channel_ids.length > 0) {
        const insertPC = db.prepare('INSERT OR IGNORE INTO plan_channels (plan_id, channel_id) VALUES (?, ?)');
        const assign = db.transaction(() => {
          for (const chId of channel_ids) {
            insertPC.run(req.params.id, chId);
          }
        });
        assign();
      }
    }

    res.json({ success: true });
  });

  // Delete plan
  app.delete('/api/admin/plans/:id', authMiddleware, (req, res) => {
    db.prepare('DELETE FROM plan_channels WHERE plan_id = ?').run(req.params.id);
    db.prepare('DELETE FROM plans WHERE id = ?').run(req.params.id);
    res.json({ success: true });
  });

  // Get channels for a plan
  app.get('/api/admin/plans/:id/channels', authMiddleware, (req, res) => {
    const channels = db.prepare(`
      SELECT c.id, c.name, c.logo, c.group_name
      FROM plan_channels pc
      JOIN channels c ON c.id = pc.channel_id
      WHERE pc.plan_id = ?
      ORDER BY c.group_name, c.name
    `).all(req.params.id);
    res.json({ channels });
  });

  // Get channels NOT in a plan (for adding)
  app.get('/api/admin/plans/:id/available-channels', authMiddleware, (req, res) => {
    const { q } = req.query;
    let query = `
      SELECT c.id, c.name, c.logo, c.group_name
      FROM channels c
      WHERE c.is_active = 1
      AND c.id NOT IN (SELECT channel_id FROM plan_channels WHERE plan_id = ?)
    `;
    const params = [req.params.id];
    if (q) {
      query += ' AND c.name LIKE ?';
      params.push(`%${q}%`);
    }
    query += ' ORDER BY c.group_name, c.name LIMIT 200';
    const channels = db.prepare(query).all(...params);
    res.json({ channels });
  });

  // Get all channels grouped by category (for plan builder)
  app.get('/api/admin/channels/grouped', authMiddleware, (req, res) => {
    const { q, limit } = req.query;
    const lim = Math.min(parseInt(limit) || 2000, 10000);
    let query = `
      SELECT c.id, c.name, c.logo, c.group_name, p.name as provider_name
      FROM channels c
      JOIN providers p ON p.id = c.provider_id
      WHERE c.is_active = 1
    `;
    const params = [];
    if (q) {
      query += ' AND (c.name LIKE ? OR c.group_name LIKE ?)';
      params.push(`%${q}%`, `%${q}%`);
    }
    query += ' ORDER BY c.group_name, c.name LIMIT ?';
    params.push(lim);
    const channels = db.prepare(query).all(...params);

    const grouped = {};
    for (const ch of channels) {
      const group = ch.group_name || 'Sin categoría';
      if (!grouped[group]) grouped[group] = [];
      grouped[group].push(ch);
    }

    res.json({ groups: grouped, total: channels.length });
  });
}
