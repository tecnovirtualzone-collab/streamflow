import db from '../database/db.js';

// Simple in-memory URL shortener
const urlMap = new Map(); // shortCode → fullUrl
const reverseMap = new Map(); // fullUrl → shortCode

export function setupShortenerRoutes(app) {
  const BASE_URL = process.env.BASE_URL || 'http://72.60.243.73:5000';

  // Create short URL
  app.post('/api/shorten', (req, res) => {
    const { url } = req.body;
    if (!url) return res.status(400).json({ error: 'URL requerida' });

    // Check if already shortened
    if (reverseMap.has(url)) {
      const code = reverseMap.get(url);
      return res.json({ shortUrl: `${BASE_URL}/s/${code}`, code });
    }

    // Generate short code
    const code = Math.random().toString(36).substring(2, 8);
    urlMap.set(code, url);
    reverseMap.set(url, code);

    res.json({ shortUrl: `${BASE_URL}/s/${code}`, code });
  });

  // Redirect short URL
  app.get('/s/:code', (req, res) => {
    const url = urlMap.get(req.params.code);
    if (!url) return res.status(404).send('URL no encontrada');
    res.redirect(301, url);
  });
}
