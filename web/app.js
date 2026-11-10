'use strict';

const $ = (id) => document.getElementById(id);
const colors = ['#7caf92', '#d3ab69', '#7ca4bb', '#b49bbd', '#b7bd79', '#d18f7d', '#88b8b2', '#a8a3d0'];
const money = (centimos) => `S/ ${(centimos / 100).toLocaleString('es-PE', {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
const stateLabels = {idle: 'En espera', atendiendo: 'Atendiendo', esperando: 'Espera semáforo', registrando: 'Registrando', barrera: 'Espera barrera', sin_proteccion: 'Sin protección', carril_fin: 'Finalizado', detenido: 'Detenido'};
const descriptions = {
  seguro: 'Los carriles comparten la caja por turnos. Cada cobro queda registrado.',
  colision: 'Una barrera provoca lecturas iguales para observar actualizaciones perdidas.',
  libre: 'Los procesos modifican la caja sin exclusión mutua. El resultado puede variar.'
};
let lanes = [], active = false, connected = false, runId = null, controller = null;
let served = 0, globalCount = 0, globalMoney = 0, eventCount = 0;
let mode = 'seguro', delay = 1200;
let hasSummary = false;
let laneCards = [];
const canvas = $('road');
const ctx = canvas.getContext('2d');
const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

function quantities() {
  return [...document.querySelectorAll('.quantity')].map((input) => Number(input.value));
}
function expected() { return quantities().reduce((sum, n) => sum + (Number.isFinite(n) ? n : 0), 0); }
function setMessage(text, error = false) { $('message').textContent = text; $('message').classList.toggle('error', error); }
function runState(text, style = '') {
  $('run-state').className = `state-pill ${style}`;
  $('run-state').replaceChildren(Object.assign(document.createElement('i')), document.createTextNode(text));
}
function makeInputs(values) {
  $('lane-inputs').replaceChildren();
  values.forEach((value, index) => {
    const row = document.createElement('div');
    row.className = 'lane-input-row'; row.style.setProperty('--lane-color', colors[index]);
    const dot = document.createElement('span'); dot.className = 'lane-dot';
    const label = document.createElement('label'); label.htmlFor = `quantity-${index}`; label.textContent = `Carril ${String(index + 1).padStart(2, '0')}`;
    const input = document.createElement('input');
    Object.assign(input, {type: 'number', min: '1', max: '100', step: '1', required: true, value, className: 'quantity', id: `quantity-${index}`});
    input.addEventListener('input', resetPreview);
    row.append(dot, label, input); $('lane-inputs').append(row);
  });
  resetPreview();
}
function resetPreview() {
  if (active) return;
  mode = $('mode').value;
  delay = Number($('speed').value) * 2;
  lanes = quantities().map((quantity, i) => ({quantity, served: 0, pid: null, state: 'idle', color: colors[i], carTime: 0, departures: []}));
  served = globalCount = globalMoney = eventCount = 0;
  hasSummary = false;
  $('mode-description').textContent = descriptions[mode];
  $('expected-count').textContent = expected();
  $('expected-money').textContent = money(expected() * 500);
  $('event-log').innerHTML = '<li class="log-empty">Los eventos de cada proceso aparecerán aquí.</li>';
  $('event-count').textContent = '0 eventos';
  runState('En espera');
  buildLaneRevenue(); resizeCanvas(); updateStats();
  setMessage('Todo listo. Configura los carriles y comienza.');
}
function buildLaneRevenue() {
  $('lane-revenue-cards').replaceChildren();
  laneCards = lanes.map((lane, index) => {
    const card = document.createElement('article');
    card.className = 'lane-revenue-card';
    card.style.setProperty('--lane-color', lane.color);
    const title = document.createElement('h4');
    title.textContent = `Carril ${String(index + 1).padStart(2, '0')}`;
    const amount = document.createElement('strong');
    const count = document.createElement('p');
    card.append(title, amount, count);
    $('lane-revenue-cards').append(card);
    return {amount, count};
  });
}
function updateStats() {
  const total = expected();
  $('money').textContent = money(globalMoney);
  $('money-caption').textContent = `de ${money(total * 500)} esperados`;
  const percent = total ? Math.min(100, Math.round(served * 100 / total)) : 0;
  $('progress-label').textContent = `${percent} %`;
  $('progress-count').textContent = `${served} de ${total} vehículos`;
  $('progress').setAttribute('aria-valuenow', percent);
  $('progress').firstElementChild.style.width = `${percent}%`;
  lanes.forEach((lane, index) => {
    // Cada evento de cobro representa S/ 5.00 atendidos por este proceso.
    laneCards[index].amount.textContent = money(lane.served * 500);
    laneCards[index].count.textContent = `${lane.served} de ${lane.quantity} vehículos`;
  });
}
function logEvent(event) {
  const texts = {
    carril_inicio: `Proceso iniciado · PID ${event.pid}`,
    esperando: 'Solicita el semáforo', registrando: 'Accede a la caja compartida',
    cobro: `Cobro realizado · S/ 5.00 · vehículo ${event.vehiculo}`,
    barrera: 'Espera lecturas de la misma ronda', sin_proteccion: 'Actualiza sin protección',
    carril_fin: 'Atención finalizada'
  };
  if (!texts[event.tipo]) return;
  if (!eventCount) $('event-log').replaceChildren();
  eventCount++;
  const li = document.createElement('li');
  const time = document.createElement('time'); time.textContent = `${(event.ms / 1000).toFixed(2)} s`;
  const text = document.createElement('span');
  const label = document.createElement('b'); label.textContent = `C${event.carril} `;
  text.append(label, document.createTextNode(texts[event.tipo])); li.append(time, text);
  $('event-log').prepend(li);
  while ($('event-log').children.length > 80) $('event-log').lastElementChild.remove();
  $('event-count').textContent = `${eventCount} eventos`;
}
function receive(event) {
  if (event.tipo === 'configuracion') {
    runId = event.id;
    delay = event.demora_ms * event.escala_tiempo;
    return;
  }
  if (event.tipo === 'resumen') {
    hasSummary = true;
    globalCount = event.total; globalMoney = event.centimos;
    updateStats();
    return;
  }
  if (event.tipo === 'fin') {
    if (event.cancelada) {
      setMessage('Simulación detenida. Puedes reiniciar o cambiar la configuración.');
      runState('Detenida');
    } else if (event.codigo !== 0 || !hasSummary) {
      throw new Error(event.error || 'El simulador no pudo completar la ejecución.');
    } else {
      const correct = globalCount === expected() && globalMoney === expected() * 500;
      runState('Finalizada', 'done');
      setMessage(correct ? `Integridad verificada: ${globalCount} vehículos y ${money(globalMoney)} registrados correctamente.` : `${served - globalCount} actualizaciones perdidas. Cobros por carril: ${money(served * 500)}; caja global: ${money(globalMoney)}.`, !correct);
    }
    return;
  }
  const lane = lanes[event.carril - 1];
  if (!lane) return;
  lane.pid = event.pid;
  if (event.tipo === 'atendiendo') { lane.state = 'atendiendo'; lane.carTime = performance.now(); }
  if (['esperando', 'registrando', 'barrera', 'sin_proteccion', 'carril_fin'].includes(event.tipo)) lane.state = event.tipo;
  if (event.tipo === 'cobro') {
    lane.departures.push({time: performance.now(), vehicle: event.vehiculo});
    lane.served = event.vehiculo;
    served = lanes.reduce((sum, item) => sum + item.served, 0);
    globalCount = event.total; globalMoney = event.centimos;
    lane.state = lane.served === lane.quantity ? 'carril_fin' : 'idle';
  }
  logEvent(event); updateStats();
}
async function checkConnection() {
  try {
    const response = await fetch('/api/estado', {signal: AbortSignal.timeout(4000)});
    if (!response.ok) throw new Error('Conexión no disponible');
    const data = await response.json(); connected = data.disponible;
    $('connection').className = `connection ${connected ? 'connected' : 'failed'}`;
    $('connection').querySelector('span').textContent = connected ? 'Motor C++ conectado' : 'Ejecuta make primero';
  } catch {
    connected = false;
    $('connection').className = 'connection failed';
    $('connection').querySelector('span').textContent = 'Sin conexión · reintentar';
    setMessage('Inicia el servidor con make web y abre http://127.0.0.1:8000.', true);
  }
  $('start').disabled = !connected || active;
}
async function start(event) {
  event.preventDefault();
  if (active || !$('configuration').reportValidity()) return;
  if ($('mode').value === 'colision' && quantities().length < 2) {
    setMessage('La colisión controlada necesita al menos dos carriles.', true); return;
  }
  resetPreview();
  active = true; runId = null; controller = new AbortController();
  $('settings').disabled = true; $('start').disabled = true;
  $('stop').disabled = false; $('reset').disabled = true;
  runState('En ejecución', 'running');
  setMessage('Cada carril atiende su fila de forma independiente.');
  try {
    const response = await fetch('/api/simular', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({cantidades: quantities(), modo: mode, demora_ms: Number($('speed').value)}), signal: controller.signal});
    if (!response.ok) { const data = await response.json(); throw new Error(data.error || 'No se pudo iniciar la simulación.'); }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '', finished = false;
    while (true) {
      const {done, value} = await reader.read();
      buffer += decoder.decode(value, {stream: !done});
      const lines = buffer.split('\n'); buffer = lines.pop();
      for (const line of lines) {
        if (!line.trim()) continue;
        const message = JSON.parse(line);
        receive(message);
        if (message.tipo === 'fin') finished = true;
      }
      if (done) break;
    }
    if (!finished) throw new Error('La conexión terminó antes de recibir el resultado final.');
  } catch (error) {
    runState('Interrumpida');
    setMessage(error.message, true);
    if (runId) {
      try { await stopRequest(); } catch { /* La desconexión también cancela el proceso en el servidor. */ }
    }
  } finally {
    active = false; runId = null;
    lanes.forEach((lane) => { if (lane.served < lane.quantity) lane.state = 'detenido'; });
    $('settings').disabled = false; $('start').disabled = !connected;
    $('stop').disabled = true; $('reset').disabled = false;
    updateStats();
  }
}
async function stopRequest() {
  const response = await fetch('/api/detener', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({id: runId})});
  if (!response.ok) throw new Error('No se pudo detener la ejecución.');
}
$('stop').addEventListener('click', async () => {
  if (!runId) { setMessage('Esperando la conexión con el proceso. Intenta detenerlo de nuevo.'); return; }
  $('stop').disabled = true;
  setMessage('Deteniendo los procesos y liberando los recursos…');
  try { await stopRequest(); } catch (error) { setMessage(error.message, true); $('stop').disabled = false; }
});
$('configuration').addEventListener('submit', start);
$('connection').addEventListener('click', checkConnection);
$('reset').addEventListener('click', resetPreview);
$('mode').addEventListener('change', resetPreview);
$('speed').addEventListener('change', resetPreview);
$('lane-count').addEventListener('change', () => {
  const previous = quantities();
  makeInputs(Array.from({length: Number($('lane-count').value)}, (_, index) => previous[index] || 5 + index * 2));
});
$('randomize').addEventListener('click', () => {
  const values = quantities().map(() => 4 + Math.floor(Math.random() * 15));
  if (values.length > 1 && values.every((value) => value === values[0])) values[0]++;
  makeInputs(values);
});

// Dibujo de la estación. Los cambios de estado provienen exclusivamente de C++;
// requestAnimationFrame interpola el desplazamiento entre esos eventos.
let width = 800, height = 440;
function resizeCanvas() {
  width = Math.max(680, canvas.parentElement.clientWidth);
  height = Math.max(300, 106 + lanes.length * 100);
  const ratio = window.devicePixelRatio || 1;
  canvas.style.height = `${height}px`;
  canvas.width = Math.round(width * ratio); canvas.height = Math.round(height * ratio);
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
}
function rect(x, y, w, h, radius, fill) {
  ctx.fillStyle = fill; ctx.beginPath(); ctx.roundRect(x, y, w, h, radius); ctx.fill();
}
function text(label, x, y, size, color, align = 'left', weight = 400) {
  ctx.font = `${weight} ${Math.max(11, size + 3)}px "Segoe UI", Arial, sans-serif`; ctx.fillStyle = color; ctx.textAlign = align; ctx.fillText(label, x, y);
}
function car(x, y, color, scale = 1) {
  ctx.save(); ctx.translate(x, y); ctx.scale(scale, scale);
  rect(-29, -14, 60, 32, 7, 'rgba(7,21,22,.18)');
  rect(-21, -18, 11, 5, 2, '#1e2e2e'); rect(12, -18, 11, 5, 2, '#1e2e2e');
  rect(-21, 13, 11, 5, 2, '#1e2e2e'); rect(12, 13, 11, 5, 2, '#1e2e2e');
  rect(-29, -15, 58, 30, 6, color);
  rect(-12, -12, 27, 24, 4, '#456568');
  rect(-5, -10, 14, 20, 3, color);
  rect(22, -11, 5, 6, 2, '#f0e6b2'); rect(22, 5, 5, 6, 2, '#f0e6b2');
  rect(-29, -10, 3, 5, 1, '#c56b5a'); rect(-29, 5, 3, 5, 1, '#c56b5a');
  ctx.restore();
}
function arrow(x, y) {
  ctx.strokeStyle = '#61716c'; ctx.lineWidth = 2; ctx.beginPath();
  ctx.moveTo(x - 10, y); ctx.lineTo(x + 10, y); ctx.moveTo(x + 4, y - 5); ctx.lineTo(x + 10, y); ctx.lineTo(x + 4, y + 5); ctx.stroke();
}
function draw(now) {
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = '#e7ecdf'; ctx.fillRect(0, 0, width, height);
  const left = 98, right = width - 22, top = 50, row = 100, bottom = top + lanes.length * row;
  const booth = width * .65;
  text('ACCESO A LA ESTACIÓN', left + 8, 24, 8, '#829075', 'left', 600);
  text('ZONA DE COBRO', booth + 7, 24, 8, '#667e54', 'center', 650);
  text('SALIDA →', right - 10, 24, 8, '#829075', 'right', 600);
  rect(left - 6, top - 5, right - left + 12, bottom - top + 10, 9, '#cad3bf');
  rect(left, top, right - left, bottom - top, 5, '#304242');
  ctx.strokeStyle = '#d2d7bc'; ctx.lineWidth = 2; ctx.setLineDash([]);
  ctx.beginPath(); ctx.moveTo(left + 4, top + 6); ctx.lineTo(right - 4, top + 6); ctx.moveTo(left + 4, bottom - 6); ctx.lineTo(right - 4, bottom - 6); ctx.stroke();
  lanes.forEach((lane, index) => {
    const y = top + row * index + row / 2;
    if (index) {
      ctx.strokeStyle = '#819087'; ctx.lineWidth = 1; ctx.setLineDash([12, 13]); ctx.beginPath(); ctx.moveTo(left, top + row * index); ctx.lineTo(right, top + row * index); ctx.stroke(); ctx.setLineDash([]);
    }
    text(String(index + 1).padStart(2, '0'), 35, y - 3, 18, '#6e8560', 'center', 650);
    text('CARRIL', 35, y + 12, 7, '#93a285', 'center', 600);
    arrow(booth + 105, y); arrow(right - 26, y);
    // Isleta, cabina y barrera del carril.
    rect(booth - 15, y - 39, 48, 17, 4, '#d2d8c6');
    rect(booth - 12, y - 39, 42, 13, 3, '#f0f1df');
    rect(booth - 8, y - 36, 34, 8, 2, '#719172');
    rect(booth - 4, y - 34, 13, 4, 1, '#c6ddc4');
    const registering = lane.state === 'registrando';
    const waiting = ['esperando', 'barrera'].includes(lane.state);
    const statusColor = registering ? '#8ab4d2' : waiting ? '#dfba6d' : lane.state === 'carril_fin' ? '#9fae93' : '#86bb8e';
    ctx.fillStyle = statusColor; ctx.beginPath(); ctx.arc(booth + 37, y - 28, 3, 0, Math.PI * 2); ctx.fill();
    rect(booth + 15, y - 16, 4, 32, 1, '#d7d4b6');
    for (let stripe = 0; stripe < 4; stripe++) rect(booth + 15, y - 16 + stripe * 8, 4, 4, 0, '#b9865e');
    const remaining = Math.max(0, lane.quantity - lane.served);
    const queueX = Math.max(left + 70, booth - 135);
    let front = queueX;
    if (lane.state !== 'idle' && lane.state !== 'detenido') {
      const t = reducedMotion ? 1 : Math.min(1, (now - lane.carTime) / Math.max(70, delay * .7));
      front = queueX + (booth - 49 - queueX) * (1 - Math.pow(1 - t, 3));
    }
    if (remaining && lane.state !== 'carril_fin') {
      for (let n = Math.min(remaining, 4) - 1; n >= 0; n--) {
        const x = front - n * 76;
        if (x > left + 25) car(x, y, lane.color, .87);
      }
      const visible = Math.min(remaining, Math.max(1, Math.floor((front - left - 25) / 76) + 1), 4);
      if (remaining > visible) text(`+${remaining - visible}`, left + 15, y + 28, 8, '#b1bda7');
    }
    lane.departures = lane.departures.filter((departure) => now - departure.time < 1900);
    lane.departures.forEach((departure) => {
      if (reducedMotion) return;
      const t = Math.min(1, (now - departure.time) / 1700);
      const x = booth - 49 + (right + 50 - (booth - 49)) * t;
      ctx.save(); ctx.beginPath(); ctx.rect(left, top, right - left, bottom - top); ctx.clip(); car(x, y, lane.color, .87); ctx.restore();
    });
    const label = stateLabels[lane.state];
    text(label, right - 12, y + 30, 8, statusColor, 'right');
  });
  text('Cada carril = un proceso independiente', left + 7, bottom + 25, 8, '#829075');
  text(mode === 'seguro' ? 'CAJA COMPARTIDA · ACCESO CON SEMÁFORO' : mode === 'colision' ? 'COLISIÓN CONTROLADA · SIN EXCLUSIÓN MUTUA' : 'CAJA COMPARTIDA · SIN PROTECCIÓN', right - 5, bottom + 43, 7, '#829075', 'right');
  requestAnimationFrame(draw);
}
new ResizeObserver(resizeCanvas).observe(canvas.parentElement);
makeInputs([8, 12, 6, 10]);
checkConnection();
requestAnimationFrame(draw);
