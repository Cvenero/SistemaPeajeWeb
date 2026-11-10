"""Puente local HTTP -> procesos C++ -> eventos JSON. Solo biblioteca estándar."""
import argparse
import json
import signal
import subprocess
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
BINARY = ROOT.parent / "peaje"
ESCALA_TIEMPO = 2


class Simulaciones:
    def __init__(self):
        self.lock = threading.Lock()
        self.proceso = None
        self.identificador = None
        self.cancelada = False

    def iniciar(self, cantidades, modo, demora):
        with self.lock:
            if self.proceso is not None:
                raise RuntimeError("Ya hay una simulación en curso. Detenla antes de iniciar otra.")
            proceso = subprocess.Popen(
                [str(BINARY), "--modo", modo, "--carriles", str(len(cantidades)),
                 "--vehiculos", ",".join(map(str, cantidades)), "--json",
                 "--demora-ms", str(demora), "--escala-tiempo", str(ESCALA_TIEMPO)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                bufsize=1, start_new_session=True, cwd=ROOT.parent)
            self.proceso = proceso
            self.identificador = uuid.uuid4().hex
            self.cancelada = False
            return proceso, self.identificador

    def detener(self, identificador=None):
        with self.lock:
            if identificador is not None and identificador != self.identificador:
                return False
            proceso = self.proceso
            if proceso is None:
                return False
            self.cancelada = True
            # El padre C++ cancela y recoge a sus hijos antes de salir.
            if proceso.poll() is None:
                proceso.terminate()
            return True

    def liberar(self, proceso):
        with self.lock:
            if self.proceso is proceso:
                self.proceso = None
                self.identificador = None


def validar(datos):
    if not isinstance(datos, dict):
        raise ValueError("La configuración debe ser un objeto.")
    cantidades = datos.get("cantidades")
    if not isinstance(cantidades, list) or not 1 <= len(cantidades) <= 8:
        raise ValueError("Selecciona entre 1 y 8 carriles.")
    if any(type(n) is not int or not 1 <= n <= 100 for n in cantidades):
        raise ValueError("Cada carril debe tener entre 1 y 100 vehículos.")
    modo = datos.get("modo", "seguro")
    if modo not in ("seguro", "libre", "colision"):
        raise ValueError("Modo de simulación inválido.")
    if modo == "colision" and len(cantidades) < 2:
        raise ValueError("La colisión requiere al menos 2 carriles.")
    demora = datos.get("demora_ms", 600)
    if type(demora) is not int or not 100 <= demora <= 1500:
        raise ValueError("El tiempo de atención debe estar entre 100 y 1500 ms.")
    return cantidades, modo, demora


class Servidor(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, direccion):
        self.simulaciones = Simulaciones()
        super().__init__(direccion, Handler)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_):
        pass

    def responder(self, codigo, datos):
        cuerpo = json.dumps(datos, ensure_ascii=False).encode()
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(cuerpo)

    def do_GET(self):
        ruta = urlsplit(self.path).path
        if ruta == "/api/estado":
            with self.server.simulaciones.lock:
                activa = self.server.simulaciones.proceso is not None
            self.responder(200, {"disponible": BINARY.is_file(), "activa": activa})
            return
        archivos = {"/": ("index.html", "text/html"),
                    "/app.js": ("app.js", "text/javascript"),
                    "/style.css": ("style.css", "text/css")}
        if ruta not in archivos:
            self.responder(404, {"error": "Recurso no encontrado"})
            return
        nombre, tipo = archivos[ruta]
        contenido = (ROOT / nombre).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", tipo + "; charset=utf-8")
        self.send_header("Content-Length", str(len(contenido)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(contenido)

    def do_POST(self):
        # La interfaz y la API se sirven desde el mismo origen local.
        origen = self.headers.get("Origin")
        if origen and origen != "http://" + self.headers.get("Host", ""):
            self.close_connection = True
            self.responder(403, {"error": "Origen no permitido"})
            return
        try:
            longitud = int(self.headers.get("Content-Length", "0"))
            if not 0 < longitud <= 4096:
                raise ValueError("Configuración vacía o demasiado grande.")
            datos = json.loads(self.rfile.read(longitud))
            if not isinstance(datos, dict):
                raise ValueError("La configuración debe ser un objeto.")
        except (ValueError, UnicodeDecodeError) as error:
            self.close_connection = True
            self.responder(400, {"error": str(error)})
            return
        if self.path == "/api/detener":
            identificador = datos.get("id")
            if not isinstance(identificador, str):
                self.responder(400, {"error": "Falta el identificador de la simulación."})
                return
            self.responder(200, {"detenida": self.server.simulaciones.detener(identificador)})
            return
        if self.path != "/api/simular":
            self.responder(404, {"error": "Recurso no encontrado"})
            return
        try:
            cantidades, modo, demora = validar(datos)
            proceso, identificador = self.server.simulaciones.iniciar(cantidades, modo, demora)
        except ValueError as error:
            self.responder(400, {"error": str(error)})
            return
        except RuntimeError as error:
            self.responder(409, {"error": str(error)})
            return
        except OSError:
            self.responder(503, {"error": "No se pudo iniciar C++. Ejecuta make antes de abrir la interfaz."})
            return
        limite = threading.Timer(900, self.server.simulaciones.detener, args=(identificador,))
        limite.daemon = True
        limite.start()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            self.enviar({"tipo": "configuracion", "id": identificador, "cantidades": cantidades,
                         "modo": modo, "demora_ms": demora, "escala_tiempo": ESCALA_TIEMPO})
            for linea in proceso.stdout:
                self.wfile.write(linea.encode())
                self.wfile.flush()
            codigo = proceso.wait()
            error = proceso.stderr.read().strip()
            self.enviar({"tipo": "fin", "codigo": codigo,
                         "cancelada": self.server.simulaciones.cancelada,
                         "error": error if codigo else ""})
        except (BrokenPipeError, ConnectionResetError):
            self.server.simulaciones.detener(identificador)
        finally:
            limite.cancel()
            if proceso.poll() is None:
                proceso.terminate()
            try:
                proceso.wait(timeout=3)
            except subprocess.TimeoutExpired:
                # Respaldo ante un error externo; normalmente el padre recoge todo.
                import os
                os.killpg(proceso.pid, signal.SIGKILL)
                proceso.wait()
            proceso.stdout.close()
            proceso.stderr.close()
            self.server.simulaciones.liberar(proceso)

    def enviar(self, datos):
        self.wfile.write((json.dumps(datos, ensure_ascii=False) + "\n").encode())
        self.wfile.flush()


def main():
    parser = argparse.ArgumentParser(description="Visualización local del simulador de peaje")
    parser.add_argument("--puerto", type=int, default=8000)
    args = parser.parse_args()
    if not BINARY.is_file():
        parser.error("Falta el ejecutable peaje. Ejecuta make primero.")
    servidor = Servidor(("127.0.0.1", args.puerto))
    print(f"PeajeLab disponible en http://127.0.0.1:{servidor.server_port}", flush=True)
    print("Presiona Ctrl+C para cerrar.", flush=True)
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        servidor.simulaciones.detener()
        with servidor.simulaciones.lock:
            proceso = servidor.simulaciones.proceso
        if proceso:
            proceso.wait(timeout=3)
        servidor.server_close()


if __name__ == "__main__":
    main()
