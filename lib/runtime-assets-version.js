const path = require('path');

// Pure decision (fs is injected so this is testable without touching the
// real disk): does the runtime-assets cache at cacheDir already satisfy
// expectedVersion, or does python-manager.js need to fetch a fresh copy?
// The marker file is written ONLY after a verified-successful extraction
// (see Task 3), so its mere presence with a matching version means the
// cache is trustworthy — never write it speculatively before that.
function needsRuntimeAssetFetch(expectedVersion, cacheDir, fs = require('fs')) {
  const markerPath = path.join(cacheDir, '.version');
  if (!fs.existsSync(markerPath)) return true;
  let contents;
  try {
    contents = fs.readFileSync(markerPath, 'utf8');
  } catch (e) {
    return true;
  }
  return contents.trim() !== String(expectedVersion);
}

module.exports = { needsRuntimeAssetFetch };
