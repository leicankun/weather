import js from '@eslint/js'
import vue from 'eslint-plugin-vue'
import globals from 'globals'
export default [
  {
    ignores: ['dist/**', 'node_modules/**', 'public/**', 'playwright-report/**', 'test-results/**'],
  },
  js.configs.recommended,
  ...vue.configs['flat/essential'],
  {
    languageOptions: { globals: { ...globals.browser, ...globals.node } },
    rules: {
      'no-unused-vars': ['error', { argsIgnorePattern: '^_', varsIgnorePattern: '^_' }],
      'vue/multi-word-component-names': 'off',
    },
  },
]
