import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    environment: 'jsdom',
    setupFiles: ['./tests/frontend/setup.js'],
    globals: true,
    testTimeout: 30000,
  },
});
