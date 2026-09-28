import { copyFile, mkdir } from 'node:fs/promises'
await mkdir(new URL('../public/assets/', import.meta.url), { recursive: true })
await copyFile(new URL('../node_modules/@mlightcad/cad-simple-viewer/dist/mtext-renderer-worker.js', import.meta.url), new URL('../public/assets/mtext-renderer-worker.js', import.meta.url))
