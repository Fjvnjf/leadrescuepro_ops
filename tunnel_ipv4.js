#!/usr/bin/env node
/**
 * IPv4-forced localtunnel client — works around Node.js IPv6 ETIMEDOUT.
 * Persistent: auto-reconnects on disconnect.
 * Usage: node tunnel_ipv4.js [port] [subdomain]
 */
const https = require('https');
const net = require('net');
const dns = require('dns');

// Flush stdout immediately
process.stdout._handle && process.stdout._handle.setBlocking(true);

const PORT = parseInt(process.argv[2] || '8650', 10);
const SUBDOMAIN = process.argv[3] || 'lrp-dash';
const HOST = 'loca.lt';

let remoteIP = null;
let tunInfo = null;

function httpGet(path) {
  return new Promise((resolve, reject) => {
    const req = https.request({
      hostname: HOST, port: 443, path, method: 'GET',
      family: 4, headers: { 'User-Agent': 'node-tunnel' }
    }, res => {
      let data = '';
      res.on('data', d => data += d);
      res.on('end', () => resolve({ status: res.statusCode, data }));
    });
    req.on('error', reject);
    req.end();
    setTimeout(() => reject(new Error('http timeout')), 10000);
  });
}

function openRelay() {
  if (!tunInfo || !remoteIP) return;
  
  const remote = net.connect({ host: remoteIP, port: tunInfo.port, family: 4 });
  remote.setKeepAlive(true);

  remote.on('connect', () => {
    const local = net.connect({ host: '127.0.0.1', port: PORT, family: 4 });
    
    local.on('connect', () => {
      remote.pipe(local).pipe(remote);
    });

    local.on('error', () => {});
    local.on('close', () => { remote.end(); });
  });

  remote.on('error', () => {});
  remote.on('close', () => {
    setTimeout(openRelay, 3000);
  });
}

async function init() {
  try {
    const res = await httpGet('/' + SUBDOMAIN);
    tunInfo = JSON.parse(res.data);
    
    const addrs = await new Promise((resolve, reject) => {
      dns.resolve4(HOST, (err, a) => err ? reject(err) : resolve(a));
    });
    remoteIP = addrs[0];

    process.stdout.write('url=' + tunInfo.url + '\n');

    for (let i = 0; i < (tunInfo.max_conn_count || 2); i++) {
      openRelay();
    }
  } catch(e) {
    process.stderr.write('init error: ' + e.message + '\n');
    setTimeout(init, 5000);
  }
}

init();
setInterval(() => {}, 60000);
