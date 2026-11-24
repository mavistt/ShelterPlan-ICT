import { useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import * as maplibregl from 'maplibre-gl'
import type { GeoJSONSource, Map as MapLibreMap } from 'maplibre-gl'
import {
  AlertTriangle, Compass, Crosshair, Layers3, LocateFixed, LoaderCircle,
  Minus, Plus, Settings2, TrendingUp, X,
} from 'lucide-react'
import { api } from '../api/client'
import type { AssignmentRow, BasemapStyle, LayerInfo, RegionMetadata, ShelterRecord } from '../api/types'
import type { FeatureCollection } from 'geojson'
import { useWorkspace } from '../state/workspace'
import { gcj02ToWgs84, transformGeoJsonToGcj02, wgs84ToGcj02 } from '../utils/coordTransform'

maplibregl.setWorkerUrl('/__module_d/maplibre-worker.mjs')

type Props = {
  region: RegionMetadata
  shelters: ShelterRecord[]
  activeLayers: string[]
  onActiveLayersChange: (layers: string[]) => void
  resultRows?: AssignmentRow[]
  expandedShelterIds?: string[]
}

const LABELS: Record<string, string> = {
  boundary: '分析范围边界',
  analysis_area: '分析核心区',
  shelters: '避难场所点位',
  shelter_capacity: '避难场所（按容量/负载）',
  population_heatmap: '人口密度热力 (Kepler)',
  population_density_raster: '人口密度栅格',
  coverage_10min: '10 分钟步行圈',
  coverage_15min: '15 分钟步行圈',
  coverage_20min: '20 分钟步行圈',
  coverage_30min: '30 分钟步行圈',
  access_gaps_10min: '10 分钟避难盲区',
  access_gaps_15min: '15 分钟避难盲区',
  access_gaps_20min: '20 分钟避难盲区',
  access_gaps_30min: '30 分钟避难盲区',
  network_buffer: '路网缓冲区',
  road_edges: '本地步行路网',
}

const COLOR: Record<string, string> = {
  shelter_capacity: '#0d9488',
  shelters: '#0d9488',
  population_heatmap: '#f97316',
  coverage_10min: '#059669',
  coverage_15min: '#0f766e',
  coverage_20min: '#2563eb',
  coverage_30min: '#6d28d9',
  access_gaps_10min: '#4338ca',
  access_gaps_15min: '#4f46e5',
  access_gaps_20min: '#6d28d9',
  access_gaps_30min: '#7e22ce',
  road_edges: '#94a3b8',
  network_buffer: '#99f6e4',
  analysis_area: '#cbd5e1',
  boundary: '#475569',
}

const GEOM_LABELS: Record<string, string> = {
  Point: '空间点阵',
  MultiPoint: '空间点阵',
  LineString: '矢量线网',
  MultiLineString: '矢量线网',
  Polygon: '区域多边形',
  MultiPolygon: '区域多边形',
}

function friendlyLayerMeta(layer: LayerInfo): string {
  if (layer.layer_id === 'boundary' || layer.layer_id === 'analysis_area') return '行政/分析范围轮廓'
  if (layer.layer_id === 'road_edges') return 'OSM 步行路网'
  if (layer.layer_id === 'network_buffer') return '路网分析缓冲区'
  if (layer.layer_id === 'population_heatmap') return 'WorldPop 100m 人口栅格'
  if (layer.layer_id === 'shelter_capacity' || layer.layer_id === 'shelters') return '避难场所点位'
  return GEOM_LABELS[layer.geometry_type] ?? 'GeoJSON 矢量层'
}

function buildBasemapStyle(style: BasemapStyle, tiandituToken: string): maplibregl.StyleSpecification {
  if (style === 'tianditu' && tiandituToken.trim()) {
    const tk = encodeURIComponent(tiandituToken.trim())
    return {
      version: 8,
      sources: {
        'tdt-vec': {
          type: 'raster',
          tiles: [
            `https://t0.tianditu.gov.cn/vec_w/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=vec&STYLE=default&TILEMATRIXSET=w&FORMAT=tiles&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}&tk=${tk}`,
            `https://t1.tianditu.gov.cn/vec_w/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=vec&STYLE=default&TILEMATRIXSET=w&FORMAT=tiles&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}&tk=${tk}`,
          ],
          tileSize: 256,
          attribution: '© 天地图 · CGCS2000',
        },
        'tdt-cva': {
          type: 'raster',
          tiles: [
            `https://t0.tianditu.gov.cn/cva_w/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=cva&STYLE=default&TILEMATRIXSET=w&FORMAT=tiles&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}&tk=${tk}`,
            `https://t1.tianditu.gov.cn/cva_w/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=cva&STYLE=default&TILEMATRIXSET=w&FORMAT=tiles&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}&tk=${tk}`,
          ],
          tileSize: 256,
        },
      },
      layers: [
        { id: 'background', type: 'background', paint: { 'background-color': '#eef2f5' } },
        { id: 'basemap-vec', type: 'raster', source: 'tdt-vec', paint: { 'raster-opacity': 0.94, 'raster-saturation': -0.18 } },
        { id: 'basemap-cva', type: 'raster', source: 'tdt-cva', paint: { 'raster-opacity': 0.92 } },
      ],
    }
  }
  if (style === 'arcgis_street') {
    return {
      version: 8,
      sources: {
        'arcgis-street': {
          type: 'raster',
          tiles: [
            'https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}',
          ],
          tileSize: 256,
          maxzoom: 17,
          attribution: '© Esri · WGS84',
        },
      },
      layers: [
        { id: 'background', type: 'background', paint: { 'background-color': '#eef2f6' } },
        {
          id: 'basemap-arcgis-street',
          type: 'raster',
          source: 'arcgis-street',
          paint: { 'raster-opacity': 0.94, 'raster-saturation': -0.12 },
        },
      ],
    }
  }
  if (style === 'amap_light') {
    return {
      version: 8,
      sources: {
        'amap-standard': {
          type: 'raster',
          tiles: [
            'https://webrd01.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=2&style=8&x={x}&y={y}&z={z}',
            'https://webrd02.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=2&style=8&x={x}&y={y}&z={z}',
            'https://webrd03.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=2&style=8&x={x}&y={y}&z={z}',
            'https://webrd04.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=2&style=8&x={x}&y={y}&z={z}',
          ],
          tileSize: 256,
          maxzoom: 18,
          attribution: '© 高德地图 · 前端实时 WGS84→GCJ02 纠偏',
        },
      },
      layers: [
        { id: 'background', type: 'background', paint: { 'background-color': '#f1f4f8' } },
        {
          id: 'basemap-amap',
          type: 'raster',
          source: 'amap-standard',
          paint: {
            'raster-opacity': 0.96,
            'raster-saturation': -0.18,
            'raster-contrast': 0.03,
            'raster-brightness-min': 0.03,
          },
        },
      ],
    }
  }
  return {
    version: 8,
    sources: {},
    layers: [{ id: 'background', type: 'background', paint: { 'background-color': '#f2f4f7' } }],
  }
}

function featureBounds(collection: FeatureCollection): [[number, number], [number, number]] | null {
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity
  const visit = (coords: unknown) => {
    if (!Array.isArray(coords)) return
    if (coords.length >= 2 && typeof coords[0] === 'number' && typeof coords[1] === 'number') {
      minX = Math.min(minX, coords[0]); maxX = Math.max(maxX, coords[0])
      minY = Math.min(minY, coords[1]); maxY = Math.max(maxY, coords[1])
      return
    }
    coords.forEach(visit)
  }
  collection.features.forEach((feature) => {
    if (feature.geometry && 'coordinates' in feature.geometry) visit(feature.geometry.coordinates)
  })
  return Number.isFinite(minX) ? [[minX, minY], [maxX, maxY]] : null
}

function paintFor(layer: LayerInfo | undefined, id: string, sourceId: string) {
  const color = COLOR[id] ?? '#0d9488'
  if (id === 'population_heatmap') {
    return [{
      id: `${sourceId}-heat`,
      source: sourceId,
      type: 'heatmap' as const,
      paint: {
        'heatmap-weight': ['interpolate', ['linear'], ['coalesce', ['to-number', ['get', 'pop_est']], 0], 0, 0.05, 80, 0.55, 420, 1],
        'heatmap-intensity': ['interpolate', ['linear'], ['zoom'], 10, 0.85, 15, 1.5],
        'heatmap-radius': ['interpolate', ['linear'], ['zoom'], 10, 10, 15, 28],
        'heatmap-opacity': 0.62,
        'heatmap-color': [
          'interpolate', ['linear'], ['heatmap-density'],
          0, 'rgba(254, 240, 138, 0)',
          0.14, 'rgba(253, 224, 71, 0.48)',
          0.34, 'rgba(251, 146, 60, 0.68)',
          0.62, 'rgba(239, 68, 68, 0.78)',
          0.86, 'rgba(190, 18, 60, 0.86)',
          1, 'rgba(136, 19, 55, 0.92)',
        ],
      },
    }]
  }
  if (id === 'boundary') {
    return [
      { id: `${sourceId}-fill`, source: sourceId, type: 'fill' as const, paint: { 'fill-color': '#0f172a', 'fill-opacity': 0.02 } },
      { id: `${sourceId}-line`, source: sourceId, type: 'line' as const, paint: { 'line-color': '#475569', 'line-width': 1.7, 'line-opacity': 0.68, 'line-dasharray': [4, 3] } },
    ]
  }
  if (id === 'analysis_area' || id === 'network_buffer') {
    return [
      { id: `${sourceId}-fill`, source: sourceId, type: 'fill' as const, paint: { 'fill-color': color, 'fill-opacity': 0.06 } },
      { id: `${sourceId}-line`, source: sourceId, type: 'line' as const, paint: { 'line-color': color, 'line-width': 1.2, 'line-opacity': 0.5 } },
    ]
  }
  if (id === 'road_edges') {
    return [{
      id: `${sourceId}-line`,
      source: sourceId,
      type: 'line' as const,
      paint: {
        'line-color': '#94a3b8',
        'line-width': ['interpolate', ['linear'], ['zoom'], 10, 0.5, 14, 1.4],
        'line-opacity': 0.52,
      },
    }]
  }
  if (id.startsWith('coverage_')) {
    return [{
      id: `${sourceId}-pts`,
      source: sourceId,
      type: 'circle' as const,
      filter: ['==', ['get', 'covered'], true],
      paint: {
        'circle-radius': ['interpolate', ['linear'], ['zoom'], 10, 2.2, 13, 4.4, 15, 7.8],
        'circle-color': color,
        'circle-opacity': 0.56,
      },
    }]
  }
  if (id.startsWith('access_gaps_')) {
    return [{
      id: `${sourceId}-pts`,
      source: sourceId,
      type: 'circle' as const,
      paint: {
        'circle-radius': ['interpolate', ['linear'], ['zoom'], 10, 1.9, 13, 3.4, 15, 6.2],
        'circle-color': color,
        'circle-opacity': 0.42,
      },
    }]
  }
  if (layer?.geometry_type?.toLowerCase().includes('polygon')) {
    return [
      { id: `${sourceId}-fill`, source: sourceId, type: 'fill' as const, paint: { 'fill-color': color, 'fill-opacity': 0.16 } },
      { id: `${sourceId}-line`, source: sourceId, type: 'line' as const, paint: { 'line-color': color, 'line-width': 1.2, 'line-opacity': 0.55 } },
    ]
  }
  const isShelter = id === 'shelter_capacity' || id === 'shelters'
  if (isShelter) {
    return [
      {
        id: `${sourceId}-halo`,
        source: sourceId,
        type: 'circle' as const,
        filter: ['any', ['==', ['get', '__is_selected'], true], ['==', ['get', '__is_expanded'], true], ['==', ['get', '__is_stressed'], true]],
        paint: {
          'circle-radius': [
            'interpolate', ['linear'], ['coalesce', ['to-number', ['get', 'capacity_persons_effective']], 100],
            10, 13, 500, 17, 8000, 23,
          ],
          'circle-color': [
            'case',
            ['==', ['get', '__is_stressed'], true], 'rgba(225, 29, 72, 0.24)',
            ['==', ['get', '__is_expanded'], true], 'rgba(13, 148, 136, 0.28)',
            'rgba(37, 99, 235, 0.24)',
          ],
          'circle-stroke-color': [
            'case',
            ['==', ['get', '__is_stressed'], true], '#e11d48',
            ['==', ['get', '__is_expanded'], true], '#0d9488',
            '#2563eb',
          ],
          'circle-stroke-width': 2,
        },
      },
      {
        id: `${sourceId}-circle`,
        source: sourceId,
        type: 'circle' as const,
        paint: {
          'circle-radius': [
            'interpolate', ['linear'], ['coalesce', ['to-number', ['get', 'capacity_persons_effective']], 100],
            10, 5.5,
            200, 7.5,
            1500, 10.5,
            8000, 13.5,
          ],
          'circle-color': [
            'match',
            ['get', '__status_bucket'],
            'danger', '#ef4444',
            'amber', '#f59e0b',
            '#10b981',
          ],
          'circle-stroke-color': '#ffffff',
          'circle-stroke-width': 2.2,
          'circle-opacity': 0.96,
        },
      },
    ]
  }
  return [{
    id: `${sourceId}-circle`,
    source: sourceId,
    type: 'circle' as const,
    paint: { 'circle-radius': 2.5, 'circle-color': color, 'circle-opacity': 0.6 },
  }]
}

type StatusBucket = 'ok' | 'amber' | 'danger'

function enrichShelterFeatures(
  sourceData: FeatureCollection,
  resultByShelterId: Map<string, AssignmentRow>,
  selectedShelterId: string | null,
  stressedSet: Set<string>,
  expandedSet: Set<string>,
  visibleBuckets: Set<StatusBucket>,
): FeatureCollection {
  return {
    ...sourceData,
    features: sourceData.features
      .map((feature) => {
        const sid = String(feature.properties?.shelter_id ?? '')
        const result = sid ? resultByShelterId.get(sid) : undefined
        const isStressed = stressedSet.has(sid)
        const util = typeof result?.utilization === 'number' ? result.utilization : null
        const bucket: StatusBucket = isStressed || (util !== null && util >= 0.9)
          ? 'danger'
          : util !== null && util >= 0.5
            ? 'amber'
            : 'ok'
        return {
          ...feature,
          properties: {
            ...feature.properties,
            ...(result ? { __result_load: result.load_persons, __result_util: result.utilization } : {}),
            __status_bucket: bucket,
            __is_selected: sid === selectedShelterId,
            __is_stressed: isStressed,
            __is_expanded: expandedSet.has(sid),
          },
        }
      })
      .filter((feature) => visibleBuckets.has(feature.properties.__status_bucket as StatusBucket)),
  }
}

export function MapView({
  region,
  shelters,
  activeLayers,
  onActiveLayersChange,
  resultRows = [],
  expandedShelterIds = [],
}: Props) {
  const container = useRef<HTMLDivElement>(null)
  const mapRef = useRef<MapLibreMap | null>(null)
  const [mapReady, setMapReady] = useState(false)
  const [styleRevision, setStyleRevision] = useState(0)
  const [layerData, setLayerData] = useState<Record<string, GeoJSON.FeatureCollection>>({})
  const [loadedRegionId, setLoadedRegionId] = useState(region.region_id)
  const [loadErrors, setLoadErrors] = useState<Record<string, string>>({})
  const [loadingLayers, setLoadingLayers] = useState<string[]>([])
  const [layerPanelOpen, setLayerPanelOpen] = useState(false)
  const [basemapMenuOpen, setBasemapMenuOpen] = useState(false)
  const [tileFallbackNotice, setTileFallbackNotice] = useState(false)
  const [cursorCoordinates, setCursorCoordinates] = useState<[number, number] | null>(null)
  const [heatmapOpacity, setHeatmapOpacity] = useState(0.55)
  const [visibleBuckets, setVisibleBuckets] = useState<StatusBucket[]>(['ok', 'amber', 'danger'])
  const fittedRegionRef = useRef<string | null>(null)

  const visibleBucketSet = useMemo(() => new Set<StatusBucket>(visibleBuckets), [visibleBuckets])
  const toggleBucket = (bucket: StatusBucket) => {
    setVisibleBuckets((prev) => {
      const next = prev.includes(bucket) ? prev.filter((b) => b !== bucket) : [...prev, bucket]
      // If user narrowed down to a single non-empty category, smoothly pan to show those markers
      if (next.length === 1 && mapRef.current) {
        const raw = loadedRegionId === region.region_id ? projectedLayerData.shelter_capacity : undefined
        if (raw) {
          const filtered = enrichShelterFeatures(raw, resultByShelterId, selectedShelterId, stressedSet, expandedSet, new Set(next))
          const b = featureBounds(filtered)
          if (b) {
            mapRef.current.fitBounds(b, { padding: { top: 96, bottom: 88, left: 80, right: 80 }, maxZoom: 14.2, duration: 500 })
          }
        }
      }
      return next
    })
  }

  const basemapStyle = useWorkspace((s) => s.basemapStyle)
  const setBasemapStyle = useWorkspace((s) => s.setBasemapStyle)
  const tiandituToken = useWorkspace((s) => s.tiandituToken)
  const setTiandituToken = useWorkspace((s) => s.setTiandituToken)
  const selectedShelterId = useWorkspace((s) => s.selectedShelterId)
  const setSelectedShelterId = useWorkspace((s) => s.setSelectedShelterId)
  const flyToTarget = useWorkspace((s) => s.flyToTarget)
  const stressedShelterIds = useWorkspace((s) => s.stressedShelterIds)
  const toggleStressedShelter = useWorkspace((s) => s.toggleStressedShelter)
  const optCandidates = useWorkspace((s) => s.optCandidates)
  const addOptCandidate = useWorkspace((s) => s.addOptCandidate)
  const activeMode = useWorkspace((s) => s.activeMode)
  const setActiveMode = useWorkspace((s) => s.setActiveMode)

  const [tokenDraft, setTokenDraft] = useState(tiandituToken)

  const layers = useMemo(() => region.layers.filter((layer) => layer.available), [region.layers])
  const resultByShelterId = useMemo(() => new Map(resultRows.map((row) => [row.shelter_id, row])), [resultRows])
  const shelterById = useMemo(() => new Map(shelters.map((s) => [s.shelter_id, s])), [shelters])
  const expandedSet = useMemo(() => new Set([
    ...expandedShelterIds,
    ...(activeMode === 'optimize' ? optCandidates.map((c) => c.shelterId) : []),
  ]), [activeMode, expandedShelterIds, optCandidates])
  const stressedSet = useMemo(() => new Set(stressedShelterIds), [stressedShelterIds])

  // Refs for reading latest interactive state inside initial layer mounting without causing full layer rebuilds
  const interactiveRef = useRef({ resultByShelterId, selectedShelterId, stressedSet, expandedSet, visibleBucketSet, heatmapOpacity })
  interactiveRef.current = { resultByShelterId, selectedShelterId, stressedSet, expandedSet, visibleBucketSet, heatmapOpacity }

  const effectiveLayers = useMemo(() => {
    if (basemapStyle === 'local_vector' && !activeLayers.includes('road_edges')) {
      return [...activeLayers, 'road_edges']
    }
    return activeLayers
  }, [activeLayers, basemapStyle])

  // Reset cached region layers on region change
  useEffect(() => {
    setLayerData({})
    setLoadedRegionId(region.region_id)
    setLoadErrors({})
    setLoadingLayers([])
    setCursorCoordinates(null)
    fittedRegionRef.current = null
    if (container.current) delete container.current.dataset.didFit
  }, [region.region_id])

  // Initialize MapLibre once
  useEffect(() => {
    if (!container.current || mapRef.current) return
    const map = new maplibregl.Map({
      container: container.current,
      style: buildBasemapStyle(basemapStyle, tiandituToken),
      center: [113.38, 22.53],
      zoom: 12.6,
      attributionControl: false,
    })
    map.addControl(new maplibregl.ScaleControl({ maxWidth: 96, unit: 'metric' }), 'bottom-right')
    mapRef.current = map

    let tileErrors = 0
    map.on('error', (event) => {
      const msg = String((event as { error?: { message?: string } })?.error?.message ?? '')
      if (/tile|arcgisonline|tianditu|Failed to fetch|NetworkError/i.test(msg)) {
        tileErrors += 1
        if (tileErrors >= 6 && useWorkspace.getState().basemapStyle !== 'local_vector') {
          setTileFallbackNotice(true)
          setBasemapStyle('local_vector')
        }
      }
    })
    map.on('load', () => {
      setMapReady(true)
      setStyleRevision((r) => r + 1)
    })
    let lastCoord = 0
    map.on('mousemove', (event) => {
      const now = performance.now()
      if (now - lastCoord < 90) return
      lastCoord = now
      const useGcj = useWorkspace.getState().basemapStyle === 'amap_light'
      const [lng, lat] = useGcj
        ? gcj02ToWgs84(event.lngLat.lng, event.lngLat.lat)
        : [event.lngLat.lng, event.lngLat.lat]
      setCursorCoordinates([lng, lat])
      const hit = map.queryRenderedFeatures(event.point).some((f) => f.properties?.shelter_id)
      map.getCanvas().style.cursor = hit ? 'pointer' : ''
    })
    map.on('mouseout', () => setCursorCoordinates(null))
    map.on('click', (event) => {
      const features = map.queryRenderedFeatures(event.point)
      const feature = features.find((item) => item.properties?.shelter_id)
      if (!feature?.properties?.shelter_id) {
        setSelectedShelterId(null)
        return
      }
      setSelectedShelterId(String(feature.properties.shelter_id))
    })
    return () => {
      map.remove()
      mapRef.current = null
      setMapReady(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Switch basemap style dynamically
  const lastStyleKeyRef = useRef(`${basemapStyle}:${tiandituToken}`)
  const styleReadyRef = useRef(false)
  useEffect(() => {
    if (mapReady) styleReadyRef.current = true
  }, [mapReady])

  useEffect(() => {
    const map = mapRef.current
    const key = `${basemapStyle}:${tiandituToken}`
    if (!map || !mapReady || key === lastStyleKeyRef.current) return
    lastStyleKeyRef.current = key
    styleReadyRef.current = false
    map.setStyle(buildBasemapStyle(basemapStyle, tiandituToken))
    map.once('style.load', () => {
      styleReadyRef.current = true
      setStyleRevision((r) => r + 1)
    })
  }, [basemapStyle, mapReady, tiandituToken])

  // Close layer & basemap popovers whenever a shelter is selected so popovers never overlap
  useEffect(() => {
    if (selectedShelterId) {
      setLayerPanelOpen(false)
      setBasemapMenuOpen(false)
    }
  }, [selectedShelterId])

  // Smooth flyTo when triggered from drawer or Agent cards
  useEffect(() => {
    const map = mapRef.current
    if (!map || !mapReady || !flyToTarget) return
    const [tx, ty] = basemapStyle === 'amap_light'
      ? wgs84ToGcj02(flyToTarget.lon, flyToTarget.lat)
      : [flyToTarget.lon, flyToTarget.lat]
    map.flyTo({
      center: [tx, ty],
      zoom: Math.max(map.getZoom(), flyToTarget.zoom ?? 14.5),
      duration: 650,
      essential: true,
    })
  }, [basemapStyle, flyToTarget, mapReady])

  // Fetch missing active GeoJSON layers on demand
  useEffect(() => {
    let cancelled = false
    const currentLayerData = loadedRegionId === region.region_id ? layerData : {}
    const missing = effectiveLayers.filter((id) => !currentLayerData[id] && !loadErrors[id])
    if (!missing.length) return
    setLoadingLayers((current) => [...new Set([...current, ...missing])])
    Promise.all(missing.map(async (id) => {
      try {
        return [id, await api.geoJson(region.region_id, id)] as const
      } catch (error) {
        setLoadErrors((current) => ({ ...current, [id]: error instanceof Error ? error.message : '图层加载失败' }))
        return null
      }
    })).then((rows) => {
      if (cancelled) return
      setLayerData((current) => ({
        ...current,
        ...Object.fromEntries(rows.filter((row): row is readonly [string, FeatureCollection] => row !== null)),
      }))
      setLoadingLayers((current) => current.filter((id) => !missing.includes(id)))
    })
    return () => { cancelled = true }
  }, [effectiveLayers, layerData, loadedRegionId, loadErrors, region.region_id])

  // Memoize GCJ-02 projected GeoJSON layers when using Amap so WGS84 data aligns to 0m without mutating backend data
  const useGcj = basemapStyle === 'amap_light'
  const projectedLayerData = useMemo(() => {
    if (!useGcj) return layerData
    const out: Record<string, FeatureCollection> = {}
    for (const [key, fc] of Object.entries(layerData)) {
      out[key] = transformGeoJsonToGcj02(fc)
    }
    return out
  }, [layerData, useGcj])

  // Effect A: Mount/unmount GeoJSON layers ONLY when layer set, data, region, or basemap style changes (NO flicker!)
  const mountedRegionRef = useRef<string | null>(null)
  useEffect(() => {
    const map = mapRef.current
    if (!map || !mapReady || !styleReadyRef.current) return

    // If region changed, clear old sp-* sources/layers first
    if (mountedRegionRef.current !== region.region_id) {
      for (const layer of map.getStyle().layers ?? []) {
        if (layer.id.startsWith('sp-layer-')) map.removeLayer(layer.id)
      }
      for (const id of Object.keys(map.getStyle().sources ?? {})) {
        if (id.startsWith('sp-source-')) map.removeSource(id)
      }
      mountedRegionRef.current = region.region_id
    }

    const activeSet = new Set(effectiveLayers)
    // Remove only layers that were toggled off
    for (const layer of [...(map.getStyle().layers ?? [])]) {
      if (!layer.id.startsWith('sp-layer-')) continue
      const match = layer.id.match(/^sp-layer-sp-source-([a-z0-9_]+)-/)
      if (match && !activeSet.has(match[1])) {
        map.removeLayer(layer.id)
      }
    }
    for (const id of Object.keys(map.getStyle().sources ?? {})) {
      if (!id.startsWith('sp-source-')) continue
      const layerKey = id.replace(/^sp-source-/, '')
      if (!activeSet.has(layerKey)) {
        map.removeSource(id)
      }
    }

    const order = [...effectiveLayers].sort((a, b) => {
      const rank = (id: string) =>
        id === 'boundary' || id === 'analysis_area' ? 0
          : id === 'network_buffer' ? 1
            : id === 'road_edges' ? 2
              : id.startsWith('coverage_') ? 3
                : id === 'population_heatmap' ? 4
                  : id.startsWith('access_gaps_') ? 5
                    : id === 'shelter_capacity' || id === 'shelters' ? 6
                      : 4
      return rank(a) - rank(b)
    })

    let bounds: [[number, number], [number, number]] | null = null
    for (const id of order) {
      const sourceData = loadedRegionId === region.region_id ? projectedLayerData[id] : undefined
      if (!sourceData) continue
      const sourceId = `sp-source-${id}`
      const isShelterLayer = id === 'shelter_capacity' || id === 'shelters'
      if (!map.getSource(sourceId)) {
        const data = isShelterLayer
          ? enrichShelterFeatures(
            sourceData,
            interactiveRef.current.resultByShelterId,
            interactiveRef.current.selectedShelterId,
            interactiveRef.current.stressedSet,
            interactiveRef.current.expandedSet,
            interactiveRef.current.visibleBucketSet,
          )
          : sourceData
        map.addSource(sourceId, { type: 'geojson', data })
        const descriptor = layers.find((item) => item.layer_id === id)
        paintFor(descriptor, id, sourceId).forEach((layer) => {
          const spec = { ...layer, id: `sp-layer-${layer.id}` } as maplibregl.LayerSpecification
          if (id === 'population_heatmap' && spec.type === 'heatmap' && spec.paint) {
            spec.paint['heatmap-opacity'] = interactiveRef.current.heatmapOpacity
          }
          map.addLayer(spec)
        })
      }

      if (id === 'shelter_capacity' || (id === 'boundary' && !bounds)) {
        const nextBounds = featureBounds(sourceData)
        if (nextBounds) bounds = nextBounds
      }
    }

    // Ensure shelter markers always stay on top of newly added heatmap/coverage layers
    for (const shelterLayerId of ['sp-layer-sp-source-shelter_capacity-halo', 'sp-layer-sp-source-shelter_capacity-circle']) {
      if (map.getLayer(shelterLayerId)) map.moveLayer(shelterLayerId)
    }

    if (bounds && fittedRegionRef.current !== region.region_id) {
      map.fitBounds(bounds, { padding: { top: 88, bottom: 72, left: 60, right: 60 }, maxZoom: 13.4, duration: 550 })
      fittedRegionRef.current = region.region_id
      map.getContainer().dataset.didFit = 'yes'
    }
  }, [effectiveLayers, layers, loadedRegionId, mapReady, projectedLayerData, region.region_id, styleRevision])

  // Effect B: In-place shelter GeoJSON data update on selection/stress/optimization/result/filter changes (0 flicker!)
  useEffect(() => {
    const map = mapRef.current
    if (!map || !mapReady || !styleReadyRef.current) return
    const raw = loadedRegionId === region.region_id ? projectedLayerData.shelter_capacity : undefined
    const src = map.getSource('sp-source-shelter_capacity') as GeoJSONSource | undefined
    if (!raw || !src) return
    src.setData(enrichShelterFeatures(raw, resultByShelterId, selectedShelterId, stressedSet, expandedSet, visibleBucketSet))
  }, [expandedSet, loadedRegionId, mapReady, projectedLayerData.shelter_capacity, region.region_id, resultByShelterId, selectedShelterId, stressedSet, styleRevision, visibleBucketSet])

  // Effect C: Live 60fps heatmap opacity adjustment preserving relative thermal contrast
  useEffect(() => {
    const map = mapRef.current
    const layerId = 'sp-layer-sp-source-population_heatmap-heat'
    if (!map || !mapReady || !styleReadyRef.current || !map.getLayer(layerId)) return
    map.setPaintProperty(layerId, 'heatmap-opacity', heatmapOpacity)
  }, [heatmapOpacity, mapReady, styleRevision])

  const toggleLayer = (id: string) => onActiveLayersChange(
    activeLayers.includes(id) ? activeLayers.filter((layer) => layer !== id) : [...activeLayers, id],
  )
  const zoom = (delta: number) => mapRef.current?.easeTo({ zoom: mapRef.current.getZoom() + delta, duration: 180 })
  const focusRegion = () => {
    const data = projectedLayerData.shelter_capacity ?? projectedLayerData.boundary
    const bounds = data ? featureBounds(data) : null
    if (bounds) mapRef.current?.fitBounds(bounds, { padding: { top: 88, bottom: 72, left: 60, right: 60 }, maxZoom: 13.4, duration: 500 })
  }
  const resetNorth = () => mapRef.current?.easeTo({ bearing: 0, pitch: 0, duration: 250 })

  const selectedShelter = selectedShelterId ? shelterById.get(selectedShelterId) : null
  const selectedResult = selectedShelterId ? resultByShelterId.get(selectedShelterId) : undefined
  const isStressed = selectedShelter ? stressedSet.has(selectedShelter.shelter_id) : false
  const isInOpt = selectedShelter ? optCandidates.some((c) => c.shelterId === selectedShelter.shelter_id) : false
  const heatmapOn = activeLayers.includes('population_heatmap')

  const selectedUtilPct = selectedResult && typeof selectedResult.utilization === 'number'
    ? selectedResult.utilization * 100
    : null
  const shelterStatus = isStressed
    ? { tone: 'danger', label: '模拟受损 · 已停用' }
    : selectedUtilPct !== null
      ? selectedUtilPct >= 90
        ? { tone: 'danger', label: `满载告警 · ${selectedUtilPct.toFixed(1)}%` }
        : selectedUtilPct >= 50
          ? { tone: 'amber', label: `负载紧张 · ${selectedUtilPct.toFixed(1)}%` }
          : { tone: 'ok', label: `承载充裕 · ${selectedUtilPct.toFixed(1)}%` }
      : isInOpt
        ? { tone: 'teal', label: '已纳入扩容候选' }
        : { tone: 'ok', label: '正常开放 · 待求解' }

  const bucketCounts = useMemo(() => {
    const counts = { ok: 0, amber: 0, danger: 0 }
    for (const s of shelters) {
      const isStr = stressedSet.has(s.shelter_id)
      const u = resultByShelterId.get(s.shelter_id)?.utilization
      const util = typeof u === 'number' ? u : null
      if (isStr || (util !== null && util >= 0.9)) counts.danger += 1
      else if (util !== null && util >= 0.5) counts.amber += 1
      else counts.ok += 1
    }
    return counts
  }, [resultByShelterId, shelters, stressedSet])

  const basemapLabel = basemapStyle === 'amap_light'
    ? '高德高清底图'
    : basemapStyle === 'arcgis_street'
      ? 'Esri 国际街道'
      : basemapStyle === 'tianditu'
        ? '天地图中文'
        : '本地矢量路网'

  return (
    <section className="map-stage" aria-label={`${region.town} 空间决策地图`}>
      <div className="map-canvas" ref={container} />
      {!mapReady && (
        <div className="map-loading-pill">
          <LoaderCircle size={15} className="spin" /> 正在初始化地理引擎…
        </div>
      )}

      {tileFallbackNotice && (
        <div className="map-fallback-toast">
          <span>外部底图连接受限，已自动切换为本地步行路网矢量底图</span>
          <button type="button" onClick={() => setTileFallbackNotice(false)} aria-label="关闭提示"><X size={13} /></button>
        </div>
      )}

      {/* Top-right floating map tools & basemap switcher */}
      <div className="map-top-right">
        <div className="basemap-pill-wrap">
          <button
            type="button"
            className={`glass-pill-btn ${basemapMenuOpen ? 'is-active' : ''}`}
            onClick={() => {
              setBasemapMenuOpen((v) => {
                const next = !v
                if (next) { setLayerPanelOpen(false); setSelectedShelterId(null) }
                return next
              })
            }}
          >
            <Settings2 size={14} />
            <span>{basemapLabel}</span>
          </button>
          {basemapMenuOpen && (
            <div className="glass-popover basemap-popover">
              <div className="popover-title">真实底图切换（自动纠偏零偏移）</div>
              <button
                type="button"
                className={`basemap-opt ${basemapStyle === 'amap_light' ? 'selected' : ''}`}
                onClick={() => { setBasemapStyle('amap_light'); setBasemapMenuOpen(false) }}
              >
                <strong>高德高清底图（Apple 浅色调色）</strong>
                <small>国内秒开 · 彩色水系绿地+中文地标 · 前端自动 WGS84→GCJ02 纠偏</small>
              </button>
              <button
                type="button"
                className={`basemap-opt ${basemapStyle === 'arcgis_street' ? 'selected' : ''}`}
                onClick={() => { setBasemapStyle('arcgis_street'); setBasemapMenuOpen(false) }}
              >
                <strong>Esri 国际街道底图 (WGS84)</strong>
                <small>原生 WGS84 坐标 · 无需纠偏</small>
              </button>
              <button
                type="button"
                className={`basemap-opt ${basemapStyle === 'local_vector' ? 'selected' : ''}`}
                onClick={() => { setBasemapStyle('local_vector'); setBasemapMenuOpen(false) }}
              >
                <strong>本地步行路网 (离线兜底)</strong>
                <small>直接渲染 A v2 本地 OSM 路网，断网可用</small>
              </button>
              <div className="tianditu-box">
                <label>
                  <span>天地图中文官方底图 Token（可选，CGCS2000）</span>
                  <div className="tianditu-input-row">
                    <input
                      type="text"
                      value={tokenDraft}
                      onChange={(e) => setTokenDraft(e.target.value)}
                      placeholder="粘贴免费天地图 tk…"
                    />
                    <button
                      type="button"
                      disabled={!tokenDraft.trim()}
                      onClick={() => {
                        setTiandituToken(tokenDraft.trim())
                        setBasemapStyle('tianditu')
                        setBasemapMenuOpen(false)
                      }}
                    >
                      启用
                    </button>
                  </div>
                </label>
              </div>
            </div>
          )}
        </div>

        <div className="map-zoom-stack">
          <button type="button" title="放大" aria-label="放大" onClick={() => zoom(1)}><Plus size={16} /></button>
          <button type="button" title="缩小" aria-label="缩小" onClick={() => zoom(-1)}><Minus size={16} /></button>
          <button type="button" title="回到全区视角" aria-label="回到全区视角" onClick={focusRegion}><LocateFixed size={15} /></button>
          <button type="button" title="重置正北" aria-label="重置正北" onClick={resetNorth}><Compass size={15} /></button>
        </div>
      </div>

      {/* Apple Maps Frosted-Glass Interactive Shelter Info Card */}
      {selectedShelter && (
        <div className={`shelter-focus-card tone-${shelterStatus.tone}`} role="dialog" aria-label="避难场所详情">
          <div className="sfc-status-strip">
            <span className={`sfc-status-pill tone-${shelterStatus.tone}`}>
              <i />
              {shelterStatus.label}
            </span>
            <button type="button" className="icon-close-btn" onClick={() => setSelectedShelterId(null)} aria-label="关闭场所卡片">
              <X size={15} />
            </button>
          </div>
          <div className="sfc-head">
            <div className="sfc-title">
              <div>
                <strong>{selectedShelter.name}</strong>
                <small>{selectedShelter.address || selectedShelter.community}</small>
              </div>
            </div>
          </div>

          {selectedUtilPct !== null && (
            <div className="sfc-load-meter">
              <div className="sfc-load-row">
                <span>本次求解承载负荷</span>
                <b>
                  {Math.round(selectedResult?.load_persons ?? 0).toLocaleString('zh-CN')} / {selectedShelter.capacity.toLocaleString('zh-CN')} 人
                </b>
              </div>
              <div className="sfc-load-track">
                <div
                  className={`sfc-load-fill tone-${shelterStatus.tone}`}
                  style={{ width: `${Math.min(100, selectedUtilPct)}%` }}
                />
              </div>
            </div>
          )}

          <div className="sfc-metrics">
            <div>
              <span>有效容量</span>
              <b>{selectedShelter.capacity.toLocaleString('zh-CN')} 人</b>
            </div>
            <div>
              <span>建筑面积</span>
              <b>{selectedShelter.area_sqm ? `${Math.round(selectedShelter.area_sqm).toLocaleString('zh-CN')} ㎡` : '—'}</b>
            </div>
            <div>
              <span>所属片区</span>
              <b>{selectedShelter.community || region.town}</b>
            </div>
          </div>
          <div className="sfc-id-line">
            <code>{selectedShelter.shelter_id}</code>
            <span>{selectedShelter.lon.toFixed(5)}°E, {selectedShelter.lat.toFixed(5)}°N</span>
          </div>
          <div className="sfc-actions">
            <button
              type="button"
              className={`sfc-action-btn ${isStressed ? 'is-danger-active' : ''}`}
              onClick={() => {
                toggleStressedShelter(selectedShelter.shelter_id)
                setActiveMode('stress')
              }}
            >
              <AlertTriangle size={14} />
              {isStressed ? '取消受损模拟' : '模拟此点受损'}
            </button>
            <button
              type="button"
              className={`sfc-action-btn ${isInOpt ? 'is-teal-active' : ''}`}
              onClick={() => {
                addOptCandidate(selectedShelter.shelter_id)
                setActiveMode('optimize')
              }}
            >
              <TrendingUp size={14} />
              {isInOpt ? '已在扩容清单' : '加入扩容候选'}
            </button>
          </div>
        </div>
      )}

      {/* Bottom-left Kepler-style legend: Layer popover opens UPWARD above bar so it never clips */}
      <div className="map-bottom-left">
        {layerPanelOpen && (
          <div className="glass-popover layer-popover">
            <div className="popover-title">
              <span>空间图层控制</span>
              <button type="button" className="icon-close-btn" onClick={() => setLayerPanelOpen(false)}><X size={14} /></button>
            </div>
            <div className="layer-list-scroll">
              {layers.map((layer) => (
                <label key={layer.layer_id} className="layer-row">
                  <span className="layer-swatch" style={{ '--swatch': COLOR[layer.layer_id] ?? '#0d9488' } as CSSProperties} />
                  <span className="layer-copy">
                    <strong>{LABELS[layer.layer_id] ?? layer.layer_id}</strong>
                    <small>{friendlyLayerMeta(layer)}</small>
                  </span>
                  {loadingLayers.includes(layer.layer_id) && <LoaderCircle size={13} className="spin" />}
                  <input
                    type="checkbox"
                    checked={activeLayers.includes(layer.layer_id)}
                    onChange={() => toggleLayer(layer.layer_id)}
                  />
                </label>
              ))}
            </div>
          </div>
        )}

        <div className="kepler-legend-pill">
          <div className="heatmap-slider-group">
            <button
              type="button"
              className={`heatmap-label-btn ${heatmapOn && heatmapOpacity > 0 ? 'is-on' : ''}`}
              onClick={() => {
                if (!heatmapOn) {
                  toggleLayer('population_heatmap')
                  if (heatmapOpacity === 0) setHeatmapOpacity(0.55)
                } else {
                  setHeatmapOpacity((v) => (v > 0 ? 0 : 0.55))
                }
              }}
              title="点击快速隐藏/恢复热力图"
            >
              热力
            </button>
            <input
              type="range"
              className="heatmap-opacity-range"
              min={0}
              max={1}
              step={0.05}
              value={heatmapOn ? heatmapOpacity : 0}
              onChange={(e) => {
                const v = Number(e.target.value)
                if (v > 0 && !heatmapOn) toggleLayer('population_heatmap')
                setHeatmapOpacity(v)
              }}
              aria-label="热力图透明度调节"
              title={`热力图强度 ${Math.round((heatmapOn ? heatmapOpacity : 0) * 100)}%`}
            />
            <span className="heatmap-pct-txt">{Math.round((heatmapOn ? heatmapOpacity : 0) * 100)}%</span>
          </div>
          <span className="legend-sep" />
          <div className="legend-filter-group" role="group" aria-label="按场所状态筛选显示">
            <button
              type="button"
              className={`status-filter-pill ${visibleBucketSet.has('ok') ? 'active' : 'muted'}`}
              onClick={() => toggleBucket('ok')}
              title="点击显示/隐藏绿色充裕点 (<50%)"
            >
              <i className="dot-teal" /><span>充裕 {bucketCounts.ok}</span>
            </button>
            <button
              type="button"
              className={`status-filter-pill ${visibleBucketSet.has('amber') ? 'active' : 'muted'}`}
              onClick={() => toggleBucket('amber')}
              title="点击显示/隐藏黄色紧张点 (50%–90%)"
            >
              <i className="dot-amber" /><span>紧张 {bucketCounts.amber}</span>
            </button>
            <button
              type="button"
              className={`status-filter-pill ${visibleBucketSet.has('danger') ? 'active' : 'muted'}`}
              onClick={() => toggleBucket('danger')}
              title="点击显示/隐藏红色满载/受损点 (≥90%)"
            >
              <i className="dot-red" /><span>满载 {bucketCounts.danger}</span>
            </button>
          </div>
          <span className="legend-sep" />
          <button
            type="button"
            className={`legend-layer-btn ${layerPanelOpen ? 'is-open' : ''}`}
            onClick={() => {
              setLayerPanelOpen((v) => {
                const next = !v
                if (next) { setSelectedShelterId(null); setBasemapMenuOpen(false) }
                return next
              })
            }}
          >
            <Layers3 size={14} /> 图层 ({activeLayers.length})
          </button>
        </div>
      </div>

      {/* Bottom-right subtle coordinate badge above scale bar */}
      <div className="map-coord-chip" aria-live="polite">
        <Crosshair size={12} />
        <span>
          {cursorCoordinates
            ? `${cursorCoordinates[0].toFixed(4)}°E, ${cursorCoordinates[1].toFixed(4)}°N`
            : `${region.town} · WGS84`}
        </span>
      </div>
    </section>
  )
}
