// ESLint config for proctor-browser Electron app.
module.exports = {
  env: {
    browser: true,
    node: true,
    es2022: true,
  },
  globals: {
    WebSocket: 'readonly',
    Blob: 'readonly',
    URL: 'readonly',
    atob: 'readonly',
    btoa: 'readonly',
    performance: 'readonly',
  },
  parserOptions: {
    ecmaVersion: 2022,
    sourceType: 'module',
  },
  rules: {
    'no-unused-vars': 'off',
    'no-console': 'off',
    'no-undef': 'error',
  },
}
