import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

// `drop` strips console.* and debugger statements from production builds only.
// Dev (`vite` / `vite dev`) keeps them so diagnostics remain available.
export default defineConfig(({ mode }) => ({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  esbuild: {
    drop: mode === 'production' ? ['console', 'debugger'] : [],
  },
  server: {
    proxy: {
      '/api': {
        target: 'http://localhost:8899',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
  },
}))
