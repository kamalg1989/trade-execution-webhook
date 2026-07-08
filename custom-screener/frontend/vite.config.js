import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Served under https://ohmstockvault.duckdns.org/custom-screener/
export default defineConfig({
  base: '/custom-screener/',
  plugins: [react()],
  server: {
    port: 5183,
    proxy: {
      // dev: forward API + charts to the running services
      '/custom-screener/api': { target: 'http://localhost:8005', changeOrigin: true,
        rewrite: (p) => p.replace(/^\/custom-screener\/api/, '/api') },
      '/api/v1': { target: 'http://localhost:8001', changeOrigin: true },
    },
  },
});
