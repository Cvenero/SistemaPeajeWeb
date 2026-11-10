"""Pruebas de caja negra: procesos reales y resultados observables en Linux."""
import subprocess
import re
import unittest


class SimuladorTest(unittest.TestCase):
    def ejecutar(self, *args):
        return subprocess.run(["./peaje", *args], capture_output=True,
                              text=True, timeout=30)

    def test_seguro_repetido(self):
        for carriles in (1, 2, 8):
            for _ in range(3):
                r = self.ejecutar("--modo", "seguro", "--carriles", str(carriles),
                                  "--vehiculos", "2000")
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn(f"Contador global: {carriles * 2000} / esperado: {carriles * 2000}", r.stdout)
                self.assertIn("Integridad: CORRECTA", r.stdout)

    def test_colision_controlada(self):
        r = self.ejecutar("--modo", "colision", "--carriles", "4", "--vehiculos", "100")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Suma independiente por carril: 400", r.stdout)
        self.assertIn("Contador global: 100 / esperado: 400", r.stdout)
        self.assertIn("Recaudacion: S/ 500.00 / esperada: S/ 2000.00", r.stdout)

    def test_demo(self):
        r = self.ejecutar("--modo", "demo", "--vehiculos", "50")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Demostracion: VERIFICADA", r.stdout)

    def test_procesos_eventos_y_recaudacion_por_carril(self):
        r = self.ejecutar("--modo", "seguro", "--carriles", "3",
                          "--vehiculos", "7", "--eventos")
        self.assertEqual(r.returncode, 0, r.stderr)
        eventos = re.findall(r"PID (\d+) \| carril (\d+) \| vehiculo (\d+) \| cobro S/ 5.00", r.stdout)
        self.assertEqual(len(eventos), 21)
        pids = set()
        for carril in range(1, 4):
            registros = [(pid, int(v)) for pid, c, v in eventos if int(c) == carril]
            self.assertEqual(sorted(v for _, v in registros), list(range(1, 8)))
            self.assertEqual(len({pid for pid, _ in registros}), 1)
            pid = registros[0][0]
            pids.add(pid)
            self.assertIn(f"Carril {carril}: 7 vehiculos | PID {pid} | recaudacion S/ 35.00", r.stdout)
        self.assertEqual(len(pids), 3)
        self.assertIn("Suma de cobros por carril: S/ 105.00", r.stdout)
        self.assertIn("Recaudacion: S/ 105.00 / esperada: S/ 105.00", r.stdout)

    def test_maximo_de_carriles(self):
        r = self.ejecutar("--modo", "seguro", "--carriles", "32", "--vehiculos", "100")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Contador global: 3200 / esperado: 3200", r.stdout)

    def test_argumentos_invalidos(self):
        for args in (("--carriles", "0"), ("--vehiculos", "abc"),
                     ("--modo", "otro"), ("--modo", "colision", "--carriles", "1"),
                     ("--vehiculos",), ("--carriles", "33")):
            self.assertEqual(self.ejecutar(*args).returncode, 2)

    def test_cantidades_distintas(self):
        for cantidades in ((3, 1, 7), (1, 9), (20, 2, 20, 1), (100, 125, 150, 175)):
            lista = ",".join(map(str, cantidades))
            for modo in ("seguro", "colision", "demo", "libre"):
                r = self.ejecutar("--modo", modo, "--vehiculos", lista)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn(f"Suma independiente por carril: {sum(cantidades)}", r.stdout)
                for i, cantidad in enumerate(cantidades, 1):
                    self.assertIn(f"Carril {i}: {cantidad} vehiculos", r.stdout)
                if modo == "colision":
                    self.assertIn(f"Contador global: {max(cantidades)} / esperado: {sum(cantidades)}", r.stdout)
                if modo == "demo":
                    self.assertIn("Demostracion: VERIFICADA", r.stdout)

    def test_carga_predeterminada_variable(self):
        r = self.ejecutar()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("vehiculos por carril: 100, 125, 150, 175", r.stdout)
        self.assertIn("Contador global: 550 / esperado: 550", r.stdout)

    def test_listas_invalidas(self):
        for lista in ("1,", ",2", "1,,2", "0,2", "1,abc", "1,1000001", ",".join(["1"] * 33)):
            self.assertEqual(self.ejecutar("--vehiculos", lista).returncode, 2)
        self.assertEqual(self.ejecutar("--carriles", "3", "--vehiculos", "2,4").returncode, 2)
        r = self.ejecutar("--modo", "seguro", "--carriles", "3", "--vehiculos", "2,4,6")
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
