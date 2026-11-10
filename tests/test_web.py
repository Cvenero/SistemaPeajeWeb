"""Verifica el puente HTTP con el ejecutable C++ y procesos reales."""
import http.client
import importlib.util
import json
import io
import os
import signal
from contextlib import closing
from pathlib import Path
import subprocess
import threading
import time
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("peaje_server", ROOT / "web/server.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


class EventosCppTest(unittest.TestCase):
    def eventos(self, modo, lista):
        resultado = subprocess.run(
            [str(ROOT / "peaje"), "--modo", modo, "--vehiculos", lista, "--json"],
            capture_output=True, text=True, timeout=15)
        self.assertEqual(resultado.returncode, 0, resultado.stderr)
        return [json.loads(linea) for linea in resultado.stdout.splitlines()]

    def test_seccion_critica_y_totales(self):
        eventos = self.eventos("seguro", "5,2,7,3")
        propietario = None
        cobros = []
        pids = set()
        for evento in eventos:
            if evento["tipo"] == "carril_inicio":
                pids.add(evento["pid"])
            if evento["tipo"] == "registrando":
                self.assertIsNone(propietario, "Dos procesos entraron en la sección crítica")
                propietario = evento["carril"]
            if evento["tipo"] == "cobro":
                self.assertEqual(propietario, evento["carril"])
                cobros.append(evento)
                self.assertEqual(evento["total"], len(cobros))
                self.assertEqual(evento["centimos"], len(cobros) * 500)
                propietario = None
        self.assertIsNone(propietario)
        self.assertEqual(len(pids), 4)
        self.assertEqual(len(cobros), 17)
        self.assertEqual(eventos[-1]["tipo"], "resumen")
        self.assertEqual(eventos[-1]["centimos"], 8500)

    def test_colision_con_cantidades_distintas(self):
        eventos = self.eventos("colision", "8,2,5,1")
        self.assertEqual(sum(e["tipo"] == "cobro" for e in eventos), 16)
        self.assertEqual(eventos[-1]["total"], 8)
        self.assertEqual(eventos[-1]["centimos"], 4000)

    def test_fallo_de_un_hijo_cancela_a_los_demas(self):
        proceso = subprocess.Popen(
            [str(ROOT / "peaje"), "--modo", "colision", "--vehiculos", "30,40,20",
             "--json", "--demora-ms", "600"], stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True)
        pids = []
        try:
            while len(pids) < 3:
                evento = json.loads(proceso.stdout.readline())
                if evento["tipo"] == "carril_inicio":
                    pids.append(evento["pid"])
            # Termina el último hijo mientras el padre podría esperar al primero.
            os.kill(pids[-1], signal.SIGKILL)
            proceso.communicate(timeout=3)
            self.assertEqual(proceso.returncode, 2)
            for pid in pids:
                self.assertFalse(Path(f"/proc/{pid}").exists())
        finally:
            if proceso.poll() is None:
                proceso.terminate()
                proceso.communicate(timeout=3)


class InterfazWebTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.httpd = server.Servidor(("127.0.0.1", 0))
        except PermissionError:
            raise unittest.SkipTest("El entorno no permite sockets; se verifica HTTP en memoria")
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.simulaciones.detener()
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=3)

    def conexion(self):
        return http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=10)

    def post(self, ruta, datos, **headers):
        conexion = self.conexion()
        conexion.request("POST", ruta, json.dumps(datos), {"Content-Type": "application/json", **headers})
        return conexion, conexion.getresponse()

    def esperar_liberacion(self):
        limite = time.monotonic() + 3
        while self.httpd.simulaciones.proceso is not None and time.monotonic() < limite:
            time.sleep(.01)
        self.assertIsNone(self.httpd.simulaciones.proceso)

    def test_archivos_locales(self):
        for ruta, tipo in (("/", "text/html"), ("/app.js", "text/javascript"), ("/style.css", "text/css")):
            with closing(self.conexion()) as conexion:
                conexion.request("GET", ruta)
                respuesta = conexion.getresponse()
                self.assertEqual(respuesta.status, 200)
                self.assertIn(tipo, respuesta.getheader("Content-Type"))
                self.assertTrue(respuesta.read())
        with closing(self.conexion()) as conexion:
            conexion.request("GET", "/../src/main.cpp")
            respuesta = conexion.getresponse()
            self.assertEqual(respuesta.status, 404)
            respuesta.read()

    def test_configuraciones_invalidas(self):
        for datos in ({"cantidades": []}, {"cantidades": [True]}, {"cantidades": [101]},
                      {"cantidades": [1] * 9}, {"cantidades": [2], "modo": "colision"},
                      {"cantidades": [2], "demora_ms": 0}, {"cantidades": [2], "modo": "otro"}):
            conexion, respuesta = self.post("/api/simular", datos)
            self.assertEqual(respuesta.status, 400)
            self.assertIn("error", json.loads(respuesta.read()))
            conexion.close()
        conexion, respuesta = self.post("/api/simular", {"cantidades": [1]}, Origin="http://otro-origen.example")
        self.assertEqual(respuesta.status, 403)
        respuesta.read()
        conexion.close()

    def test_simulacion_completa(self):
        for modo in ("seguro", "colision", "libre"):
            conexion, respuesta = self.post("/api/simular", {"cantidades": [2, 3, 1], "modo": modo, "demora_ms": 100})
            self.assertEqual(respuesta.status, 200)
            eventos = [json.loads(linea) for linea in respuesta]
            conexion.close()
            self.assertEqual(eventos[0]["tipo"], "configuracion")
            self.assertEqual(eventos[-1]["tipo"], "fin")
            self.assertEqual(eventos[-1]["codigo"], 0)
            self.assertFalse(eventos[-1]["cancelada"])
            resumen = next(e for e in eventos if e["tipo"] == "resumen")
            if modo == "seguro":
                self.assertEqual(resumen["total"], 6)
                self.assertEqual(resumen["centimos"], 3000)
            if modo == "colision":
                self.assertEqual(resumen["total"], 3)
            self.esperar_liberacion()

    def test_detener_recoge_hijos_y_permite_reiniciar(self):
        conexion, respuesta = self.post("/api/simular", {"cantidades": [40, 35], "demora_ms": 600})
        configuracion = json.loads(respuesta.readline())
        pids = []
        while len(pids) < 2:
            evento = json.loads(respuesta.readline())
            if evento["tipo"] == "carril_inicio":
                pids.append(evento["pid"])
        otra, ocupada = self.post("/api/simular", {"cantidades": [1]})
        self.assertEqual(ocupada.status, 409)
        ocupada.read()
        otra.close()
        otra, detencion = self.post("/api/detener", {"id": configuracion["id"]})
        self.assertTrue(json.loads(detencion.read())["detenida"])
        otra.close()
        eventos = [json.loads(linea) for linea in respuesta]
        conexion.close()
        self.assertTrue(eventos[-1]["cancelada"])
        self.esperar_liberacion()
        for pid in pids:
            self.assertFalse(Path(f"/proc/{pid}").exists(), f"El proceso {pid} quedó sin recoger")
        self.test_simulacion_completa()

    def test_desconexion_cancela_simulacion(self):
        conexion, respuesta = self.post("/api/simular", {"cantidades": [30, 20], "demora_ms": 100})
        self.assertEqual(json.loads(respuesta.readline())["tipo"], "configuracion")
        respuesta.close()
        conexion.close()
        self.esperar_liberacion()


class ConexionEnMemoria:
    """Transporte HTTP sin socket para entornos que bloquean puertos locales."""
    def __init__(self, peticion, callback=None):
        self.entrada = io.BytesIO(peticion)
        self.salida = bytearray()
        self.callback = callback

    def makefile(self, *_):
        return self.entrada

    def sendall(self, datos):
        self.salida.extend(datos)
        if self.callback and datos.startswith(b'{"tipo"'):
            self.callback(json.loads(datos))


class HttpEnMemoriaTest(unittest.TestCase):
    def setUp(self):
        self.servidor = SimpleNamespace(simulaciones=server.Simulaciones())

    def peticion(self, ruta, datos=None, callback=None, origen=None):
        cuerpo = json.dumps(datos).encode() if datos is not None else b""
        metodo = "POST" if datos is not None else "GET"
        cabeceras = f"{metodo} {ruta} HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\nContent-Length: {len(cuerpo)}\r\n"
        if origen:
            cabeceras += f"Origin: {origen}\r\n"
        conexion = ConexionEnMemoria(cabeceras.encode() + b"\r\n" + cuerpo, callback)
        server.Handler(conexion, ("127.0.0.1", 12345), self.servidor)
        cabeceras, cuerpo = bytes(conexion.salida).split(b"\r\n\r\n", 1)
        codigo = int(cabeceras.split(b" ")[1])
        return codigo, cuerpo

    def test_http_y_archivos(self):
        for ruta in ("/", "/app.js", "/style.css", "/api/estado"):
            codigo, cuerpo = self.peticion(ruta)
            self.assertEqual(codigo, 200)
            self.assertTrue(cuerpo)
        self.assertEqual(self.peticion("/../src/main.cpp")[0], 404)
        self.assertEqual(self.peticion("/api/simular", {"cantidades": [0]})[0], 400)
        self.assertEqual(self.peticion("/api/simular", {"cantidades": [2]}, origen="http://otro.example")[0], 403)

    def test_eventos_http_completos_y_reinicio(self):
        for modo in ("seguro", "colision", "libre"):
            codigo, cuerpo = self.peticion("/api/simular", {"cantidades": [2, 3, 1], "modo": modo, "demora_ms": 100})
            self.assertEqual(codigo, 200)
            eventos = [json.loads(linea) for linea in cuerpo.splitlines()]
            self.assertEqual(eventos[0]["tipo"], "configuracion")
            self.assertEqual(eventos[-1]["codigo"], 0)
            self.assertFalse(eventos[-1]["cancelada"])
            self.assertEqual(sum(e["tipo"] == "cobro" for e in eventos), 6)
            resumen = next(e for e in eventos if e["tipo"] == "resumen")
            if modo == "seguro":
                self.assertEqual(resumen["centimos"], 3000)
            if modo == "colision":
                self.assertEqual(resumen["total"], 3)
            self.assertIsNone(self.servidor.simulaciones.proceso)

    def test_detener_y_recoger_todos_los_procesos(self):
        pids = []
        identificador = None

        def al_evento(evento):
            nonlocal identificador
            if evento["tipo"] == "configuracion":
                identificador = evento["id"]
            if evento["tipo"] == "carril_inicio":
                pids.append(evento["pid"])
                if len(pids) == 2:
                    # El mismo gestor atiende una segunda petición de detención.
                    self.assertEqual(self.peticion("/api/simular", {"cantidades": [1]})[0], 409)
                    codigo, respuesta = self.peticion("/api/detener", {"id": identificador})
                    self.assertEqual(codigo, 200)
                    self.assertTrue(json.loads(respuesta)["detenida"])

        codigo, cuerpo = self.peticion("/api/simular", {"cantidades": [30, 40], "demora_ms": 600}, al_evento)
        self.assertEqual(codigo, 200)
        eventos = [json.loads(linea) for linea in cuerpo.splitlines()]
        self.assertTrue(eventos[-1]["cancelada"])
        self.assertIsNone(self.servidor.simulaciones.proceso)
        self.assertEqual(len(pids), 2)
        for pid in pids:
            self.assertFalse(Path(f"/proc/{pid}").exists(), "Quedó un hijo sin recoger")
        self.test_eventos_http_completos_y_reinicio()


if __name__ == "__main__":
    unittest.main()
