const express = require('express');
const path = require('path');
const http = require('http');

const app = express();
const PORT = process.env.PORT || 3002;
const UIH_API = process.env.UIH_API_URL || 'http://localhost:3000';
const TIMEOUT_MS = 8000;

app.use(express.json());
app.use(express.static(path.join(__dirname, 'public')));

// Reuse TCP connections to the upstream API
const agent = new http.Agent({ keepAlive: true, maxSockets: 10 });

function proxyGet(apiPath, res) {
  const target = new URL(UIH_API);
  const req = http.get({
    hostname: target.hostname,
    port: target.port || 80,
    path: apiPath,
    agent,
  }, (apiRes) => {
    const chunks = [];
    apiRes.on('data', chunk => chunks.push(chunk));
    apiRes.on('end', () => {
      res.setHeader('Content-Type', 'application/json');
      res.status(apiRes.statusCode).send(Buffer.concat(chunks));
    });
  });
  req.setTimeout(TIMEOUT_MS, () => {
    req.destroy();
    if (!res.headersSent) res.status(504).json({ error: 'UIH API timeout' });
  });
  req.on('error', (err) => {
    if (!res.headersSent) res.status(502).json({ error: 'UIH API unreachable', detail: err.message });
  });
}

function proxyMutate(method, apiPath, body, res) {
  const target = new URL(UIH_API);
  const bodyStr = body ? JSON.stringify(body) : '';
  const req = http.request({
    hostname: target.hostname,
    port: target.port || 80,
    path: apiPath,
    method,
    agent,
    headers: bodyStr
      ? { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(bodyStr) }
      : {},
  }, (apiRes) => {
    const chunks = [];
    apiRes.on('data', chunk => chunks.push(chunk));
    apiRes.on('end', () => {
      res.setHeader('Content-Type', 'application/json');
      res.status(apiRes.statusCode).send(chunks.length ? Buffer.concat(chunks) : '{}');
    });
  });
  req.setTimeout(TIMEOUT_MS, () => {
    req.destroy();
    if (!res.headersSent) res.status(504).json({ error: 'UIH API timeout' });
  });
  req.on('error', (err) => {
    if (!res.headersSent) res.status(502).json({ error: err.message });
  });
  if (bodyStr) req.write(bodyStr);
  req.end();
}

app.get('/api/uih-daily', (req, res) => proxyGet('/api/daily', res));
app.get('/api/uih-all',   (req, res) => proxyGet('/api/records', res));

app.put('/api/uih-record/:id', (req, res) => {
  proxyMutate('PUT', `/api/records/${req.params.id}`, req.body, res);
});

app.delete('/api/uih-record/:id', (req, res) => {
  proxyMutate('DELETE', `/api/records/${req.params.id}`, null, res);
});

app.get('/api/uih-tickets', (req, res) => proxyGet('/api/tickets', res));

app.put('/api/uih-ticket/:id', (req, res) => {
  proxyMutate('PUT', `/api/tickets/${req.params.id}`, req.body, res);
});

app.get('/records', (req, res) => {
  res.sendFile(path.join(__dirname, 'public', 'uih-records.html'));
});

app.get('/tickets', (req, res) => {
  res.sendFile(path.join(__dirname, 'public', 'all-tickets.html'));
});

app.get('/', (req, res) => {
  res.sendFile(path.join(__dirname, 'public', 'noc-shift-handover.html'));
});

app.listen(PORT, () => {
  console.log(`NOC Shift Handover running at http://localhost:${PORT}`);
});
