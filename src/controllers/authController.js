import bcrypt from 'bcryptjs';
import db, { runMigrations } from '../database/db.js';
import { generateToken, authMiddleware, adminAuthMiddleware } from '../middleware/auth.js';
import { BCRYPT_ROUNDS, INITIAL_ADMIN_PASSWORD, STREAM_MAX_CONCURRENT } from '../config/constants.js';

export function setupAuthRoutes(app) {
  // Login
  app.post('/api/auth/login', async (req, res) => {
    try {
      const { username, password } = req.body;
      if (!username || !password) {
        return res.status(400).json({ error: 'Usuario y contraseña requeridos' });
      }

      // Check admin first
      const admin = db.prepare('SELECT * FROM admin_users WHERE username = ? AND is_active = 1').get(username);
      if (admin) {
        const valid = await bcrypt.compare(password, admin.password);
        if (valid) {
          const token = generateToken({ id: admin.id, username: admin.username, isAdmin: true });
          return res.json({ token, user: { id: admin.id, username: admin.username, role: 'admin' } });
        }
      }

      // Check regular user
      const user = db.prepare('SELECT * FROM users WHERE username = ? AND is_active = 1').get(username);
      if (user) {
        const valid = await bcrypt.compare(password, user.password);
        if (valid) {
          if (user.expires_at > 0 && user.expires_at < Date.now() / 1000) {
            return res.status(401).json({ error: 'Suscripción vencida' });
          }
          const token = generateToken({ id: user.id, username: user.username, plan: user.plan, isAdmin: false });
          return res.json({ token, user: { id: user.id, username: user.username, plan: user.plan, role: 'user' } });
        }
      }

      return res.status(401).json({ error: 'Credenciales inválidas' });
    } catch (err) {
      console.error('Login error:', err);
      res.status(500).json({ error: 'Error en login' });
    }
  });

  // Register user
  app.post('/api/auth/register', async (req, res) => {
    try {
      const { username, password, email } = req.body;
      if (!username || !password) {
        return res.status(400).json({ error: 'Usuario y contraseña requeridos' });
      }

      const existing = db.prepare('SELECT id FROM users WHERE username = ?').get(username);
      if (existing) {
        return res.status(409).json({ error: 'Usuario ya existe' });
      }

      const hashedPassword = await bcrypt.hash(password, BCRYPT_ROUNDS);
      const result = db.prepare('INSERT INTO users (username, password, email, plan) VALUES (?, ?, ?, ?)').run(username, hashedPassword, email || '', 'basico');

      res.status(201).json({ id: result.lastInsertRowid, username, plan: 'basico' });
    } catch (err) {
      console.error('Register error:', err);
      res.status(500).json({ error: 'Error en registro' });
    }
  });

  // Change password
  app.post('/api/auth/change-password', authMiddleware, async (req, res) => {
    try {
      const { currentPassword, newPassword } = req.body;
      const user = db.prepare('SELECT * FROM users WHERE id = ?').get(req.user.id);
      if (!user) return res.status(404).json({ error: 'Usuario no encontrado' });

      const valid = await bcrypt.compare(currentPassword, user.password);
      if (!valid) return res.status(401).json({ error: 'Contraseña actual incorrecta' });

      const hashedPassword = await bcrypt.hash(newPassword, BCRYPT_ROUNDS);
      db.prepare('UPDATE users SET password = ? WHERE id = ?').run(hashedPassword, req.user.id);
      res.json({ success: true });
    } catch (err) {
      res.status(500).json({ error: 'Error al cambiar contraseña' });
    }
  });

  // Verify token
  app.get('/api/auth/verify', authMiddleware, (req, res) => {
    res.json({ valid: true, user: req.user });
  });
}

export async function createDefaultAdmin() {
  const existing = db.prepare('SELECT id FROM admin_users').get();
  if (existing) return;

  const hashedPassword = await bcrypt.hash(INITIAL_ADMIN_PASSWORD, BCRYPT_ROUNDS);
  db.prepare('INSERT INTO admin_users (username, password) VALUES (?, ?)').run('admin', hashedPassword);
  console.log(`✅ Default admin user created (password: ${INITIAL_ADMIN_PASSWORD})`);
}
