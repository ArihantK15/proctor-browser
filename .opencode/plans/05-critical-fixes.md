# Critical Fixes — Implementation Plan

## Files to modify

### 3. Public API wrong table name
**File**: `app/routers/api.py`
- **Line 53**: `exam_configs` → `exam_config`
- **Line 62**: `exam_configs` → `exam_config`

### 4. Billing missing import
**File**: `app/routers/billing.py`
- **Line ~17**: After `from ..models.organizations import ...`, add:
  ```python
  from ..services.billing import _get_client, _is_live
  ```

### 2. Docker worker broken
Two changes:
- **File**: `Dockerfile` (line ~22)
  - Add `COPY worker.py ./worker.py` between the app code copy and scripts copy
  - Or use: `COPY . .` with a `.dockerignore` — but current pattern is selective, so add explicit copy
  
- **File**: `docker-compose.yml` (line ~63-66)
  - Override entrypoint for worker service:
  ```yaml
  worker:
    build: .
    entrypoint: python worker.py
    # remove `command: python worker.py` since entrypoint replaces it
  ```

### 1. Electron dependency vulnerabilities
**File**: `package.json` (line 16-17)
- `electron`: `^31.0.0` → `^33.0.0`
- `electron-builder`: `^24.13.3` → `^25.1.0`
- Then run `npm install` to update lockfile

Potential compat issues in `main.js`:
- `session.defaultSession` API changes (Electron 33 removed `webFrame` some APIs)
- `webContents.setWindowOpenHandler` is fine
- Need to test electron-updater compat

### 5. Electron remove `--no-sandbox`
**File**: `package.json` (line 7)
- Remove `--no-sandbox` flag from start script:
  ```json
  "start": "electron ."
  ```

**File**: `main.js` (if needed)
- On Linux, sandbox may be needed in Docker. Add conditional:
  ```javascript
  if (process.platform === 'linux' && !app.isPackaged) {
    app.commandLine.appendSwitch('no-sandbox');
  }
  ```

## Verification
1. `python3 -m pytest tests/ -x -q` — 482 should pass
2. `npm audit` — vuln count should drop from 11
3. `python3 -c "from app.routers.billing import ...; from app.routers.api import ..."` — import check
