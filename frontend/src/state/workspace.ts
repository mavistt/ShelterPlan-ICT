import { create } from 'zustand'
import type { CStatus, TurnEnvelope } from '../api/types'

export type WorkspaceMessage = {
  id: string
  role: 'assistant' | 'user'
  text: string
  createdAt: string
  turn?: TurnEnvelope
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
  setRegion: (regionId: string) => void
  setSession: (sessionId: string) => void
  setSessionBusy: (busy: boolean, error?: string | null) => void
  addMessage: (message: WorkspaceMessage) => void
  updateTurn: (turn: TurnEnvelope) => void
  setActive: (turnId: string | null, status: CStatus | null) => void
  clearSessionMessages: () => void
}

const initialRegion = typeof window !== 'undefined' ? sessionStorage.getItem('sp-region') ?? 'shiqi' : 'shiqi'

export const useWorkspace = create<WorkspaceState>((set) => ({
  regionId: initialRegion,
  sessionId: null,
  sessionBusy: false,
  sessionError: null,
  messages: [],
  activeTurnId: null,
  activeStatus: null,
  activeResultId: null,
  setRegion: (regionId) => {
    if (typeof window !== 'undefined') sessionStorage.setItem('sp-region', regionId)
    set({ regionId, sessionId: null, messages: [], activeTurnId: null, activeStatus: null, activeResultId: null })
  },
  setSession: (sessionId) => set({ sessionId, sessionError: null }),
  setSessionBusy: (sessionBusy, sessionError = null) => set({ sessionBusy, sessionError }),
  addMessage: (message) => set((state) => ({ messages: [...state.messages, message] })),
  updateTurn: (turn) => set((state) => ({
    messages: state.messages.map((message) => message.id === turn.turn_id ? { ...message, turn } : message),
    activeStatus: turn.status,
    activeResultId: turn.status === 'SUCCEEDED' ? turn.result_id ?? null : state.activeResultId,
  })),
  setActive: (activeTurnId, activeStatus) => set({ activeTurnId, activeStatus }),
  clearSessionMessages: () => set({ messages: [], activeTurnId: null, activeStatus: null, activeResultId: null }),
}))
