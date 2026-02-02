'use client'

import { useState, useCallback } from 'react'
import { AttackMode, AttackStatus, api } from '@/lib/api'

interface Props {
  currentMode: AttackMode
  onModeChange?: (newStatus: AttackStatus) => void
}

const ATTACK_MODES: { id: AttackMode; label: string; color: string; hoverColor: string; description: string }[] = [
  { id: 'OFF', label: 'OFF', color: 'bg-gray-600', hoverColor: 'hover:bg-gray-700', description: 'Attack 비활성화' },
  { id: 'NORMAL', label: '일반', color: 'bg-blue-600', hoverColor: 'hover:bg-blue-700', description: '리스크 0.5%/트레이드' },
  { id: 'PLUS', label: '플러스', color: 'bg-orange-600', hoverColor: 'hover:bg-orange-700', description: '리스크 0.6%/트레이드' },
  { id: 'MAX', label: 'MAX', color: 'bg-red-600', hoverColor: 'hover:bg-red-700', description: '리스크 0.7%/트레이드' },
]

export default function AttackModeSelector({ currentMode, onModeChange }: Props) {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleModeChange = useCallback(async (mode: AttackMode) => {
    if (loading || currentMode === mode) return

    setLoading(true)
    setError(null)

    try {
      const result = await api.setAttackMode(mode)
      if (result.success && onModeChange) {
        onModeChange(result.status)
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : '모드 변경 실패')
    } finally {
      setLoading(false)
    }
  }, [loading, currentMode, onModeChange])

  return (
    <div className="flex flex-col gap-2">
      <div className="text-slate-400 text-xs mb-1">Attack 모드</div>
      <div className="flex gap-1">
        {ATTACK_MODES.map((mode) => (
          <button
            key={mode.id}
            onClick={() => handleModeChange(mode.id)}
            disabled={loading}
            title={mode.description}
            className={`
              px-3 py-1.5 rounded text-xs font-medium transition-all
              ${currentMode === mode.id
                ? `${mode.color} text-white ring-2 ring-white/50`
                : `bg-slate-700 text-slate-300 ${mode.hoverColor}`
              }
              ${loading ? 'opacity-50 cursor-wait' : 'cursor-pointer'}
              disabled:cursor-not-allowed
            `}
          >
            {mode.label}
          </button>
        ))}
      </div>
      {error && (
        <div className="text-red-400 text-xs">{error}</div>
      )}
    </div>
  )
}
