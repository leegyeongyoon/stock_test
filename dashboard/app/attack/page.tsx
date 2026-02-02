'use client'

import { useEffect, useState, useCallback } from 'react'
import { api, AttackStatus, AttackPosition, AttackSignal, AttackScore } from '@/lib/api'
import ConnectionStatus from '@/components/ConnectionStatus'
import {
  AttackStatusCard,
  AttackModeSelector,
  AttackScoreDisplay,
  AttackPositionsTable,
} from '@/components/attack'

export default function AttackPage() {
  const [status, setStatus] = useState<AttackStatus | null>(null)
  const [positions, setPositions] = useState<AttackPosition[]>([])
  const [signals, setSignals] = useState<AttackSignal[]>([])
  const [selectedSymbol, setSelectedSymbol] = useState<string>('')
  const [selectedScore, setSelectedScore] = useState<AttackScore | null>(null)
  const [scoreLoading, setScoreLoading] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const fetchData = useCallback(async () => {
    try {
      const [statusData, posData] = await Promise.all([
        api.getAttackStatus(),
        api.getAttackPositions(),
      ])
      setStatus(statusData)
      setPositions(posData.active_positions)
      setSignals(posData.pending_signals)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Attack 데이터 조회 실패')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchData()
    const interval = setInterval(fetchData, 3000)
    return () => clearInterval(interval)
  }, [fetchData])

  const handleSymbolLookup = async () => {
    if (!selectedSymbol.trim()) return

    setScoreLoading(true)
    try {
      const score = await api.getAttackScore(selectedSymbol.trim())
      setSelectedScore(score)
    } catch (e) {
      setSelectedScore(null)
      alert(e instanceof Error ? e.message : 'Score 조회 실패')
    } finally {
      setScoreLoading(false)
    }
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-2xl font-bold">Attack 전략</h1>
          <p className="text-slate-400 text-sm">급등 감지 및 공격적 진입 전략</p>
        </div>
        <div className="flex items-center gap-6">
          {status && (
            <AttackModeSelector
              currentMode={status.mode}
              onModeChange={(newStatus) => setStatus(newStatus)}
            />
          )}
          <ConnectionStatus />
        </div>
      </div>

      {error && (
        <div className="bg-red-900/50 border border-red-700 text-red-300 px-4 py-3 rounded">
          ⚠️ {error}
        </div>
      )}

      {/* Status Cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <AttackStatusCard status={status} loading={loading} />

        {/* Gate Status Detail */}
        <div className="bg-slate-800 rounded-lg border border-slate-700 p-4">
          <h3 className="text-slate-400 text-sm mb-3">Gate 상태</h3>
          {status && (
            <div className={`text-lg font-bold ${status.gate_status.can_enter ? 'text-green-400' : 'text-red-400'}`}>
              {status.gate_status.can_enter ? '✅ 진입 가능' : '❌ 진입 불가'}
            </div>
          )}
          {status?.gate_status.reason && (
            <p className="text-sm text-slate-400 mt-2">{status.gate_status.reason}</p>
          )}
        </div>

        {/* Stats */}
        <div className="bg-slate-800 rounded-lg border border-slate-700 p-4">
          <h3 className="text-slate-400 text-sm mb-3">오늘 통계</h3>
          <div className="space-y-2">
            <div className="flex justify-between">
              <span className="text-slate-400">거래 수</span>
              <span className="font-medium">{status?.stats.today_trades || 0}건</span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-400">손익</span>
              <span className={`font-medium ${(status?.stats.today_pnl || 0) >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                {(status?.stats.today_pnl || 0) >= 0 ? '+' : ''}₩{(status?.stats.today_pnl || 0).toLocaleString()}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-400">승률</span>
              <span className="font-medium">{status?.stats.win_rate ? `${(status.stats.win_rate * 100).toFixed(1)}%` : '-'}</span>
            </div>
          </div>
        </div>
      </div>

      {/* Main Content */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Positions Table - 2/3 */}
        <div className="lg:col-span-2">
          <AttackPositionsTable
            positions={positions}
            signals={signals}
            loading={loading}
          />
        </div>

        {/* Score Display - 1/3 */}
        <div className="space-y-4">
          {/* Symbol Quick Lookup */}
          <div className="bg-slate-800 rounded-lg border border-slate-700 p-4">
            <h4 className="text-sm font-medium mb-3">Attack Score 조회</h4>
            <div className="flex gap-2">
              <input
                type="text"
                placeholder="KRW-BTC"
                value={selectedSymbol}
                onChange={(e) => setSelectedSymbol(e.target.value.toUpperCase())}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    handleSymbolLookup()
                  }
                }}
                className="flex-1 bg-slate-700 border border-slate-600 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-orange-500"
              />
              <button
                onClick={handleSymbolLookup}
                disabled={scoreLoading}
                className="px-4 py-2 bg-orange-600 hover:bg-orange-700 disabled:opacity-50 rounded text-sm font-medium transition-colors"
              >
                {scoreLoading ? '...' : '조회'}
              </button>
            </div>
          </div>

          {/* Score Display */}
          {selectedScore && (
            <AttackScoreDisplay score={selectedScore} loading={scoreLoading} />
          )}

          {/* Quick Links */}
          <div className="bg-slate-800 rounded-lg border border-slate-700 p-4">
            <h4 className="text-sm font-medium mb-3">빠른 조회</h4>
            <div className="flex flex-wrap gap-2">
              {['KRW-BTC', 'KRW-ETH', 'KRW-XRP', 'KRW-SOL', 'KRW-DOGE'].map((sym) => (
                <button
                  key={sym}
                  onClick={() => {
                    setSelectedSymbol(sym)
                    api.getAttackScore(sym).then(setSelectedScore).catch(() => setSelectedScore(null))
                  }}
                  className="px-2 py-1 bg-slate-700 hover:bg-slate-600 rounded text-xs font-mono transition-colors"
                >
                  {sym.replace('KRW-', '')}
                </button>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
