import { test } from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { download } from '../lib/http-download.js';

test('download() writes the response body to dest on a plain 200', async () => {
  const server = http.createServer((req, res) => {
    res.writeHead(200);
    res.end('hello world');
  });
  await new Promise(resolve => server.listen(0, resolve));
  const port = server.address().port;
  const dest = path.join(os.tmpdir(), `http-download-test-${Date.now()}.txt`);
  try {
    await download(`http://127.0.0.1:${port}/`, dest, 1);
    assert.equal(fs.readFileSync(dest, 'utf8'), 'hello world');
  } finally {
    server.close();
    fs.rmSync(dest, { force: true });
  }
});

test('download() follows a redirect chain to the final 200', async () => {
  const server = http.createServer((req, res) => {
    if (req.url === '/start') {
      res.writeHead(302, { Location: '/final' });
      res.end();
      return;
    }
    res.writeHead(200);
    res.end('redirected content');
  });
  await new Promise(resolve => server.listen(0, resolve));
  const port = server.address().port;
  const dest = path.join(os.tmpdir(), `http-download-test-redirect-${Date.now()}.txt`);
  try {
    await download(`http://127.0.0.1:${port}/start`, dest, 1);
    assert.equal(fs.readFileSync(dest, 'utf8'), 'redirected content');
  } finally {
    server.close();
    fs.rmSync(dest, { force: true });
  }
});

test('download() retries on failure and succeeds if a later attempt works', async () => {
  let requestCount = 0;
  const server = http.createServer((req, res) => {
    requestCount += 1;
    if (requestCount < 2) {
      req.socket.destroy(); // simulate a dropped connection on the first attempt
      return;
    }
    res.writeHead(200);
    res.end('succeeded on retry');
  });
  await new Promise(resolve => server.listen(0, resolve));
  const port = server.address().port;
  const dest = path.join(os.tmpdir(), `http-download-test-retry-${Date.now()}.txt`);
  try {
    await download(`http://127.0.0.1:${port}/`, dest, 3);
    assert.equal(fs.readFileSync(dest, 'utf8'), 'succeeded on retry');
    assert.ok(requestCount >= 2);
  } finally {
    server.close();
    fs.rmSync(dest, { force: true });
  }
});

test('download() rejects after exhausting all attempts against a dead port', async () => {
  const dest = path.join(os.tmpdir(), `http-download-test-fail-${Date.now()}.txt`);
  await assert.rejects(
    download('http://127.0.0.1:1/', dest, 2),
  );
  assert.equal(fs.existsSync(dest), false);
});
