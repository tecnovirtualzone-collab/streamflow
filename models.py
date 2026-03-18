from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

class Usuario(db.Model):
    __tablename__ = 'usuarios'
    id             = db.Column(db.Integer, primary_key=True)
    usuario        = db.Column(db.String(50), unique=True, nullable=False)
    contrasena     = db.Column(db.String(200), nullable=False)
    paquete        = db.Column(db.String(20), default='basico')
    max_conexiones = db.Column(db.Integer, default=1)
    fecha_expira   = db.Column(db.DateTime, nullable=False)
    activo         = db.Column(db.Boolean, default=True)
    creado         = db.Column(db.DateTime, default=datetime.utcnow)
    notas          = db.Column(db.String(300), default='')
    macs           = db.relationship('MacRegistrada', backref='usuario', lazy=True, cascade='all, delete-orphan')
    sesiones       = db.relationship('SesionActiva', backref='usuario', lazy=True, cascade='all, delete-orphan')

class MacRegistrada(db.Model):
    __tablename__ = 'macs'
    id         = db.Column(db.Integer, primary_key=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey('usuarios.id'), nullable=False)
    mac        = db.Column(db.String(17), nullable=False)
    nombre     = db.Column(db.String(50), default='Dispositivo')
    registrada = db.Column(db.DateTime, default=datetime.utcnow)

class SesionActiva(db.Model):
    __tablename__ = 'sesiones'
    id          = db.Column(db.Integer, primary_key=True)
    usuario_id  = db.Column(db.Integer, db.ForeignKey('usuarios.id'), nullable=False)
    ip          = db.Column(db.String(45), nullable=False)
    mac         = db.Column(db.String(17))
    canal       = db.Column(db.String(200))
    ultimo_ping = db.Column(db.DateTime, default=datetime.utcnow)
