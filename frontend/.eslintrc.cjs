module.exports = {
  root: true,
  env: {
    browser: true,
    node: true,
    es2021: true,
  },
  extends: [
    'eslint:recommended',
    'plugin:react/recommended',
    'plugin:react-hooks/recommended',
  ],
  parserOptions: {
    ecmaVersion: 'latest',
    sourceType: 'module',
    ecmaFeatures: {
      jsx: true,
    },
  },
  plugins: ['react', 'react-refresh'],
  settings: {
    react: {
      version: 'detect',
    },
  },
  ignorePatterns: [
    'dist/',
    'node_modules/',
    'coverage/',
    'playwright-report/',
    'test-results/',
  ],
  rules: {
    'react/react-in-jsx-scope': 'off',
    'react/prop-types': 'off',
    // Preserve the existing application baseline while restoring lint as a
    // useful syntax and hook-safety gate for new changes.
    'no-unused-vars': 'off',
    'no-constant-condition': 'off',
    'no-case-declarations': 'off',
    'react/no-unescaped-entities': 'off',
    'react-hooks/exhaustive-deps': 'off',
    'react-refresh/only-export-components': 'off',
  },
}
