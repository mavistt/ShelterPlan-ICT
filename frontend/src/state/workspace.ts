import { create } from 'zustand'
import type { BasemapStyle, CStatus, OptimizationCandidate, TaskMode, TurnEnvelope } from '../api/types'

export type WorkspaceMessage = {
  id: string
  role: 'assistant' | 'user'
  text: string
  createdAt: string
  turn?: TurnEnvelope
}

export type FlyToTarget = {
  lon: number
  lat: number
  zoom?: number
  shelterId?: string
  seq: number
}

type WorkspaceState = {
  regionId: string
  sessionId: string | null
  sessionBusy: boolean
  sessionError: string | null
  messages: WorkspaceMessage[]
  activeTurnId: string | null
  activeStatus: CStatus | null
  activeResultId: string | null
  // Map-First UI state
  activeMode: TaskMode
  drawerOpen: boolean
  copilotOpen: boolean
  evidenceOpen: boolean
  introOpen: boolean
  basemapStyle: BasemapStyle
  tiandituToken: string
  selectedShelterId: string | null
  flyToTarget: FlyToTarget | null
  stressedShelterIds: string[]
  optCandidates: OptimizationCandidate[]
  // Actions
  setRegion: (regionId: string) => void
  setSession: (sessionId: string) => void
  setSessionBusy: (busy: boolean, error?: string | null) => void
  setMessages: (messages: WorkspaceMessage[]) => void
  addMessage: (message: WorkspaceMessage) => void
  updateTurn: (turn: TurnEnvelope) => void
  setActive: (turnId: string | null, status: CStatus | null) => void
  clearSessionMessages: () => void
  setActiveMode: (mode: TaskMode) => void
  setDrawerOpen: (open: boolean) => void
  setCopilotOpen: (open: boolean) => void
  setEvidenceOpen: (open: boolean) => void
  setIntroOpen: (open: boolean) => void
  setBasemapStyle: (style: BasemapStyle) => void
  setTiandituToken: (token: string) => void
  setSelectedShelterId: (shelterId: string | null) => void
  flyTo: (lon: number, lat: number, shelterId?: string, zoom?: number) => void
  toggleStressedShelter: (shelterId: string) => void
  setStressedShelterIds: (ids: string[]) => void
  addOptCandidate: (shelterId: string, capacity?: string, cost?: string) => void
  setOptCandidates: (candidates: OptimizationCandidate[]) => void
}

const initialRegion = typeof window !== 'undefined' ? sessionStorage.getItem('sp-region') ?? 'shiqi' : 'shiqi'
const initialIntro = typeof window !== 'undefined' ? sessionStorage.getItem('sp-intro-dismissed') !== '1' : true
const initialTianditu = typeof window !== 'undefined' ? localStorage.getItem('sp-tianditu-token') ?? '' : ''

export const useWorkspace = create<WorkspaceState>((set) => ({
  regionId: initialRegion,
  sessionId: null,
  sessionBusy: false,
  sessionError: null,
  messages: [],
  activeTurnId: null,
  activeStatus: null,
  activeResultId: null,
  activeMode: 'assess',
  drawerOpen: true,
  copilotOpen: true,
  evidenceOpen: false,
  introOpen: initialIntro,
  basemapStyle: 'amap_light',
  tiandituToken: initialTianditu,
  selectedShelterId: null,
  flyToTarget: null,
  stressedShelterIds: [],
  optCandidates: [],

  setRegion: (regionId) => {
    if (typeof window !== 'undefined') sessionStorage.setItem('sp-region', regionId)
    set({
      regionId,
      sessionId: null,
      messages: [],
      activeTurnId: null,
      activeStatus: null,
      activeResultId: null,
      selectedShelterId: null,
      stressedShelterIds: [],
      optCandidates: [],
    })
  },
  setSession: (sessionId) => set({ sessionId, sessionError: null }),
  setSessionBusy: (sessionBusy, sessionError = null) => set({ sessionBusy, sessionError }),
  setMessages: (messages) => {
    const lastSucceeded = [...messages].reverse().find((m) => m.turn?.status === 'SUCCEEDED' && m.turn?.result_id)
    const lastTurn = [...messages].reverse().find((m) => m.turn)
    set({
      messages,
      activeTurnId: lastTurn?.turn?.turn_id ?? null,
      activeStatus: lastTurn?.turn?.status ?? null,
      activeResultId: lastSucceeded?.turn?.result_id ?? null,
    })
  },
  addMessage: (message) => set((state) => ({ messages: [...state.messages, message] })),
  updateTurn: (turn) => set((state) => ({
    messages: state.messages.map((message) => message.id === turn.turn_id ? { ...message, turn } : message),
    activeStatus: turn.status,
    activeResultId: turn.status === 'SUCCEEDED' ? turn.result_id ?? state.activeResultId : state.activeResultId,
  })),
  setActive: (activeTurnId, activeStatus) => set({ activeTurnId, activeStatus }),
  clearSessionMessages: () => set({ messages: [], activeTurnId: null, activeStatus: null, activeResultId: null }),
  setActiveMode: (activeMode) => set({ activeMode, drawerOpen: true }),
  setDrawerOpen: (drawerOpen) => set({ drawerOpen }),
  setCopilotOpen: (copilotOpen) => set({ copilotOpen }),
  setEvidenceOpen: (evidenceOpen) => set({ evidenceOpen }),
  setIntroOpen: (introOpen) => {
    if (typeof window !== 'undefined' && !introOpen) sessionStorage.setItem('sp-intro-dismissed', '1')
    set({ introOpen })
  },
  setBasemapStyle: (basemapStyle) => set({ basemapStyle }),
  setTiandituToken: (tiandituToken) => {
    if (typeof window !== 'undefined') localStorage.setItem('sp-tianditu-token', tiandituToken)
    set({ tiandituToken })
  },
  setSelectedShelterId: (selectedShelterId) => set({ selectedShelterId }),
  flyTo: (lon, lat, shelterId, zoom = 14.2) => set((state) => ({
    selectedShelterId: shelterId ?? state.selectedShelterId,
    flyToTarget: { lon, lat, zoom, shelterId, seq: (state.flyToTarget?.seq ?? 0) + 1 },
  })),
  toggleStressedShelter: (shelterId) => set((state) => ({
    stressedShelterIds: state.stressedShelterIds.includes(shelterId)
      ? state.stressedShelterIds.filter((id) => id !== shelterId)
      : [...state.stressedShelterIds, shelterId],
  })),
  setStressedShelterIds: (stressedShelterIds) => set({ stressedShelterIds }),
  addOptCandidate: (shelterId, capacity = '400', cost = '1') => set((state) => {
    if (state.optCandidates.length >= 12) return state
    if (state.optCandidates.some((c) => c.shelterId === shelterId)) return state
    return {
      optCandidates: [
        ...state.optCandidates,
        { id: `cand-${crypto.randomUUID()}`, shelterId, capacity, cost },
      ],
    }
  }),
  setOptCandidates: (optCandidates) => set({ optCandidates }),
}))
