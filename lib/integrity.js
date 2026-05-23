const os = require('os');
const { screen } = require('electron');
const { _exec, scanProcessOutput } = require('./utils');
const {
  VM_MAC_PREFIXES, ALL_PROCESSES, SCAN_TYPE_MAP, VM_GPU_RENDERERS,
} = require('../config');

async function runIntegrityChecks() {
  const flags = [];
  const isWin = process.platform === 'win32';
  const isMac = process.platform === 'darwin';

  // 1. VM Detection — MAC address prefixes
  try {
    const nets = os.networkInterfaces();
    for (const [name, addrs] of Object.entries(nets)) {
      for (const a of addrs) {
        if (a.mac && a.mac !== '00:00:00:00:00:00') {
          const prefix = a.mac.substring(0, 8).toLowerCase();
          if (VM_MAC_PREFIXES.includes(prefix)) {
            flags.push({ type: 'vm_detected', severity: 'high',
              details: `VM MAC address detected (${a.mac}, interface: ${name})` });
          }
        }
      }
    }
  } catch(e) { console.warn('[Integrity] MAC check failed:', e.message); }

  // 2. VPN network interfaces
  try {
    const nets = os.networkInterfaces();
    const NAMED_VPN = [/^tailscale/i, /^zt[a-z0-9]/i, /^gpd\d/i,
                       /^proton/i, /^nordlynx/i, /^wg\d+$/i];
    const GENERIC_TUNNEL = [/^tun\d+$/i, /^tap\d+$/i, /^utun\d+$/i, /^ppp\d+$/i];

    const isRoutable = (a) => {
      if (!a || a.internal) return false;
      if (a.address === '127.0.0.1' || a.address === '::1') return false;
      if (a.family === 'IPv4') {
        if (a.address.startsWith('169.254.')) return false;
        return true;
      }
      if (a.family === 'IPv6') {
        if (a.scopeid && a.scopeid !== 0) return false;
        const lower = a.address.toLowerCase();
        if (lower.startsWith('fe80:') || lower.startsWith('fe80::')) return false;
        return true;
      }
      return false;
    };

    for (const [name, addrs] of Object.entries(nets)) {
      const named = NAMED_VPN.some(p => p.test(name));
      if (named && addrs.some(a => !a.internal)) {
        flags.push({ type: 'vpn_detected', severity: 'high',
          details: `VPN interface active: ${name}` });
        continue;
      }
      const generic = GENERIC_TUNNEL.some(p => p.test(name));
      if (generic && addrs.some(isRoutable)) {
        flags.push({ type: 'vpn_detected', severity: 'high',
          details: `Tunnel interface with routable address: ${name}` });
      }
    }
  } catch(e) { console.warn('[Integrity] VPN check failed:', e.message); }

  // 3. Multiple monitors
  try {
    const displays = screen.getAllDisplays();
    if (displays.length > 1) {
      flags.push({ type: 'multiple_monitors', severity: 'medium',
        details: `${displays.length} displays detected` });
    }
  } catch(e) { console.warn('[Integrity] display check failed:', e.message); }

  // 4. Proxy environment variables
  const PROXY_VARS = ['HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','SOCKS_PROXY',
                       'http_proxy','https_proxy','all_proxy','socks_proxy'];
  for (const v of PROXY_VARS) {
    if (process.env[v]) {
      flags.push({ type: 'proxy_detected', severity: 'high',
        details: `Proxy env var: ${v}=${process.env[v]}` });
      break;
    }
  }

  // 5. Electron debug flags
  if (process.argv.some(a => a.includes('--inspect') || a.includes('--remote-debugging-port'))) {
    flags.push({ type: 'debugger_detected', severity: 'high',
      details: 'Launched with debugging flags (--inspect or --remote-debugging-port)' });
  }

  // 6. Async shell checks — run in parallel
  const tasks = [];

  // Process list — ONE call, scan for everything
  if (isWin) {
    tasks.push(_exec('tasklist /fo csv /nh', 8000).then(output => {
      const scanFlags = scanProcessOutput(output, ALL_PROCESSES, SCAN_TYPE_MAP);
      flags.push(...scanFlags);
    }));
  } else if (isMac) {
    tasks.push(_exec('ps -eo comm', 5000).then(output => {
      const scanFlags = scanProcessOutput(output, ALL_PROCESSES, SCAN_TYPE_MAP);
      flags.push(...scanFlags);
    }));
  }

  // 7. VM detection via BIOS/model
  if (isWin) {
    tasks.push(
      _exec('wmic computersystem get model,manufacturer /format:list', 5000).then(output => {
        if (!output) return;
        const lower = output.toLowerCase();
        const vmKw = ['vmware', 'virtualbox', 'hyper-v', 'qemu', 'xen', 'parallels', 'virtual machine'];
        for (const kw of vmKw) {
          if (lower.includes(kw)) {
            flags.push({ type: 'vm_detected', severity: 'high',
              details: `VM indicator in hardware info: ${kw}` });
            break;
          }
        }
      })
    );
    tasks.push(
      _exec('reg query "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Internet Settings" /v ProxyEnable', 5000)
        .then(output => {
          if (output && /ProxyEnable\s+REG_DWORD\s+0x1/i.test(output)) {
            flags.push({ type: 'proxy_detected', severity: 'high',
              details: 'Windows system proxy is enabled' });
          }
        })
    );
  }

  if (isMac) {
    tasks.push(
      _exec('sysctl -n machdep.cpu.brand_string', 3000).then(output => {
        if (!output) return;
        const lower = output.toLowerCase();
        if (lower.includes('qemu') || lower.includes('virtual')) {
          flags.push({ type: 'vm_detected', severity: 'high',
            details: `Virtual CPU: ${output.trim()}` });
        }
      })
    );
    tasks.push(
      _exec('scutil --proxy', 3000).then(output => {
        if (!output) return;
        const httpOn = /HTTPEnable\s*:\s*1/i.test(output);
        const httpsOn = /HTTPSEnable\s*:\s*1/i.test(output);
        const socksOn = /SOCKSEnable\s*:\s*1/i.test(output);
        if (httpOn || httpsOn || socksOn) {
          const types = [];
          if (httpOn) types.push('HTTP');
          if (httpsOn) types.push('HTTPS');
          if (socksOn) types.push('SOCKS');
          flags.push({ type: 'proxy_detected', severity: 'high',
            details: `System proxy active: ${types.join(', ')}` });
        }
      })
    );
  }

  await Promise.all(tasks);

  console.log(`[Integrity] ${flags.length} flag(s) found`);
  for (const f of flags) {
    console.log(`  [${f.severity.toUpperCase()}] ${f.type}: ${f.details}`);
  }
  return flags;
}

async function checkGPU(win) {
  try {
    const renderer = await win.webContents.executeJavaScript(`
      (function() {
        try {
          const c = document.createElement('canvas');
          const gl = c.getContext('webgl') || c.getContext('experimental-webgl');
          if (!gl) return '';
          const ext = gl.getExtension('WEBGL_debug_renderer_info');
          return ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : '';
        } catch(e) { return ''; }
      })()
    `);
    if (renderer) {
      const lower = renderer.toLowerCase();
      for (const vr of VM_GPU_RENDERERS) {
        if (lower.includes(vr)) {
          return {
            type: 'vm_detected', severity: 'high',
            details: `Virtual GPU renderer: ${renderer}`
          };
        }
      }
    }
  } catch(e) { console.error('[Integrity] GPU check error:', e.message); }
  return null;
}

module.exports = { runIntegrityChecks, checkGPU };
