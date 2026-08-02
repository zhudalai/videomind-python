import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';
import { fileURLToPath } from 'url';
const __dirname = path.dirname(fileURLToPath(import.meta.url));
export default defineConfig({
    plugins: [react()],
    resolve: {
        alias: {
            '@': path.resolve(__dirname, './src'),
            '@/components': path.resolve(__dirname, './src/components'),
            '@/features': path.resolve(__dirname, './src/features'),
            '@/hooks': path.resolve(__dirname, './src/hooks'),
            '@/lib': path.resolve(__dirname, './src/lib'),
            '@/store': path.resolve(__dirname, './src/store'),
            '@/types': path.resolve(__dirname, './src/types'),
        },
    },
    server: {
        port: 4000,
        strictPort: true,
        host: '127.0.0.1',
        proxy: {
            '/api': {
                target: 'http://127.0.0.1:8001',
                changeOrigin: true,
            },
            '/sse': {
                target: 'http://127.0.0.1:8001',
                changeOrigin: true,
            },
        },
    },
});
