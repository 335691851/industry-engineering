import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { '@mlightcad/cad-agent-plugin/register': '/src/cad-agent-disabled.ts' } },
  build: { rollupOptions: { input: { main: 'index.html', cad: 'cad-studio.html' }, output: {
    manualChunks(id) {
      if (id.includes('commonjsHelpers')) return 'shared-helpers'
      if (id.includes('node_modules') && !/node_modules\/(react|react-dom|scheduler|lucide-react)\//.test(id)) return 'cad-engine'
    }
  } } },
  server: { proxy: { '/api': 'http://127.0.0.1:8000' } }
})
