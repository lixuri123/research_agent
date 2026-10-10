import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const apiTarget = 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: Object.fromEntries(
      ['/remote/local', '/health', '/tasks', '/research', '/reports', '/sessions', '/memories', '/skills', '/mcp', '/workspaces', '/settings', '/profile'].map((path) => [
        path,
        { target: apiTarget, changeOrigin: true },
      ]),
    ),
  },
})

