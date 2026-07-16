const express = require('express');
const path = require('path');
const http = require('http');
const crypto = require('crypto');

const app = express();
const PORT = process.env.PORT || 3002;
const UIH_API = process.env.UIH_API_URL || 'http://localhost:3000';
const TIMEOUT_MS = 8000;

const O365_CLIENT_ID = process.env.O365_CLIENT_ID;
const O365_CLIENT_SECRET = process.env.O365_CLIENT_SECRET;
const O365_TENANT_ID = process.env.O365_TENANT_ID || 'common';
const O365_REDIRECT_URI = process.env.O365_REDIRECT_URI || `http://localhost:${PORT}/auth/o365/callback`;
const O365_SCOPES = process.env.O365_SCOPES || 'openid profile email User.Read offline_access';

function parseCookies(req) {
  const header = req.headers.cookie;
  const out = {};
  if (!header) return out;
  header.split(';').forEach((pair) => {
    const idx = pair.indexOf('=');
    if (idx === -1) return;
    out[pair.slice(0, idx).trim()] = decodeURIComponent(pair.slice(idx + 1).trim());
  });
  return out;
}

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

app.get('/api/uih-mails',       (req, res) => proxyGet('/api/uih-mails', res));
app.get('/api/uih-mails/daily', (req, res) => proxyGet('/api/uih-mails/daily', res));

app.put('/api/uih-mails/:id', (req, res) => {
  proxyMutate('PUT', `/api/uih-mails/${req.params.id}`, req.body, res);
});

app.delete('/api/uih-mails/:id', (req, res) => {
  proxyMutate('DELETE', `/api/uih-mails/${req.params.id}`, null, res);
});

app.get('/api/ma-tickets',         (req, res) => proxyGet('/api/ma-tickets', res));
app.get('/api/ma-pm-tickets',      (req, res) => proxyGet('/api/ma-pm-tickets', res));
app.get('/api/ma-tickets-history', (req, res) => proxyGet('/api/ma-tickets-history', res));

app.put('/api/ma-tickets/:id', (req, res) => {
  proxyMutate('PUT', `/api/ma-tickets/${req.params.id}`, req.body, res);
});

app.post('/api/verify-employee', (req, res) => {
  proxyMutate('POST', '/api/verify-employee', req.body, res);
});

app.get('/records', (req, res) => {
  res.sendFile(path.join(__dirname, 'public', 'uih-records.html'));
});

app.get('/tickets', (req, res) => {
  res.sendFile(path.join(__dirname, 'public', 'all-tickets.html'));
});

app.get('/pending-tickets', (req, res) => {
  res.sendFile(path.join(__dirname, 'public', 'pending-tickets.html'));
});

app.get('/tempOAuth', (req, res) => {
  res.sendFile(path.join(__dirname, 'public', 'temp-oauth.html'));
});

app.get('/auth/o365/login', (req, res) => {
  const state = crypto.randomBytes(16).toString('hex');
  res.cookie('o365_oauth_state', state, { httpOnly: true, maxAge: 5 * 60 * 1000 });
  const authorizeUrl = new URL(`https://login.microsoftonline.com/${O365_TENANT_ID}/oauth2/v2.0/authorize`);
  authorizeUrl.searchParams.set('client_id', O365_CLIENT_ID);
  authorizeUrl.searchParams.set('response_type', 'code');
  authorizeUrl.searchParams.set('redirect_uri', O365_REDIRECT_URI);
  authorizeUrl.searchParams.set('response_mode', 'query');
  authorizeUrl.searchParams.set('scope', O365_SCOPES);
  authorizeUrl.searchParams.set('state', state);
  res.redirect(authorizeUrl.toString());
});

app.get('/auth/o365/callback', async (req, res) => {
  const { code, state, error, error_description: errorDescription } = req.query;
  if (error) {
    return res.redirect(`/tempOAuth?status=error&message=${encodeURIComponent(errorDescription || error)}`);
  }

  const cookies = parseCookies(req);
  res.clearCookie('o365_oauth_state');
  if (!state || state !== cookies.o365_oauth_state) {
    return res.redirect(`/tempOAuth?status=error&message=${encodeURIComponent('Invalid or missing state parameter')}`);
  }

  try {
    const tokenRes = await fetch(`https://login.microsoftonline.com/${O365_TENANT_ID}/oauth2/v2.0/token`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({
        client_id: O365_CLIENT_ID,
        client_secret: O365_CLIENT_SECRET,
        grant_type: 'authorization_code',
        code,
        redirect_uri: O365_REDIRECT_URI,
        scope: O365_SCOPES,
      }),
    });
    const tokenData = await tokenRes.json();
    if (!tokenRes.ok) {
      return res.redirect(`/tempOAuth?status=error&message=${encodeURIComponent(tokenData.error_description || 'Token exchange failed')}`);
    }

    const profileRes = await fetch('https://graph.microsoft.com/v1.0/me', {
      headers: { Authorization: `Bearer ${tokenData.access_token}` },
    });
    const profile = await profileRes.json();
    const user = profile.mail || profile.userPrincipalName || profile.displayName || 'unknown';
    return res.redirect(`/tempOAuth?status=success&user=${encodeURIComponent(user)}`);
  } catch (err) {
    return res.redirect(`/tempOAuth?status=error&message=${encodeURIComponent(err.message)}`);
  }
});

app.get('/', (req, res) => {
  res.sendFile(path.join(__dirname, 'public', 'noc-shift-handover.html'));
});

app.listen(PORT, () => {
  console.log(`NOC Shift Handover running at http://localhost:${PORT}`);
});
