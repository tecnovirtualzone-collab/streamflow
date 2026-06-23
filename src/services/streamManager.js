import { spawn } from 'child_process';
import crypto from 'crypto';
import db from '../database/db.js';
import { STREAM_INACTIVITY_TIMEOUT_MS, STREAM_MAX_CONCURRENT } from '../config/constants.js';

class StreamManager {
  constructor() {
    this.streams = new Map(); // streamId -> { process, userId, channelId, startTime, lastActivity }
  }

  /**
   * Start a new stream relay via FFmpeg
   * Key feature: deduplication - same channel from same IP reuses the same stream
   */
  async startStream({ userId, username, channelId, channelName, streamUrl, ip }) {
    // Check if there's already a stream for this channel + IP (Smart Relay)
    const existing = this.findExistingStream(channelId, ip);
    if (existing) {
      existing.lastActivity = Date.now();
      return { streamId: existing.id, status: 'reused' };
    }

    // Get user's plan max_connections limit
    const user = db.prepare('SELECT plan FROM users WHERE id = ?').get(userId);
    const planName = user?.plan || 'basico';
    const plan = db.prepare('SELECT max_connections FROM plans WHERE name = ?').get(planName);
    const maxDevices = plan?.max_connections || 1;

    // Check concurrent connection limit for this user (by IP = device)
    const userActiveIps = this.getUserActiveIps(userId);
    if (userActiveIps.length >= maxDevices && !userActiveIps.includes(ip)) {
      return { streamId: null, status: 'error', error: `Limite de dispositivos alcanzado (${maxDevices}). Cierra la reproduccion en otro dispositivo para continuar.` };
    }

    // Also check total concurrent streams per user (safety)
    const userStreams = this.getUserStreams(userId);
    if (userStreams.length >= maxDevices) {
      // Kill oldest stream for this user
      const oldest = userStreams[0];
      await this.stopStream(oldest.id);
    }

    const streamId = crypto.randomUUID();

    try {
      // FFmpeg relay: read stream and output to MPEG-TS for HLS/player
      // Headers adaptativos según el proveedor
      const isFreeIPTV = streamUrl.includes('138.121.15.230') || 
                         streamUrl.includes('iptv-org') ||
                         !streamUrl.includes('ultratvsv.site');
      
      let headerStr;
      if (isFreeIPTV) {
        // Canales libres - headers genéricos sin referer específico
        headerStr = 'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36\r\nAccept: */*\r\nConnection: keep-alive\r\n';
      } else {
        // Proveedor Red4TV - requiere referer específico
        headerStr = 'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36\r\nReferer: http://red4tv.lat:80/\r\n';
      }

      const ffmpegArgs = [
        '-headers', headerStr,
        '-reconnect', '1',
        '-reconnect_streamed', '1',
        '-reconnect_delay_max', '5',
        '-i', streamUrl,
        '-c', 'copy',           // Copy codec (no transcoding = low CPU)
        '-f', 'mpegts',
        '-'
      ];

      const ffmpeg = spawn('ffmpeg', ffmpegArgs, {
        stdio: ['ignore', 'pipe', 'pipe']
      });

      const streamData = {
        id: streamId,
        process: ffmpeg,
        userId,
        username,
        channelId,
        channelName,
        startTime: Date.now(),
        lastActivity: Date.now(),
        ip,
        output: ffmpeg.stdout
      };

      this.streams.set(streamId, streamData);

      // Track in database
      db.prepare(`
        INSERT OR REPLACE INTO current_streams (id, user_id, username, channel_name, start_time, last_activity, ip, worker_pid, channel_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
      `).run(streamId, userId, username, channelName, streamData.startTime, streamData.lastActivity, ip, process.pid, channelId);

      // Update stats
      const stat = db.prepare('SELECT id FROM stream_stats WHERE channel_id = ?').get(channelId);
      if (stat) {
        db.prepare('UPDATE stream_stats SET views = views + 1, last_viewed = ? WHERE channel_id = ?').run(Date.now(), channelId);
      } else {
        db.prepare('INSERT INTO stream_stats (channel_id, views, last_viewed) VALUES (?, 1, ?)').run(channelId, Date.now());
      }

      // Handle stream end
      ffmpeg.on('close', () => {
        this.streams.delete(streamId);
        db.prepare('DELETE FROM current_streams WHERE id = ?').run(streamId);
      });

      ffmpeg.stderr.on('data', () => {
        // Consume stderr to prevent buffer overflow
      });

      return { streamId, status: 'started' };
    } catch (err) {
      console.error('Stream start error:', err.message);
      return { streamId, status: 'error', error: err.message };
    }
  }

  /**
   * Stop a stream
   */
  async stopStream(streamId) {
    const stream = this.streams.get(streamId);
    if (!stream) return false;

    try {
      stream.process.kill('SIGTERM');
      this.streams.delete(streamId);
      db.prepare('DELETE FROM current_streams WHERE id = ?').run(streamId);
      return true;
    } catch (err) {
      console.error('Stream stop error:', err.message);
      return false;
    }
  }

  /**
   * Find existing stream for same channel + IP (Smart Relay deduplication)
   */
  findExistingStream(channelId, ip) {
    const chId = Number(channelId);
    for (const stream of this.streams.values()) {
      if (stream.channelId === chId && stream.ip === ip) {
        return stream;
      }
    }
    return null;
  }

  /**
   * Get all streams for a user
   */
  getUserStreams(userId) {
    const result = [];
    for (const stream of this.streams.values()) {
      if (stream.userId === userId) {
        result.push(stream);
      }
    }
    return result.sort((a, b) => a.startTime - b.startTime);
  }

  /**
   * Get unique IPs (devices) a user is streaming from
   */
  getUserActiveIps(userId) {
    const ips = new Set();
    for (const stream of this.streams.values()) {
      if (stream.userId === userId) {
        ips.add(stream.ip);
      }
    }
    return Array.from(ips);
  }

  /**
   * Get all active streams
   */
  getAllStreams() {
    return Array.from(this.streams.values()).map(s => ({
      id: s.id,
      userId: s.userId,
      username: s.username,
      channelName: s.channelName,
      startTime: s.startTime,
      lastActivity: s.lastActivity,
      ip: s.ip
    }));
  }

  /**
   * Cleanup inactive streams (called by scheduler)
   */
  cleanupInactive() {
    const now = Date.now();
    for (const [id, stream] of this.streams.entries()) {
      if (now - stream.lastActivity > STREAM_INACTIVITY_TIMEOUT_MS) {
        this.stopStream(id);
      }
    }
  }

  /**
   * Get stream by ID
   */
  getStream(streamId) {
    return this.streams.get(streamId) || null;
  }
}

// Singleton
const streamManager = new StreamManager();
export default streamManager;
