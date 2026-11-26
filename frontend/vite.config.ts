import { readFileSync, existsSync } from 'node:fs'
import { createHash } from 'node:crypto'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import type { IncomingMessage, ServerResponse } from 'node:http'
import type { Plugin } from 'vite'
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import { parse as parseYaml } from 'yaml'

const frontendDir = path.dirname(fileURLToPath(import.meta.url))
const projectRoot = path.resolve(frontendDir, '..')
const regionConfigPath = path.join(projectRoot, 'configs', 'regions.yaml')
const maplibreWorkerPath = path.join(frontendDir, 'node_modules', 'maplibre-gl', 'dist', 'maplibre-gl-worker.mjs')

type AssetDescriptor = { id: string; path: string; format: string; description?: string; fields?: string[]; geometry_type?: string }

function json(res: ServerResponse, status: number, value: unknown) {
  res.statusCode = status
  res.setHeader('content-type', 'application/json; charset=utf-8')
  res.setHeader('cache-control', 'no-store')
  res.end(JSON.stringify(value))
}

function regionCatalog() {
  const config = parseYaml(readFileSync(regionConfigPath, 'utf8')) as { regions?: Record<string, { town?: string; role?: string }> }
  return Object.entries(config.regions ?? {}).map(([id, details]) => ({ id, town: details.town ?? id, role: details.role ?? 'supplementary' }))
}

function manifestFor(regionId: string) {
  if (!/^[a-z0-9_]+$/.test(regionId) || !regionCatalog().some((region) => region.id === regionId)) return null
  const dir = path.join(projectRoot, 'data', 'processed', 'regions', regionId)
  const manifestPath = path.join(dir, 'manifest.json')
  const layerManifestPath = path.join(dir, 'layers_manifest.json')
  if (!existsSync(manifestPath) || !existsSync(layerManifestPath)) return null
  const manifest = JSON.parse(readFileSync(manifestPath, 'utf8')) as { data_version?: string; release_status?: string; files?: Record<string, { bytes: number; sha256: string }> }
  const layerManifest = JSON.parse(readFileSync(layerManifestPath, 'utf8')) as { layers?: Array<Record<string, unknown>> }
  const assets = new Map<string, AssetDescriptor>()
  for (const row of layerManifest.layers ?? []) {
    if (typeof row.layer_id !== 'string' || typeof row.path !== 'string') continue
    assets.set(row.layer_id, {
      id: row.layer_id,
      path: row.path,
      format: String(row.format ?? ''),
      description: typeof row.description === 'string' ? row.description : undefined,
      fields: Array.isArray(row.fields) ? row.fields.map(String) : undefined,
      geometry_type: typeof row.geometry_type === 'string' ? row.geometry_type : undefined,
    })
  }
  const extra: Record<string, string> = {
    boundary: `data/processed/regions/${regionId}/boundary.geojson`,
    analysis_area: `data/processed/regions/${regionId}/analysis_area.geojson`,
    shelters: `data/processed/regions/${regionId}/shelters.geojson`,
    road_edges: `data/processed/regions/${regionId}/map_layers/road_edges.geojson`,
    network_buffer: `data/processed/regions/${regionId}/map_layers/network_buffer.geojson`,
  }
  for (const [id, assetPath] of Object.entries(extra)) {
    if (manifest.files?.[assetPath]) assets.set(id, { id, path: assetPath, format: 'GeoJSON' })
  }
  return { dir, manifest, assets }
}

function readVerifiedGeoJson(regionId: string, asset: AssetDescriptor) {
  const bundle = manifestFor(regionId)
  if (!bundle || asset.format.toLowerCase() !== 'geojson') throw new Error('图层不可用或不是 GeoJSON')
  const normalized = asset.path.replaceAll('\\', '/')
  const expectedPrefix = `data/processed/regions/${regionId}/`
  if (!normalized.startsWith(expectedPrefix) || normalized.includes('..')) throw new Error('图层路径未通过白名单检查')
  const manifestEntry = bundle.manifest.files?.[normalized]
  if (!manifestEntry) throw new Error('图层不在区域 manifest 中')
  const absolutePath = path.resolve(projectRoot, normalized)
  const regionRoot = path.resolve(projectRoot, 'data', 'processed', 'regions', regionId) + path.sep
  if (!absolutePath.startsWith(regionRoot)) throw new Error('图层路径越界')
  const rawBytes = readFileSync(absolutePath)
  const rawDigest = createHash('sha256').update(rawBytes).digest('hex')
  if (rawBytes.length === manifestEntry.bytes && rawDigest === manifestEntry.sha256) return rawBytes

  // Git may check out a text GeoJSON with LF even when A's manifest records
  // the exact CRLF artifact. Reconstruct those declared bytes in memory, then
  // compare both byte length and digest. This mirrors Module B's strict
  // raw-or-reconstructed-CRLF verification and never blesses arbitrary text.
  const hasBom = rawBytes.subarray(0, 3).equals(Buffer.from([0xef, 0xbb, 0xbf]))
  const decoded = rawBytes.toString('utf8').replace(/^\uFEFF/, '')
  const normalizedText = decoded.replace(/\r\n?/g, '\n')
  const reconstructed = Buffer.from(normalizedText.replace(/\n/g, '\r\n'), 'utf8')
  const declaredBytes = hasBom ? Buffer.concat([Buffer.from([0xef, 0xbb, 0xbf]), reconstructed]) : reconstructed
  const reconstructedDigest = createHash('sha256').update(declaredBytes).digest('hex')
  if (declaredBytes.length !== manifestEntry.bytes || reconstructedDigest !== manifestEntry.sha256) {
    throw new Error(`SHA-256 校验失败：${asset.id}`)
  }
  return declaredBytes
}

function dataAdapter(): Plugin {
  const handler = (req: IncomingMessage, res: ServerResponse, next: (error?: unknown) => void) => {
    if (req.method !== 'GET' || !req.url?.startsWith('/__a/v2/')) return next()
    const url = new URL(req.url, 'http://localhost')
    if (url.pathname === '/__a/v2/regions') {
      try {
        const regions = regionCatalog().flatMap((region) => {
          const bundle = manifestFor(region.id)
          const qualityPath = path.join(bundle?.dir ?? '', 'quality_summary.json')
          if (!bundle || !bundle.manifest.files?.[`data/processed/regions/${region.id}/quality_summary.json`] || !existsSync(qualityPath)) return []
          const quality = JSON.parse(readFileSync(qualityPath, 'utf8')) as Record<string, any>
          const regionLayers = [...bundle.assets.values()].map((asset) => ({
            layer_id: asset.id,
            description: asset.description ?? asset.id,
            geometry_type: asset.geometry_type ?? 'unknown',
            format: asset.format,
            fields: asset.fields ?? [],
            available: asset.format.toLowerCase() === 'geojson',
            unavailable_reason: asset.format.toLowerCase() === 'geojson' ? undefined : '当前地图版本不直接展示 GeoTIFF',
          }))
          return [{
            region_id: region.id,
            town: region.town,
            role: region.role,
            data_version: bundle.manifest.data_version,
            release_status: bundle.manifest.release_status,
            data_status: quality.data_status,
            demand_nodes: quality.travel_matrix?.demand_nodes ?? quality.population?.positive_cells,
            shelters: quality.spatial_shelters,
            od_rows: quality.travel_matrix?.rows,
            reachable_pairs: quality.travel_matrix?.reachable_pairs,
            population_estimate: quality.population?.population_sum,
            coverage: quality.spatial_analysis?.coverage ?? {},
            quality: {
              real_pilot_accepted: quality.real_pilot_accepted,
              verdict: quality.verdict,
              unresolved_shelters: quality.unresolved_shelters,
              boundary_status: quality.boundary_status,
              blockers: quality.blockers ?? [],
            },
            layers: regionLayers,
          }]
        })
        return json(res, 200, { regions })
      } catch (error) {
        return json(res, 500, { error: error instanceof Error ? error.message : '区域元数据读取失败' })
      }
    }
    const match = url.pathname.match(/^\/__a\/v2\/layers\/([a-z0-9_]+)\/([a-z0-9_]+)$/)
    if (!match) return json(res, 404, { error: '未知 A v2 只读数据路由' })
    const [, regionId, layerId] = match
    const bundle = manifestFor(regionId)
    const asset = bundle?.assets.get(layerId)
    if (!bundle || !asset) return json(res, 404, { error: '未知区域或图层 ID' })
    try {
      const body = readVerifiedGeoJson(regionId, asset)
      res.statusCode = 200
      res.setHeader('content-type', 'application/geo+json; charset=utf-8')
      res.setHeader('cache-control', 'public, max-age=3600, immutable')
      res.setHeader('x-shelterplan-manifest-sha256', bundle.manifest.files?.[asset.path]?.sha256 ?? '')
      res.end(body)
    } catch (error) {
      json(res, 409, { error: error instanceof Error ? error.message : '图层校验失败' })
    }
  }
  return {
    name: 'shelterplan-a-v2-readonly-data',
    configureServer(server) { server.middlewares.use(handler) },
    configurePreviewServer(server) { server.middlewares.use(handler) },
  }
}

function maplibreWorkerAdapter(): Plugin {
  const worker = readFileSync(maplibreWorkerPath)
  const handler = (req: IncomingMessage, res: ServerResponse, next: (error?: unknown) => void) => {
    if (req.method !== 'GET' || new URL(req.url ?? '/', 'http://localhost').pathname !== '/__module_d/maplibre-worker.mjs') return next()
    res.statusCode = 200
    res.setHeader('content-type', 'text/javascript; charset=utf-8')
    res.setHeader('cache-control', 'public, max-age=3600')
    res.end(worker)
  }
  return {
    name: 'shelterplan-maplibre-worker',
    configureServer(server) { server.middlewares.use(handler) },
    configurePreviewServer(server) { server.middlewares.use(handler) },
    generateBundle() { this.emitFile({ type: 'asset', fileName: 'maplibre-gl-worker.mjs', source: worker }) },
  }
}

export default defineConfig(({ mode }) => ({
  plugins: [react(), dataAdapter(), maplibreWorkerAdapter()],
  resolve: { alias: { '@': path.join(frontendDir, 'src') } },
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: {
      '/api/c': {
        target: process.env.C_API_TARGET ?? 'http://127.0.0.1:8080',
        changeOrigin: false,
        rewrite: (requestPath) => requestPath.replace(/^\/api\/c/, ''),
      },
    },
  },
  preview: {
    host: '127.0.0.1',
    port: 4173,
    strictPort: true,
    proxy: {
      '/api/c': {
        target: process.env.C_API_TARGET ?? 'http://127.0.0.1:8080',
        changeOrigin: false,
        rewrite: (requestPath) => requestPath.replace(/^\/api\/c/, ''),
      },
    },
  },
  test: { environment: 'jsdom', setupFiles: ['./src/test/setup.ts'], css: true, include: ['src/**/*.test.ts', 'src/**/*.test.tsx'] },
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes('/node_modules/maplibre-gl/')) return 'maplibre'
          if (id.includes('/node_modules/echarts/') || id.includes('/node_modules/zrender/')) return 'charts'
          if (id.includes('/node_modules/@tanstack/')) return 'query'
          if (id.includes('/node_modules/react/') || id.includes('/node_modules/react-dom/')) return 'react'
        },
      },
    },
  },
  define: { __MODULE_D_MODE__: JSON.stringify(mode) },
}))
