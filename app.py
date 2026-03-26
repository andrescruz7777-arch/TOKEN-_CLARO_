"""
SMS Monitor Backend
Requisitos: pip install flask flask-socketio flask-cors eventlet
Ejecutar:   python app.py
"""

from flask import Flask, request, jsonify
from flask_socketio import SocketIO, emit
from flask_cors import CORS
import sqlite3
import json
import re
from datetime import datetime
import os

app = Flask(__name__)
app.config['SECRET_KEY'] = 'sms-monitor-secret-2024'
CORS(app, origins="*")
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

DB_PATH = 'sms_monitor.db'

# ─── Líneas registradas (agregar/quitar según necesidad) ─────────────────────
LINEAS_REGISTRADAS = {
    "+573123343507": "Portabilidad ♥",
    "+573227573091": "T y T ♥",
    "+573103333333": "Línea 3",
    "+573104444444": "Línea 4",
    "+573105555555": "Línea 5",
}

# ─── Patrones para extracción automática de tokens ───────────────────────────
PATRONES_TOKEN = [
    r'\b(\d{6})\b',             # 6 dígitos (OTP más común)
    r'\b(\d{4})\b',             # 4 dígitos
    r'(?:código|code|token|clave)[:\s]+([A-Z0-9\-]{4,12})',  # etiqueta + valor
    r'\b([A-Z0-9]{8,12})\b',    # alfanumérico mayúsculas
]

def extraer_token(mensaje):
    """Extrae el token/código del cuerpo del SMS usando patrones en orden de prioridad."""
    for patron in PATRONES_TOKEN:
        match = re.search(patron, mensaje, re.IGNORECASE)
        if match:
            return match.group(1)
    return None

# ─── Base de datos ────────────────────────────────────────────────────────────
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS mensajes (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                linea     TEXT NOT NULL,
                nombre    TEXT,
                remitente TEXT,
                mensaje   TEXT,
                token     TEXT,
                recibido  TEXT NOT NULL
            )
        ''')
        conn.commit()

def guardar_mensaje(data):
    linea    = data.get('linea', 'desconocida')
    nombre   = LINEAS_REGISTRADAS.get(linea, linea)
    remitente = data.get('remitente', '')
    mensaje  = data.get('mensaje', '')
    token    = extraer_token(mensaje)
    recibido = data.get('timestamp', datetime.now().isoformat())

    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            'INSERT INTO mensajes (linea, nombre, remitente, mensaje, token, recibido) VALUES (?,?,?,?,?,?)',
            (linea, nombre, remitente, mensaje, token, recibido)
        )
        conn.commit()
        return {
            'id': cur.lastrowid,
            'linea': linea,
            'nombre': nombre,
            'remitente': remitente,
            'mensaje': mensaje,
            'token': token,
            'recibido': recibido
        }

def obtener_mensajes(limite=100, linea=None):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        if linea:
            rows = conn.execute(
                'SELECT * FROM mensajes WHERE linea=? ORDER BY id DESC LIMIT ?',
                (linea, limite)
            ).fetchall()
        else:
            rows = conn.execute(
                'SELECT * FROM mensajes ORDER BY id DESC LIMIT ?', (limite,)
            ).fetchall()
        return [dict(r) for r in rows]

# ─── Rutas HTTP ───────────────────────────────────────────────────────────────
@app.route('/webhook/sms', methods=['POST'])
def webhook_sms():
    """Endpoint que llama la app Android cada vez que llega un SMS."""
    data = request.get_json(force=True, silent=True) or {}
    if not data.get('mensaje') and not data.get('linea'):
        return jsonify({'error': 'payload inválido'}), 400

    registro = guardar_mensaje(data)

    # Broadcast en tiempo real a todos los clientes conectados
    socketio.emit('nuevo_sms', registro)

    return jsonify({'ok': True, 'id': registro['id'], 'token': registro['token']}), 200

@app.route('/api/mensajes', methods=['GET'])
def api_mensajes():
    """Retorna historial de mensajes. Query param: ?linea=+573101111111&limite=50"""
    linea  = request.args.get('linea')
    limite = int(request.args.get('limite', 100))
    return jsonify(obtener_mensajes(limite=limite, linea=linea))

@app.route('/api/lineas', methods=['GET'])
def api_lineas():
    """Retorna las líneas configuradas con conteo de mensajes."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            'SELECT linea, nombre, COUNT(*) as total FROM mensajes GROUP BY linea'
        ).fetchall()
        registradas = [
            {'linea': k, 'nombre': v, 'total': 0}
            for k, v in LINEAS_REGISTRADAS.items()
        ]
        conteos = {r['linea']: r['total'] for r in rows}
        for item in registradas:
            item['total'] = conteos.get(item['linea'], 0)
        return jsonify(registradas)

@app.route('/api/stats', methods=['GET'])
def api_stats():
    with sqlite3.connect(DB_PATH) as conn:
        total    = conn.execute('SELECT COUNT(*) FROM mensajes').fetchone()[0]
        con_token = conn.execute("SELECT COUNT(*) FROM mensajes WHERE token IS NOT NULL AND token != ''").fetchone()[0]
        hoy      = conn.execute(
            "SELECT COUNT(*) FROM mensajes WHERE recibido >= date('now')"
        ).fetchone()[0]
    return jsonify({'total': total, 'con_token': con_token, 'hoy': hoy})

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'lineas': len(LINEAS_REGISTRADAS)})

# ─── WebSocket ────────────────────────────────────────────────────────────────
@socketio.on('connect')
def on_connect():
    # Al conectar, enviar los últimos 50 mensajes como estado inicial
    mensajes = obtener_mensajes(limite=50)
    emit('estado_inicial', mensajes)

@socketio.on('solicitar_historial')
def on_historial(data):
    linea  = data.get('linea')
    limite = int(data.get('limite', 100))
    emit('historial', obtener_mensajes(limite=limite, linea=linea))

# ─── Entry point ─────────────────────────────────────────────────────────────
if __name__ == '__main__':
    init_db()
    port = int(os.environ.get('PORT', 5000))
    print(f"SMS Monitor backend corriendo en http://0.0.0.0:{port}")
    print(f"Líneas configuradas: {len(LINEAS_REGISTRADAS)}")
    socketio.run(app, host='0.0.0.0', port=port, debug=False)
