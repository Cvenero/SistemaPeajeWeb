# Simulador de peaje: sincronización de procesos

Su objetivo central es demostrar la sincronización de procesos mediante semáforos POSIX, aplicando exclusión mutua para garantizar la consistencia de los datos en memoria compartida.

El informe final incluye el PID, los vehículos y la recaudación de cada carril. Los registros individuales se contrastan con los valores esperados y, en el modo seguro, con los totales globales.

## Componentes del sistema

| Requisito | Implementación |
|---|---|
| Procesos independientes por carril | `fork()` y PID individual en el informe y los eventos. |
| Memoria compartida | `Compartido` en un `mmap` compartido, inicializado antes de crear hijos. |
| Exclusión mutua | Semáforo POSIX con `pshared=1`; protege juntos el contador y la recaudación en modo `seguro`. |
| Espera y limpieza | `waitpid()`, comprobación de estados, `sem_destroy()` y `munmap()`. |
| Atención, cobros y estadísticas | Eventos opcionales y estadísticas globales e individuales con tarifa fija de S/ 5.00. |
| Plan de pruebas | Pruebas de procesos distintos, eventos, cobros, consistencia y carga concurrente; experimento reproducible de actualizaciones perdidas. |

Para ejecutar directamente el sistema con sincronización y visualizar cada cobro:

```bash
make
./peaje --modo seguro --carriles 4 --vehiculos 10 --eventos
./peaje --modo seguro --vehiculos 12,7,20,9 --eventos
```

## Compilar y ejecutar

En una terminal Linux (Ubuntu, WSL2 o una máquina virtual), con `g++` y `make` instalados:

```bash
make
./peaje
```

El modo predeterminado es una demostración con 4 carriles y cargas distintas: 100, 125, 150 y 175 vehículos (550 en total). Sin `--vehiculos`, se asignan 100 vehículos al primer carril y 25 adicionales por cada carril siguiente. La tarifa fija es S/ 5.00, almacenada internamente como 500 céntimos.

En Windows, abrir la carpeta del proyecto desde Linux/WSL; para esta ubicación normalmente sería:

```bash
cd /mnt/d/GPT/SO/SistemaPeaje
make
./peaje
```

Si las herramientas no están instaladas, en Ubuntu se pueden instalar con `sudo apt install build-essential python3`. Python solo se necesita para las pruebas.

## Cantidades de vehículos por carril

Puedes especificar una lista de cantidades; su longitud determina el número de carriles:

```bash
./peaje --modo seguro --vehiculos 12,7,20,9 --eventos
./peaje --modo demo --vehiculos 12,7,20,9
```

Los cuatro carriles atienden respectivamente 12, 7, 20 y 9 vehículos. Se esperan 48 vehículos y S/ 240.00. Cada proceso termina cuando completa su propia cantidad. El semáforo sigue protegiendo únicamente la actualización de los totales en modo seguro.

Un único número, como `--vehiculos 100`, mantiene la misma cantidad en todos los carriles. Si también se indica `--carriles`, debe coincidir con la longitud de la lista. Cada cantidad admite de 1 a 1000000 vehículos, con un máximo de 32 carriles.

En el experimento `colision`, cada ronda reúne solo a los carriles que todavía tienen vehículos. Así, los carriles que ya terminaron no bloquean a los demás. El contador experimental termina con el máximo de las cantidades individuales, mientras que el modo seguro registra su suma.

## Los tres experimentos

| Modo | Qué demuestra |
|---|---|
| `seguro` | Un semáforo de exclusión mutua protege conjuntamente el contador global y la recaudación. |
| `libre` | Las actualizaciones no están protegidas. Una pausa entre lectura y escritura amplía la ventana de carrera; los resultados pueden variar. |
| `colision` | Experimento controlado: una barrera obliga a todos los carriles a leer antes de que cualquiera escriba. Permite reproducir la pérdida de actualizaciones. |
| `demo` | Ejecuta `colision` y después `seguro`, con memoria nueva en cada ejecución, y valida los resultados de ambos. |

**En `colision` sí hay sincronización de las fases del experimento**, pero no exclusión mutua alrededor de la operación completa de lectura, modificación y escritura. Esto permite explicar que coordinar procesos y proteger una sección crítica son usos distintos de la sincronización. El modo `libre` no utiliza la barrera ni protege los totales.

```bash
./peaje --modo demo --carriles 4 --vehiculos 100
./peaje --modo seguro --carriles 3 --vehiculos 5 --eventos
./peaje --modo libre --carriles 8 --vehiculos 1000
./peaje --ayuda
```

`--eventos` muestra PID, carril, vehículo y cobro. El orden de las líneas depende de la planificación y no prueba, por sí solo, que exista exclusión mutua.

## Resultado esperado de la demostración

Con `./peaje --modo demo --vehiculos 100`, cuatro carriles y cien vehículos por carril:

| Medida | Colisión controlada | Con semáforo |
|---|---:|---:|
| Suma de vehículos por carril | 400 | 400 |
| Contador global | 100 | 400 |
| Recaudación global | S/ 500.00 | S/ 2000.00 |
| Integridad | Actualizaciones perdidas | Correcta |

Los 400 cobros se simulan en ambos casos. En la colisión, cuatro procesos leen el mismo valor y escriben el mismo incremento: solo queda registrado un incremento por ronda. Un segundo encuentro en la barrera impide que la siguiente ronda empiece antes de terminar todas las escrituras de la anterior.

## Cómo funciona la solución

1. El padre crea un `mmap` con `MAP_SHARED | MAP_ANONYMOUS` e inicializa datos y semáforos.
2. `sem_init(..., 1, 1)` inicializa el semáforo de exclusión mutua para uso entre procesos. El propio `sem_t` también reside en el mapa compartido.
3. El padre llama a `fork()` una vez por carril. Cada hijo atiende sus vehículos.
4. En modo seguro, cada cobro ejecuta `sem_wait`, actualiza los dos totales y ejecuta `sem_post`.
5. Cada hijo actualiza además su contador individual. Solo ese hijo escribe esa casilla; el padre la consulta tras esperar a todos los hijos.
6. El padre usa `waitpid()` para recoger los hijos, contrasta los resultados y libera los semáforos y el mapa.

La sección crítica está en `src/main.cpp`, dentro de `simular()`:

```cpp
esperar(&c->mutex);
++c->vehiculos;
c->centimos += tarifa;
liberar(&c->mutex);
```

Solo un proceso puede ejecutar ese bloque a la vez. La atención de vehículos y la salida de eventos quedan fuera de él. Una variable ordinaria, aunque exista antes del `fork`, no sustituye al mapa compartido: cada proceso tendría su propia copia.

`volatile`, usado exclusivamente en los modos experimentales, no hace que un incremento sea atómico ni evita la pérdida de actualizaciones.

## Pruebas

```bash
make test
```

Las pruebas ejecutan procesos reales con un límite de tiempo. Verifican el modo seguro repetidamente con 1, 2 y 8 carriles, la pérdida exacta del experimento controlado, la comparación de ambos modos, cargas distintas por carril y el rechazo de argumentos inválidos. El modo libre no tiene una cantidad de pérdidas garantizada y por ello no se prueba exigiendo un total incorrecto.

Código de salida: `0` ejecución válida (incluida una pérdida esperada en un experimento), `1` fallo de validación, `2` error de parámetros o de ejecución.

## Guion breve de exposición

1. Ejecutar `./peaje --modo seguro --carriles 3 --vehiculos 3 --eventos` para mostrar los PID de los procesos.
2. Ejecutar `./peaje --modo demo` y comparar los contadores individuales con el total global.
3. Explicar la actualización perdida: dos procesos leen 0, ambos calculan 1 y ambos escriben 1; se atendieron dos vehículos, pero queda uno registrado.
4. Mostrar el bloque protegido por `sem_wait` y `sem_post`: el segundo proceso lee después de que el primero termine su actualización.
5. Ejecutar `make test` para verificar integridad con más carga y varias repeticiones.

## Alcance y límites

Se simulan cantidades fijas de vehículos y una tarifa única para aislar el fenómeno de sincronización. No se modelan colas de tráfico, pagos reales ni interfaz gráfica.

La limpieza cubre la finalización normal y los errores detectados de creación de procesos. No se implementa recuperación ante la muerte externa de un hijo mientras mantiene un semáforo o participa en la barrera: en tal caso la ejecución puede quedar esperando y debe detenerse. Los semáforos POSIX ordinarios no recuperan automáticamente un bloqueo cuyo propietario muere.

La verificación automática tiene un tiempo máximo para detectar bloqueos. El código debe compilarse y probarse en Linux: las llamadas POSIX de este proyecto no están disponibles de forma nativa en Windows.
