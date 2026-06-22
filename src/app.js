import express from 'express';
import cors from 'cors';
import helmet from 'helmet';
import morgan from 'morgan';
import path from 'path';
import { fileURLToPath } from 'url';
import { PORT, TZ } from './config/constants.js';
import db, { runMigrations } from './database/db.js';
import { createDefaultAdmin, setupAuthRoutes } from './controllers/authController.js';
import { setupStreamRoutes, setupChannelRoutes, setupProviderRoutes } from './controllers/streamController.js';
import { setupAdminRoutes } from './controllers/adminController.js';
import { setupPlanRoutes } from './controllers/planController.js';
import { setupWhatsAppRoutes } from './controllers/whatsappController.js';
import { setupPublicRoutes } from './controllers/publicController.js';
import streamManager from './services/streamManager.js';
import { createProxyMiddleware } from 'http-proxy-middleware';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const app = express();

// Middleware
app.use(cors());
app.use(helmet({ contentSecurityPolicy: false }));
app.use(express.json({ limit: '50mb' }));
app.use(express.urlencoded({ extended: true }));
if (process.env.NODE_ENV !== 'test') {
  app.use(morgan('dev'));
}

// Health check
app.get('/health', (req, res) => {
  res.json({ status: 'ok', uptime: process.uptime(), timezone: TZ });
});

// API Routes
setupAuthRoutes(app);
setupStreamRoutes(app);
setupChannelRoutes(app);
setupProviderRoutes(app);
setupAdminRoutes(app);
setupPlanRoutes(app);
setupWhatsAppRoutes(app);
setupPublicRoutes(app);

// WA Service proxy (microservicio WA en puerto 5002)
const WA_PORT = process.env.WA_PORT || 5002;
app.use('/wa', async (req, res) => {
  try {
    const url = `http://127.0.0.1:${WA_PORT}/wa${req.path}`;
    const response = await fetch(url, {
      method: req.method,
      headers: { 'Content-Type': 'application/json' },
      body: req.method !== 'GET' ? JSON.stringify(req.body) : undefined
    });
    const contentType = response.headers.get('content-type') || '';
    if (contentType.includes('application/json')) {
      const data = await response.json();
      res.json(data);
    } else {
      const text = await response.text();
      res.type('html').send(text);
    }
  } catch (err) {
    res.status(502).json({ error: 'WA Service unavailable', message: err.message });
  }
});

// Serve admin panel
app.use(express.static(path.join(__dirname, '..', 'public')));
app.get('*', (req, res) => {
  if (!req.path.startsWith('/api')) {
    res.sendFile(path.join(__dirname, '..', 'public', 'index.html'));
  }
});

// Cleanup inactive streams every 60 seconds
setInterval(() => {
  streamManager.cleanupInactive();
}, 60000);

// Start server
async function start() {
  process.env.TZ = TZ;
  
  // Run database migrations
  runMigrations();
  
  // Create default admin
  await createDefaultAdmin();
  
  // Start HTTP server
  app.listen(PORT, '0.0.0.0', () => {
    console.log(`
╔═══════════════════════════════════════════╗
║   🌙 StreamFlow v5.0 - Node.js Edition    ║
║   IPTV Relay Platform                     ║
║   Running on port ${PORT}                     ║
╚═══════════════════════════════════════════╝
    `);
  });
}

start().catch(console.error);

export default app;
