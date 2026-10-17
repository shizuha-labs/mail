/**
 * Shared Vite defaults for standalone Shizuha service frontends.
 */
export function createShizuhaConfig({ port, serviceName }) {
  const basePath = process.env.VITE_BASE_PATH || '/'
  const allowedHosts = [
    'localhost',
    `shizuha-${serviceName}-frontend`,
    'nginx',
    'shizuha-nginx',
    'shizuha-nginx-dev',
    '.shizuha.com',
    `shizuha-shizuha-${serviceName}-frontend`,
    '.svc.cluster.local',
  ]

  return {
    base: basePath,
    optimizeDeps: { include: ['lucide-react'] },
    server: {
      host: '0.0.0.0',
      port,
      allowedHosts,
      hmr: process.env.VITE_HMR_DISABLE === 'true' ? false : true,
      watch: { usePolling: true },
    },
    build: {
      outDir: 'dist',
      sourcemap: true,
    },
  }
}

export default createShizuhaConfig
