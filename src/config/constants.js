import dotenv from 'dotenv';
dotenv.config();

export const PORT = parseInt(process.env.PORT || '5000');
export const DATA_DIR = process.env.DATA_DIR || '/data';
export const JWT_SECRET = process.env.JWT_SECRET || 'streamflow-secret-change-me';
export const JWT_EXPIRES_IN = process.env.JWT_EXPIRES_IN || '24h';
export const BCRYPT_ROUNDS = parseInt(process.env.BCRYPT_ROUNDS || '10');
export const ENCRYPTION_KEY = process.env.ENCRYPTION_KEY || '';
export const INITIAL_ADMIN_PASSWORD = process.env.INITIAL_ADMIN_PASSWORD || 'admin123';
export const API_RATE_LIMIT_MAX = parseInt(process.env.API_RATE_LIMIT_MAX || '1000');
export const API_RATE_LIMIT_WINDOW_MS = parseInt(process.env.API_RATE_LIMIT_WINDOW_MS || '60000');
export const AUTH_RATE_LIMIT_MAX = parseInt(process.env.AUTH_RATE_LIMIT_MAX || '10');
export const AUTH_RATE_LIMIT_WINDOW_MS = parseInt(process.env.AUTH_RATE_LIMIT_WINDOW_MS || '60000');
export const STREAM_INACTIVITY_TIMEOUT_MS = parseInt(process.env.STREAM_INACTIVITY_TIMEOUT_MS || '120000');
export const STREAM_MAX_CONCURRENT = parseInt(process.env.STREAM_MAX_CONCURRENT || '3');
export const TZ = process.env.TZ || 'America/Bogota';
