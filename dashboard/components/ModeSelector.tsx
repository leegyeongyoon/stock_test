'use client'

import { useState, useEffect, useCallback } from 'react'

interface UserModeStatus {
  requested_mode: string
  requested_mode_display: string
  effective_mode: string
  effective_mode_display: string
  downgrade_reason: string | null
  config: {
    max_position_pct: number
    max_concurrent_positions: number
    dd_halt_threshold: number
    daily_loss_limit: number
    correlation_warn: number
    correlation_action: number
    execute_trades: boolean
  }
  timestamp: string
}

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8086'

const MODES = [
  { id: 'SAFE', label: '보수', color: 'bg-green-600', hoverColor: 'hover:bg-green-700', description: '3% 포지션, 보수적 운용' },
  { id: 'BALANCED', label: '기본', color: 'bg-blue-600', hoverColor: 'hover:bg-blue-700', description: '5% 포지션, 균형 운용' },
  { id: 'AGGRESSIVE', label: '공격', color: 'bg-red-600', hoverColor: 'hover:bg-red-700', description: '8% 포지션, 공격적 운용' },
  { id: 'PAPER', label: '모의', color: 'bg-gray-600', hoverColor: 'hover:bg-gray-700', description: '실거래 없음' },
]

export default function ModeSelector() {
  const [status, setStatus] = useState<UserModeStatus | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const fetchStatus = useCallback(async () => {
    try {
      const response = await fetch(`${API_BASE_URL}/api/user/mode`)
      if (!response.ok) throw new Error('Failed to fetch mode')
      const data = await response.json()
      setStatus(data)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Unknown error')
    }
  }, [])

  useEffect(() => {
    fetchStatus()
    const interval = setInterval(fetchStatus, 5000)
    return () => clearInterval(interval)
  }, [fetchStatus])

  const handleModeChange = async (mode: string) => {
    if (loading || status?.requested_mode === mode) return

    setLoading(true)
    try {
      const response = await fetch(`${API_BASE_URL}/api/user/mode`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode }),
      })

      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(errorData.detail || 'Failed to change mode')
      }

      const data = await response.json()
      setStatus(data)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Unknown error')
    } finally {
      setLoading(false)
    }
  }

  const isDowngraded = status && status.requested_mode !== status.effective_mode

  return (
    <div className="flex flex-col gap-2">
      {/* Mode Buttons */}
      <div className="flex gap-1">
        {MODES.map((mode) => {
          const isRequested = status?.requested_mode === mode.id
          const isEffective = status?.effective_mode === mode.id

          return (
            <button
              key={mode.id}
              onClick={() => handleModeChange(mode.id)}
              disabled={loading}
              title={mode.description}
              className={`
                px-3 py-1.5 rounded text-xs font-medium transition-all
                ${isRequested
                  ? `${mode.color} text-white ring-2 ring-white/50`
                  : `bg-slate-700 text-slate-300 ${mode.hoverColor}`
                }
                ${isEffective && !isRequested ? 'ring-2 ring-yellow-500' : ''}
                ${loading ? 'opacity-50 cursor-wait' : 'cursor-pointer'}
                disabled:cursor-not-allowed
              `}
            >
              {mode.label}
            </button>
          )
        })}
      </div>

      {/* Downgrade Warning */}
      {isDowngraded && (
        <div className="flex items-center gap-1 text-yellow-400 text-xs">
          <span className="animate-pulse">⚠️</span>
          <span>
            {status.effective_mode_display}로 다운그레이드: {status.downgrade_reason}
          </span>
        </div>
      )}

      {/* Error Message */}
      {error && (
        <div className="text-red-400 text-xs">
          {error}
        </div>
      )}
    </div>
  )
}
