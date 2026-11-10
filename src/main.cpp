#include <algorithm>
#include <chrono>
#include <numeric>
#include <sstream>
#include <cerrno>
#include <cstdint>
#include <cstdlib>
#include <cstdio>
#include <csignal>
#include <cstring>
#include <iomanip>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>
#include <semaphore.h>
#include <sys/mman.h>
#include <sys/wait.h>
#include <unistd.h>

// Todos estos objetos viven en un mmap MAP_SHARED, incluido el semáforo.
struct Compartido {
    sem_t mutex;
    sem_t puerta1;
    sem_t puerta2;
    sem_t barrera_mutex;
    int esperando;
    std::uint64_t vehiculos;
    std::uint64_t centimos;
    std::uint64_t por_carril[32];
    std::uint64_t centimos_por_carril[32];
    pid_t pid_por_carril[32];
};

static void esperar(sem_t* s) {
    while (sem_wait(s) == -1) {
        if (errno != EINTR) { perror("sem_wait"); _exit(2); }
    }
}
static void liberar(sem_t* s) {
    if (sem_post(s) == -1) { perror("sem_post"); _exit(2); }
}

// Barrera reutilizable de dos torniquetes. Solo coordina el experimento
// de colisión: NO protege la lectura-modificación-escritura de los totales.
static void barrera(Compartido* c, int n) {
    esperar(&c->barrera_mutex);
    if (++c->esperando == n) {
        esperar(&c->puerta2);
        liberar(&c->puerta1);
    }
    liberar(&c->barrera_mutex);
    esperar(&c->puerta1); liberar(&c->puerta1);
    esperar(&c->barrera_mutex);
    if (--c->esperando == 0) {
        esperar(&c->puerta1);
        liberar(&c->puerta2);
    }
    liberar(&c->barrera_mutex);
    esperar(&c->puerta2); liberar(&c->puerta2);
}

struct Resultado { std::uint64_t vehiculos, centimos; };
static volatile sig_atomic_t detener = 0;
static void solicitar_salida(int) { detener = 1; }

static const auto inicio_reloj = std::chrono::steady_clock::now();

// Cada registro cabe en PIPE_BUF y se escribe de una vez para que los hijos
// no mezclen sus mensajes cuando stdout está conectado al servidor local.
static void evento_json(const char* tipo, const std::string& modo, int carril = 0,
                        int vehiculo = 0, std::uint64_t total = 0,
                        std::uint64_t centimos = 0) {
    auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now() - inicio_reloj).count();
    char linea[512];
    int longitud = snprintf(linea, sizeof(linea),
        "{\"tipo\":\"%s\",\"modo\":\"%s\",\"carril\":%d,\"vehiculo\":%d,"
        "\"pid\":%ld,\"ms\":%lld,\"total\":%llu,\"centimos\":%llu}\n",
        tipo, modo.c_str(), carril, vehiculo, static_cast<long>(getpid()),
        static_cast<long long>(ms), static_cast<unsigned long long>(total),
        static_cast<unsigned long long>(centimos));
    ssize_t escritos;
    do { escritos = write(STDOUT_FILENO, linea, longitud); } while (escritos == -1 && errno == EINTR);
    if (escritos != longitud) _exit(2);
}

static Resultado simular(const std::string& modo, const std::vector<int>& cantidades,
                         bool eventos, bool json, int demora_ms, int escala_tiempo) {
    const int carriles = static_cast<int>(cantidades.size());
    void* mem = mmap(nullptr, sizeof(Compartido), PROT_READ | PROT_WRITE,
                     MAP_SHARED | MAP_ANONYMOUS, -1, 0);
    if (mem == MAP_FAILED) throw std::runtime_error("mmap: " + std::string(strerror(errno)));
    auto* c = static_cast<Compartido*>(mem);
    std::memset(c, 0, sizeof(*c));
    sem_t* semaforos[] = {&c->mutex, &c->puerta1, &c->puerta2, &c->barrera_mutex};
    unsigned valores[] = {1, 0, 1, 1};
    int inicializados = 0;
    auto limpiar = [&]() {
        for (int i = 0; i < inicializados; ++i) sem_destroy(semaforos[i]);
        munmap(mem, sizeof(Compartido));
    };
    for (int i = 0; i < 4; ++i) {
        if (sem_init(semaforos[i], 1, valores[i]) == -1) {
            std::string error = strerror(errno); limpiar();
            throw std::runtime_error("sem_init: " + error);
        }
        ++inicializados;
    }
    std::vector<pid_t> hijos;
    hijos.reserve(carriles);
    bool fallo = false;
    std::cout.flush();
    if (json) evento_json("inicio", modo);
    for (int carril = 0; carril < carriles; ++carril) {
        pid_t pid = fork();
        if (pid == -1) { perror("fork"); fallo = true; break; }
        if (pid == 0) {
            signal(SIGTERM, SIG_DFL);
            signal(SIGINT, SIG_DFL);
            c->pid_por_carril[carril] = getpid();
            if (json) evento_json("carril_inicio", modo, carril + 1);
            for (int v = 0; v < cantidades[carril]; ++v) {
                if (json) evento_json("atendiendo", modo, carril + 1, v + 1);
                // Tiempo de atención fuera de la sección crítica: los carriles
                // trabajan concurrentemente y llegan al cobro en momentos distintos.
                if (demora_ms) usleep((demora_ms + (carril * 71 + v * 37) % 150) * 1000 * escala_tiempo);
                // Solo participan en esta ronda los carriles que aún tienen vehículos.
                int participantes = modo == "colision"
                    ? static_cast<int>(std::count_if(cantidades.begin(), cantidades.end(),
                        [v](int cantidad) { return cantidad > v; })) : 0;
                // Todos usan la misma tarifa para aislar el efecto de la carrera.
                constexpr std::uint64_t tarifa = 500;
                if (modo == "seguro") {
                    if (json) evento_json("esperando", modo, carril + 1, v + 1);
                    esperar(&c->mutex);
                    if (json) evento_json("registrando", modo, carril + 1, v + 1);
                    // Pausa didáctica opcional para poder observar la exclusión mutua.
                    if (demora_ms) usleep(std::min(demora_ms / 4, 150) * 1000 * escala_tiempo);
                    ++c->vehiculos;
                    c->centimos += tarifa;
                    if (json) evento_json("cobro", modo, carril + 1, v + 1, c->vehiculos, c->centimos);
                    liberar(&c->mutex);
                } else {
                    if (json) evento_json(modo == "colision" ? "barrera" : "sin_proteccion",
                                          modo, carril + 1, v + 1);
                    // volatile hace visibles los accesos del experimento; NO
                    // proporciona atomicidad ni sustituye la sincronización.
                    volatile std::uint64_t* total = &c->vehiculos;
                    volatile std::uint64_t* caja = &c->centimos;
                    auto leidos = *total;
                    auto dinero = *caja;
                    if (modo == "colision") barrera(c, participantes);
                    else usleep(50 * escala_tiempo); // Amplía la ventana; el fallo sigue siendo variable.
                    *total = leidos + 1;
                    *caja = dinero + tarifa;
                    if (modo == "colision") barrera(c, participantes);
                    if (json) evento_json("cobro", modo, carril + 1, v + 1, leidos + 1, dinero + tarifa);
                    // No comienza la ronda siguiente antes de publicar sus eventos.
                    if (json && modo == "colision") barrera(c, participantes);
                }
                // Solo este hijo escribe su casilla. El padre lee tras waitpid.
                ++c->por_carril[carril];
                c->centimos_por_carril[carril] += tarifa;
                if (eventos && !json) {
                    // Una línea por evento; la salida no forma parte de la prueba.
                    dprintf(STDOUT_FILENO, "PID %ld | carril %d | vehiculo %d | cobro S/ 5.00\n",
                            static_cast<long>(getpid()), carril + 1, v + 1);
                }
            }
            if (json) evento_json("carril_fin", modo, carril + 1, cantidades[carril]);
            _exit(0);
        }
        hijos.push_back(pid);
    }
    // Un fork fallido dejaría incompleta la barrera. Cancelar antes de esperar.
    if (fallo) for (pid_t pid : hijos) kill(pid, SIGKILL);
    // Recoger al primero que termine evita quedar bloqueado si otro hijo falla
    // mientras sus compañeros esperan una barrera o un semáforo.
    while (!hijos.empty()) {
        if (detener) {
            fallo = true;
            for (pid_t pid : hijos) kill(pid, SIGKILL);
        }
        int estado = 0;
        pid_t r = waitpid(-1, &estado, 0);
        if (r == -1 && errno == EINTR) continue;
        if (r > 0) hijos.erase(std::remove(hijos.begin(), hijos.end(), r), hijos.end());
        if (r == -1 || !WIFEXITED(estado) || WEXITSTATUS(estado) != 0) {
            fallo = true;
            for (pid_t otro : hijos) kill(otro, SIGKILL);
            if (r == -1) break;
        }
    }
    Resultado resultado{c->vehiculos, c->centimos};
    std::uint64_t suma = 0, suma_centimos = 0;
    bool carriles_validos = true;
    if (!json) std::cout << "\nModo: " << modo << "\n";
    for (int i = 0; i < carriles; ++i) {
        suma += c->por_carril[i];
        suma_centimos += c->centimos_por_carril[i];
        carriles_validos = carriles_validos && c->pid_por_carril[i] > 0
            && c->por_carril[i] == static_cast<std::uint64_t>(cantidades[i])
            && c->centimos_por_carril[i] == c->por_carril[i] * 500;
        if (!json) std::cout << "  Carril " << i + 1 << ": " << c->por_carril[i]
                  << " vehiculos | PID " << c->pid_por_carril[i]
                  << " | recaudacion S/ " << std::fixed << std::setprecision(2)
                  << c->centimos_por_carril[i] / 100.0 << "\n";
    }
    auto esperado = std::accumulate(cantidades.begin(), cantidades.end(), std::uint64_t{0});
    bool integridad = carriles_validos && suma == esperado
        && resultado.vehiculos == suma && resultado.centimos == suma_centimos;
    if (!json) std::cout << "  Suma independiente por carril: " << suma
              << "\n  Suma de cobros por carril: S/ " << std::fixed << std::setprecision(2)
              << suma_centimos / 100.0
              << "\n  Contador global: " << resultado.vehiculos << " / esperado: " << esperado
              << "\n  Recaudacion: S/ " << std::fixed << std::setprecision(2)
              << resultado.centimos / 100.0 << " / esperada: S/ " << esperado * 5.0
              << "\n  Integridad: " << (integridad
                                          ? "CORRECTA" : "ACTUALIZACIONES PERDIDAS") << "\n";
    if (json && !fallo) evento_json("resumen", modo, 0, 0, resultado.vehiculos, resultado.centimos);
    limpiar();
    if (fallo) throw std::runtime_error("Uno o mas procesos fallaron; resultado invalido");
    if (!carriles_validos || suma != esperado || suma_centimos != esperado * 500)
        throw std::runtime_error("Los registros individuales de los carriles son inconsistentes");
    return resultado;
}

static int numero(const char* texto, int maximo) {
    std::size_t pos = 0;
    long valor = std::stol(texto, &pos);
    if (texto[pos] != '\0' || valor < 1 || valor > maximo)
        throw std::runtime_error("Parametro numerico fuera de rango");
    return static_cast<int>(valor);
}

int main(int argc, char** argv) {
    struct sigaction accion{};
    accion.sa_handler = solicitar_salida;
    sigemptyset(&accion.sa_mask);
    sigaction(SIGTERM, &accion, nullptr);
    sigaction(SIGINT, &accion, nullptr);
    signal(SIGPIPE, SIG_IGN);
    try {
        std::string modo = "demo";
        int carriles = 4;
        bool carriles_explicitos = false;
        std::string entrada_vehiculos;
        bool eventos = false;
        bool json = false;
        int demora_ms = 0;
        int escala_tiempo = 1;
        for (int i = 1; i < argc; ++i) {
            std::string arg = argv[i];
            if (arg == "--ayuda") {
                std::cout << "Uso: peaje [--modo demo|seguro|libre|colision] "
                             "[--carriles 1..32] [--vehiculos N|N1,N2,...] [--eventos] [--json] [--demora-ms 0..2000] [--escala-tiempo 1..4]\n"
                             "Cada cantidad debe estar entre 1 y 1000000. Sin lista: cargas distintas por carril.\n";
                return 0;
            }
            if (arg == "--eventos") { eventos = true; continue; }
            if (arg == "--json") { json = true; continue; }
            if (i + 1 >= argc) throw std::runtime_error("Falta valor para " + arg);
            if (arg == "--modo") modo = argv[++i];
            else if (arg == "--carriles") { carriles = numero(argv[++i], 32); carriles_explicitos = true; }
            else if (arg == "--vehiculos") entrada_vehiculos = argv[++i];
            else if (arg == "--escala-tiempo") escala_tiempo = numero(argv[++i], 4);
            else if (arg == "--demora-ms") {
                const std::string valor = argv[++i];
                demora_ms = valor == "0" ? 0 : numero(valor.c_str(), 2000);
            }
            else throw std::runtime_error("Opcion desconocida: " + arg);
        }
        std::vector<int> cantidades;
        if (entrada_vehiculos.empty()) {
            for (int i = 0; i < carriles; ++i) cantidades.push_back(100 + 25 * i);
        } else if (entrada_vehiculos.find(',') == std::string::npos) {
            cantidades.assign(carriles, numero(entrada_vehiculos.c_str(), 1000000));
        } else {
            if (entrada_vehiculos.back() == ',') throw std::runtime_error("Lista de vehiculos incompleta");
            std::istringstream lista(entrada_vehiculos);
            std::string valor;
            while (std::getline(lista, valor, ',')) cantidades.push_back(numero(valor.c_str(), 1000000));
            if (cantidades.size() > 32 || (carriles_explicitos && cantidades.size() != static_cast<std::size_t>(carriles)))
                throw std::runtime_error("La lista debe contener una cantidad por carril, hasta 32 carriles");
            carriles = static_cast<int>(cantidades.size());
        }
        auto esperado = std::accumulate(cantidades.begin(), cantidades.end(), std::uint64_t{0});
        auto rondas = static_cast<std::uint64_t>(*std::max_element(cantidades.begin(), cantidades.end()));
        if (modo != "demo" && modo != "seguro" && modo != "libre" && modo != "colision")
            throw std::runtime_error("Modo desconocido");
        if ((modo == "demo" || modo == "colision") && carriles < 2)
            throw std::runtime_error("La demostracion de colision requiere al menos 2 carriles");
        if (!json) {
            std::cout << "SIMULADOR DE PEAJE - SINCRONIZACION DE PROCESOS\n"
                      << "Carriles: " << carriles << " | vehiculos por carril: ";
            for (int i = 0; i < carriles; ++i) std::cout << (i ? ", " : "") << cantidades[i];
            std::cout << " | tarifa: S/ 5.00\n";
        }
        if (modo == "demo") {
            if (!json) std::cout << "\nExperimento controlado: todos leen el mismo valor antes de escribir.\n";
            auto malo = simular("colision", cantidades, eventos, json, demora_ms, escala_tiempo);
            auto bueno = simular("seguro", cantidades, eventos, json, demora_ms, escala_tiempo);
            bool ok = malo.vehiculos == rondas
                   && malo.centimos == rondas * 500
                   && bueno.vehiculos == esperado && bueno.centimos == esperado * 500;
            if (!json) std::cout << "\nDemostracion: " << (ok ? "VERIFICADA" : "FALLO") << "\n";
            return ok ? 0 : 1;
        }
        auto r = simular(modo, cantidades, eventos, json, demora_ms, escala_tiempo);
        if (modo == "seguro") return (r.vehiculos == esperado && r.centimos == esperado * 500) ? 0 : 1;
        return 0; // En modos experimentales, perder actualizaciones es un resultado esperado.
    } catch (const std::exception& e) {
        std::cerr << "Error: " << e.what() << "\n";
        return 2;
    }
}
