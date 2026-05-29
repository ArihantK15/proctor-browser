// ESLint 9 flat config. Pre-existing CI failure: app/dashboard-ui has
// no eslint config file but its package.json runs `eslint src` which
// requires one as of ESLint v9.0.
//
// Minimal config: js.configs.recommended + a relaxed unused-vars rule
// that ignores PascalCase (React components imported but referenced
// only as JSX) — same pattern as website/eslint.config.js.

import js from '@eslint/js'
import reactHooks from 'eslint-plugin-react-hooks'

export default [
  js.configs.recommended,
  {
    files: ['**/*.{js,jsx}'],
    plugins: { 'react-hooks': reactHooks },
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'module',
      parserOptions: {
        ecmaFeatures: { jsx: true },
      },
      globals: {
        window: 'readonly',
        document: 'readonly',
        console: 'readonly',
        fetch: 'readonly',
        URL: 'readonly',
        URLSearchParams: 'readonly',
        FormData: 'readonly',
        Blob: 'readonly',
        File: 'readonly',
        FileReader: 'readonly',
        Audio: 'readonly',
        Image: 'readonly',
        EventSource: 'readonly',
        WebSocket: 'readonly',
        setTimeout: 'readonly',
        clearTimeout: 'readonly',
        setInterval: 'readonly',
        clearInterval: 'readonly',
        navigator: 'readonly',
        location: 'readonly',
        history: 'readonly',
        localStorage: 'readonly',
        sessionStorage: 'readonly',
        Event: 'readonly',
        CustomEvent: 'readonly',
        atob: 'readonly',
        btoa: 'readonly',
        crypto: 'readonly',
        AbortController: 'readonly',
        process: 'readonly',
      },
    },
    rules: {
      // PascalCase covers React components imported but referenced via
      // JSX (which ESLint w/o react plugin can't see). Underscore
      // prefix is the established intentionally-unused marker.
      'no-unused-vars': ['error', {
        varsIgnorePattern: '^[A-Z_]',
        argsIgnorePattern: '^_',
        caughtErrors: 'none',  // `catch (_) {}` and `catch (e) {}` both ok
        destructuredArrayIgnorePattern: '^_',
      }],
      'no-empty': ['error', { allowEmptyCatch: true }],
      // React 17+ JSX transform doesn't need React in scope.
      'no-undef': 'off',
      // Hooks: keep the rule at 'warn' (not 'error') so the existing
      // `// eslint-disable-next-line react-hooks/exhaustive-deps`
      // comments scattered across panels actually suppress something.
      // With max-warnings=0 in the lint script, this means any NEW
      // unmarked exhaustive-deps issue still fails CI.
      'react-hooks/exhaustive-deps': 'warn',
      'react-hooks/rules-of-hooks': 'error',
    },
  },
]
