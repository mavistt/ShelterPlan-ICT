import { useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import * as maplibregl from 'maplibre-gl'
import type { Map as MapLibreMap } from 'maplibre-gl'
import { Compass, Crosshair, Layers3, LocateFixed, LoaderCircle, MapPin, Plus, Minus } from 'lucide-react'
import { api } from '../api/client'
import type { AssignmentRow, LayerInfo, RegionMetadata } from '../api/types'
import type { FeatureCollection, Point } from 'geojson'

maplibregl.setWorkerUrl('/__module_d/maplibre-worker.mjs')

type Props = {
  region: RegionMetadata
  activeLayers: string[]
  onActiveLayersChange: (layers: string[]) => void
  resultRows?: AssignmentRow[]
  onShelterSelect?: (shelterId: string) => void
  compact?: boolean
}

const LABELS: Record<string, string> = {
  boundary: '分析边界', analysis_area: '分析范围', shelters: '避难场所',
  shelter_capacity: '场所与容量', population_heatmap: '人口估算热度', population_density_raster: '人口密度栅格',
  coverage_10min: '10 分钟可达', coverage_15min: '15 分钟可达', coverage_20min: '20 分钟可达', coverage_30min: '30 分钟可达',
  access_gaps_10min: '10 分钟可达缺口', access_gaps_15min: '15 分钟可达缺口', access_gaps_20min: '20 分钟可达缺口', access_gaps_30min: '30 分钟可达缺口',
  network_buffer: '路网分析缓冲区', road_edges: '步行路网',
}

const COLOR: Record<string, string> = {
  shelter_capacity: '#168a86', shelters: '#168a86', population_heatmap: '#e69a48',
  coverage_10min: '#73bca4', coverage_15min: '#35998f', coverage_20min: '#477fad', coverage_30min: '#795eb3',
  access_gaps_10min: '#d36e5c', access_gaps_15min: '#cb784c', access_gaps_20min: '#bb8245', access_gaps_30min: '#9b8564',
  road_edges: '#879ba2', network_buffer: '#9bcec7', analysis_area: '#c0dbd6', boundary: '#2b6968',
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
  const color = COLOR[id] ?? '#168a86'
  if (id === 'population_heatmap') return [{
    id: `${sourceId}-heat`, source: sourceId, type: 'heatmap' as const,
    paint: {
      'heatmap-weight': ['interpolate', ['linear'], ['coalesce', ['to-number', ['get', 'pop_est']], 0], 0, 0.08, 100, 0.6, 500, 1],
      'heatmap-intensity': ['interpolate', ['linear'], ['zoom'], 7, 0.8, 12, 1.4],
      'heatmap-radius': ['interpolate', ['linear'], ['zoom'], 7, 7, 13, 18],
      'heatmap-opacity': 0.58,
      'heatmap-color': ['interpolate', ['linear'], ['heatmap-density'], 0, 'rgba(57, 143, 141, 0)', 0.12, 'rgba(83, 173, 151, .52)', 0.38, 'rgba(224, 187, 116, .68)', 0.72, 'rgba(220, 126, 96, .78)', 1, 'rgba(158, 76, 89, .84)'],
    },
  }]
  if (id === 'boundary') return [{
    id: `${sourceId}-fill`, source: sourceId, type: 'fill' as const,
    paint: { 'fill-color': '#83b8b0', 'fill-opacity': 0.045 },
  }, {
    id: `${sourceId}-line`, source: sourceId, type: 'line' as const,
    paint: { 'line-color': color, 'line-width': 2, 'line-opacity': 0.85, 'line-dasharray': [3, 2] },
  }]
  if (id === 'analysis_area' || id === 'network_buffer') return [{
    id: `${sourceId}-fill`, source: sourceId, type: 'fill' as const,
    paint: { 'fill-color': color, 'fill-opacity': id === 'analysis_area' ? 0.11 : 0.08 },
  }, {
    id: `${sourceId}-line`, source: sourceId, type: 'line' as const,
    paint: { 'line-color': color, 'line-width': 1.5, 'line-opacity': 0.75 },
  }]
  if (id === 'road_edges') return [{ id: `${sourceId}-line`, source: sourceId, type: 'line' as const, paint: { 'line-color': color, 'line-width': ['interpolate', ['linear'], ['zoom'], 7, 0.45, 13, 1.25], 'line-opacity': 0.54 } }]
  if (layer?.geometry_type?.toLowerCase().includes('polygon') || id.startsWith('access_gaps_') || id.startsWith('coverage_')) {
    return [{
      id: `${sourceId}-fill`, source: sourceId, type: 'fill' as const,
      paint: { 'fill-color': color, 'fill-opacity': id.startsWith('access_gaps_') ? 0.32 : 0.18 },
    }, {
      id: `${sourceId}-line`, source: sourceId, type: 'line' as const,
      paint: { 'line-color': color, 'line-width': 1, 'line-opacity': 0.62 },
    }]
  }
  const isShelter = id === 'shelter_capacity' || id === 'shelters'
  return [{
    id: `${sourceId}-circle`, source: sourceId, type: 'circle' as const,
    paint: isShelter ? {
      'circle-radius': ['interpolate', ['linear'], ['zoom'], 7, 4, 13, 7],
      'circle-color': ['case', ['!', ['has', '__result_util']], color, ['step', ['to-number', ['get', '__result_util']], '#57a789', 0.65, '#e6ad4b', 0.9, '#d96d57']],
      'circle-stroke-color': '#ffffff',
      'circle-stroke-width': 1.7,
      'circle-opacity': 0.96,
    } : { 'circle-radius': 2.5, 'circle-color': color, 'circle-opacity': 0.6 },
  }]
}

export function MapView({ region, activeLayers, onActiveLayersChange, resultRows = [], onShelterSelect, compact = false }: Props) {
  const container = useRef<HTMLDivElement>(null)
  const mapRef = useRef<MapLibreMap | null>(null)
  const [mapReady, setMapReady] = useState(false)
  const [layerData, setLayerData] = useState<Record<string, GeoJSON.FeatureCollection>>({})
  const [loadedRegionId, setLoadedRegionId] = useState(region.region_id)
  const [loadErrors, setLoadErrors] = useState<Record<string, string>>({})
  const [loadingLayers, setLoadingLayers] = useState<string[]>([])
  const [layerPanelOpen, setLayerPanelOpen] = useState(false)
  const [selectedFeature, setSelectedFeature] = useState<Record<string, unknown> | null>(null)
  const [cursorCoordinates, setCursorCoordinates] = useState<[number, number] | null>(null)
  const fittedRegionRef = useRef<string | null>(null)
  const layers = useMemo(() => region.layers.filter((layer) => layer.available), [region.layers])
  const resultByShelterId = useMemo(() => new Map(resultRows.map((row) => [row.shelter_id, row])), [resultRows])
  const resultByShelterIdRef = useRef(resultByShelterId)
  resultByShelterIdRef.current = resultByShelterId

  useEffect(() => {
    setLayerData({})
    setLoadedRegionId(region.region_id)
    setLoadErrors({})
    setLoadingLayers([])
    setSelectedFeature(null)
    setCursorCoordinates(null)
    fittedRegionRef.current = null
    if (container.current) delete container.current.dataset.didFit
  }, [region.region_id])

  useEffect(() => {
    if (!container.current || mapRef.current) return
    const map = new maplibregl.Map({
      container: container.current,
      style: {
        version: 8,
        sources: {},
        layers: [{ id: 'background', type: 'background', paint: { 'background-color': '#eef3f2' } }],
      },
      center: [0, 0],
      zoom: 2,
      attributionControl: false,
      cooperativeGestures: true,
    })
    map.addControl(new maplibregl.AttributionControl({ compact: true, customAttribution: 'A v2 · OpenStreetMap / WorldPop model' }), 'bottom-left')
    map.addControl(new maplibregl.ScaleControl({ maxWidth: 100, unit: 'metric' }), 'bottom-right')
    mapRef.current = map
    map.on('load', () => setMapReady(true))
    let lastCoordinateUpdate = 0
    map.on('mousemove', (event) => {
      const now = performance.now()
      if (now - lastCoordinateUpdate < 100) return
      lastCoordinateUpdate = now
      setCursorCoordinates([event.lngLat.lng, event.lngLat.lat])
    })
    map.on('mouseout', () => setCursorCoordinates(null))
    map.on('click', (event) => {
      const features = map.queryRenderedFeatures(event.point)
      const feature = features.find((item) => item.properties?.shelter_id)
      if (!feature?.properties) return
      const properties = feature.properties as Record<string, unknown>
      const shelterId = String(properties.shelter_id ?? '')
      if (!shelterId) return
      setSelectedFeature(properties)
      onShelterSelect?.(shelterId)
      const coordinate = (feature.geometry as Point).coordinates as [number, number]
      const content = document.createElement('div')
      content.className = 'map-popup'
      const kicker = document.createElement('span'); kicker.className = 'map-popup-kicker'; kicker.textContent = '实际场所 ID'
      const id = document.createElement('strong'); id.textContent = shelterId
      const capacity = document.createElement('span'); capacity.textContent = `容量字段：${String(properties.capacity_persons_effective ?? properties.capacity_persons ?? '未提供')}`
      const note = document.createElement('em'); note.textContent = '坐标与容量为 DEMO_ONLY 数据'
      content.append(kicker, id, capacity, note)
      const utilization = resultByShelterIdRef.current.get(shelterId)?.utilization
      if (typeof utilization === 'number') {
        const result = document.createElement('span'); result.textContent = `本次求解利用率：${(utilization * 100).toFixed(1)}%`; content.append(result)
      }
      new maplibregl.Popup({ closeButton: true, maxWidth: '270px', className: 'sp-map-popup' }).setLngLat(coordinate).setDOMContent(content).addTo(map)
    })
    return () => { map.remove(); mapRef.current = null; setMapReady(false) }
  }, [onShelterSelect])

  useEffect(() => {
    let cancelled = false
    const currentLayerData = loadedRegionId === region.region_id ? layerData : {}
    const missing = activeLayers.filter((id) => !currentLayerData[id] && !loadErrors[id])
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
      setLayerData((current) => ({ ...current, ...Object.fromEntries(rows.filter((row): row is readonly [string, FeatureCollection] => row !== null)) }))
      setLoadingLayers((current) => current.filter((id) => !missing.includes(id)))
    })
    return () => { cancelled = true }
  }, [activeLayers, layerData, loadedRegionId, loadErrors, region.region_id])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !mapReady) return
    const clear = () => {
      for (const layer of map.getStyle().layers ?? []) {
        if (layer.id.startsWith('sp-layer-')) map.removeLayer(layer.id)
      }
      for (const id of Object.keys(map.getStyle().sources ?? {})) {
        if (id.startsWith('sp-source-')) map.removeSource(id)
      }
    }
    clear()
    const order = [...activeLayers].sort((a, b) => {
      const rank = (id: string) => id === 'boundary' || id === 'analysis_area' ? 0 : id === 'network_buffer' ? 1 : id === 'road_edges' ? 2 : id === 'population_heatmap' ? 3 : id.startsWith('access_gaps_') ? 4 : id === 'shelter_capacity' || id === 'shelters' ? 5 : 4
      return rank(a) - rank(b)
    })
    let bounds: [[number, number], [number, number]] | null = null
    for (const id of order) {
      const sourceData = loadedRegionId === region.region_id ? layerData[id] : undefined
      if (!sourceData) continue
      const data = id === 'shelter_capacity' && resultByShelterId.size
        ? { ...sourceData, features: sourceData.features.map((feature) => {
          const shelterId = feature.properties?.shelter_id
          const result = typeof shelterId === 'string' ? resultByShelterId.get(shelterId) : undefined
          return result ? { ...feature, properties: { ...feature.properties, __result_load: result.load_persons, __result_util: result.utilization } } : feature
        }) }
        : sourceData
      const sourceId = `sp-source-${id}`
      map.addSource(sourceId, { type: 'geojson', data, ...(id === 'population_heatmap' ? { maxzoom: 12 } : {}) })
      const descriptor = layers.find((item) => item.layer_id === id)
      const mapLayers = paintFor(descriptor, id, sourceId)
      mapLayers.forEach((layer) => map.addLayer({ ...layer, id: `sp-layer-${layer.id}` } as maplibregl.LayerSpecification))
      if (id === 'boundary' || id === 'shelter_capacity') {
        const nextBounds = featureBounds(sourceData)
        if (nextBounds) bounds = bounds
          ? [[Math.min(bounds[0][0], nextBounds[0][0]), Math.min(bounds[0][1], nextBounds[0][1])], [Math.max(bounds[1][0], nextBounds[1][0]), Math.max(bounds[1][1], nextBounds[1][1])]]
          : nextBounds
      }
    }
    if (bounds && fittedRegionRef.current !== region.region_id) {
      map.fitBounds(bounds, { padding: 56, maxZoom: 12, duration: 500 })
      fittedRegionRef.current = region.region_id
      map.getContainer().dataset.didFit = 'yes'
    }
  }, [activeLayers, layerData, layers, loadedRegionId, mapReady, region.region_id, resultByShelterId])

  const toggleLayer = (id: string) => onActiveLayersChange(
    activeLayers.includes(id) ? activeLayers.filter((layer) => layer !== id) : [...activeLayers, id],
  )
  const zoom = (delta: number) => {
    const map = mapRef.current
    if (map) map.easeTo({ zoom: map.getZoom() + delta, duration: 180 })
  }
  const focusRegion = () => {
    const data = layerData.boundary ?? layerData.shelter_capacity
    const bounds = data ? featureBounds(data) : null
    if (bounds) mapRef.current?.fitBounds(bounds, { padding: 64, maxZoom: 12, duration: 500 })
  }
  const resetNorth = () => mapRef.current?.easeTo({ bearing: 0, pitch: 0, duration: 250 })

  return (
    <section className={`map-shell ${compact ? 'map-shell-compact' : ''}`} aria-label={`${region.town} GIS 地图`}>
      <div className="map-canvas" ref={container} />
      {!mapReady && <div className="map-loading"><LoaderCircle size={18} className="spin" /> 正在初始化本地地图…</div>}
      <div className="map-heading">
        <div className="map-location-mark"><MapPin size={16} /></div>
        <div><strong>{region.town}</strong><span>WGS84 · A v2 区域数据</span></div>
      </div>
      <div className="map-controls">
        <button title="放大" aria-label="放大" onClick={() => zoom(1)}><Plus size={17} /></button>
        <button title="缩小" aria-label="缩小" onClick={() => zoom(-1)}><Minus size={17} /></button>
        <button title="定位区域" aria-label="定位区域" onClick={focusRegion}><LocateFixed size={17} /></button>
        <button title="指北" aria-label="指北" onClick={resetNorth}><Compass size={16} /></button>
      </div>
      <button className={`map-layer-trigger ${layerPanelOpen ? 'is-open' : ''}`} onClick={() => setLayerPanelOpen((open) => !open)}>
        <Layers3 size={16} /> 图层 <span>{activeLayers.length}</span>
      </button>
      {layerPanelOpen && <div className="map-layer-panel">
        <div className="layer-panel-head"><strong>地图图层</strong><span>按需加载</span></div>
        {layers.map((layer) => <label key={layer.layer_id} className="layer-option">
          <span className={`layer-dot layer-dot-${layer.layer_id}`} style={{ '--layer-color': COLOR[layer.layer_id] ?? '#168a86' } as CSSProperties} />
          <span className="layer-option-copy"><strong>{LABELS[layer.layer_id] ?? layer.layer_id}</strong><small>{layer.geometry_type} · GeoJSON</small></span>
          {loadingLayers.includes(layer.layer_id) && <LoaderCircle size={14} className="spin layer-spinner" />}
          <input type="checkbox" checked={activeLayers.includes(layer.layer_id)} onChange={() => toggleLayer(layer.layer_id)} />
          {loadErrors[layer.layer_id] && <small className="layer-error" title={loadErrors[layer.layer_id]}>校验失败</small>}
        </label>)}
        {region.layers.filter((layer) => !layer.available).map((layer) => <div className="layer-option layer-option-disabled" key={layer.layer_id}>
          <span className="layer-dot" /><span className="layer-option-copy"><strong>{LABELS[layer.layer_id] ?? layer.layer_id}</strong><small>{layer.unavailable_reason}</small></span><span>暂不可用</span>
        </div>)}
        <p className="layer-footnote">可达性图层表达网络候选覆盖，不代表容量分配结果。</p>
      </div>}
      {selectedFeature && <button className="map-selection" onClick={() => setSelectedFeature(null)}>
        已选场所 <b>{String(selectedFeature.shelter_id ?? '')}</b><span>清除</span>
      </button>}
      <div className="map-coordinate-readout" aria-live="polite"><Crosshair size={14} /><span><small>WGS84 · 光标坐标</small><strong>{cursorCoordinates ? `${cursorCoordinates[0].toFixed(5)}°E  ${cursorCoordinates[1].toFixed(5)}°N` : '移动光标读取经纬度'}</strong></span></div>
      <div className="map-scale-note">本地 A v2 图层 · 无外部底图</div>
    </section>
  )
}
