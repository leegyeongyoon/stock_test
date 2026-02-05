'use client'

import { useState, useEffect } from 'react'

interface DipScalperStatus {
  enabled: boolean
  settings: {
    min_drop_pct: string
    max_drop_pct: string
    atr_multiplier: number
    buy_amount_krw: number
    take_profit_pct: string
    stop_loss_pct: string
    time_stop_minutes: number
    max_positions: number
    // v2.0
    min_rvol?: number
    btc_crash_threshold?: string
    min_bid_ratio?: string
  }
  stats: {
    total_signals: number
    total_trades: number
    winning_trades: number
    losing_trades: number
    win_rate: string
    avg_pnl: string
    // v2.0 filter stats
    filtered_by_volume?: number
    filtered_by_btc?: number
    filtered_by_orderbook?: number
  }
  // v2.0 BTC status
  btc_status?: {
    change_1m: string
    stable: boolean
  }
  active_positions: number
  positions: Record<string, {
    entry_price: number
    quantity: number
    stop_loss: number
    take_profit: number
    highest_price: number
    pnl_pct: string
    hold_minutes: string
  }>
}

interface DipCandidate {
  symbol: string
  price: number
  change_1m: number
  change_1m_pct: string
  threshold: number
  threshold_pct: string
  proximity: number
  proximity_pct: string
  triggered: boolean
}

interface CandidatesResponse {
  candidates: DipCandidate[]
  total_scanned: number
}

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8086'

export default function DipScalperMonitor() {
  const [status, setStatus] = useState<DipScalperStatus | null>(null)
  const [candidates, setCandidates] = useState<DipCandidate[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const fetchData = async () => {
    try {
      const [statusRes, candidatesRes] = await Promise.all([
        fetch(`${API_BASE}/api/dip-scalper/status`),
        fetch(`${API_BASE}/api/dip-scalper/candidates?limit=5`),
      ])

      if (!statusRes.ok) throw new Error('Failed to fetch status')

      const statusData = await statusRes.json()
      setStatus(statusData)

      if (candidatesRes.ok) {
        const candidatesData: CandidatesResponse = await candidatesRes.json()
        setCandidates(candidatesData.candidates || [])
      }

      setError(null)
    } catch (e) {
      setError('Failed to load')
    } finally {
      setLoading(false)
    }
  }

  const toggleEnabled = async () => {
    if (!status) return
    try {
      const res = await fetch(`${API_BASE}/api/dip-scalper/enable?enabled=${!status.enabled}`, {
        method: 'POST'
      })
      if (res.ok) {
        fetchData()
      }
    } catch (e) {
      // ignore
    }
  }

  useEffect(() => {
    fetchData()
    const interval = setInterval(fetchData, 2000)
    return () => clearInterval(interval)
  }, [])

  if (loading) {
    return (
      <div className="bg-slate-800 rounded-lg p-4">
        <div className="animate-pulse h-48 bg-slate-700 rounded"></div>
      </div>
    )
  }

  if (error || !status) {
    return (
      <div className="bg-slate-800 rounded-lg p-4">
        <p className="text-red-400">{error || 'No data'}</p>
      </div>
    )
  }

  const positions = Object.entries(status.positions)
  const totalFiltered = (status.stats.filtered_by_volume || 0) +
                        (status.stats.filtered_by_btc || 0) +
                        (status.stats.filtered_by_orderbook || 0)

  return (
    <div className="bg-slate-800 rounded-lg p-4">
      {/* Header */}
      <div className="flex justify-between items-center mb-4">
        <div className="flex items-center gap-3">
          <h3 className="text-lg font-semibold">급락 스캘퍼 <span className="text-xs text-slate-500">v2.0</span></h3>
          <button
            onClick={toggleEnabled}
            className={`px-3 py-1 rounded text-sm font-medium transition-colors ${
              status.enabled
                ? 'bg-green-600 hover:bg-green-700'
                : 'bg-slate-600 hover:bg-slate-500'
            }`}
          >
            {status.enabled ? 'ON' : 'OFF'}
          </button>
        </div>
        <div className="flex items-center gap-3">
          {/* BTC Status Badge */}
          {status.btc_status && (
            <div className={`px-2 py-1 rounded text-xs font-medium ${
              status.btc_status.stable
                ? 'bg-green-900/50 text-green-400 border border-green-700/50'
                : 'bg-red-900/50 text-red-400 border border-red-700/50'
            }`}>
              BTC {status.btc_status.change_1m}
              {!status.btc_status.stable && ' (PAUSE)'}
            </div>
          )}
          <div className="text-sm text-slate-400">
            {status.active_positions}/{status.settings.max_positions}
          </div>
        </div>
      </div>

      {/* Settings Row */}
      <div className="flex flex-wrap gap-3 text-xs text-slate-400 mb-3 pb-3 border-b border-slate-700">
        <span>급락: {status.settings.min_drop_pct}~{status.settings.max_drop_pct}</span>
        <span>TP: <span className="text-green-400">{status.settings.take_profit_pct}</span></span>
        <span>SL: <span className="text-red-400">{status.settings.stop_loss_pct}</span></span>
        <span>타임스톱: {status.settings.time_stop_minutes}분</span>
        {/* v2.0 settings */}
        {status.settings.min_rvol && (
          <span>RVOL: <span className="text-yellow-400">{status.settings.min_rvol}x+</span></span>
        )}
        {status.settings.min_bid_ratio && (
          <span>매수벽: <span className="text-blue-400">{status.settings.min_bid_ratio}+</span></span>
        )}
      </div>

      {/* Stats Row */}
      <div className="grid grid-cols-5 gap-2 mb-4 text-center text-sm">
        <div>
          <div className="text-slate-500 text-xs">신호</div>
          <div className="font-bold">{status.stats.total_signals}</div>
        </div>
        <div>
          <div className="text-slate-500 text-xs">거래</div>
          <div className="font-bold">{status.stats.total_trades}</div>
        </div>
        <div>
          <div className="text-slate-500 text-xs">승률</div>
          <div className={`font-bold ${
            parseFloat(status.stats.win_rate) >= 50 ? 'text-green-400' : 'text-red-400'
          }`}>
            {status.stats.win_rate}
          </div>
        </div>
        <div>
          <div className="text-slate-500 text-xs">평균</div>
          <div className={`font-bold ${
            parseFloat(status.stats.avg_pnl) >= 0 ? 'text-green-400' : 'text-red-400'
          }`}>
            {status.stats.avg_pnl}
          </div>
        </div>
        <div>
          <div className="text-slate-500 text-xs">필터</div>
          <div className="font-bold text-orange-400">{totalFiltered}</div>
        </div>
      </div>

      {/* v2.0 Filter Details (collapsible) */}
      {totalFiltered > 0 && (
        <div className="flex gap-3 text-xs text-slate-500 mb-3 pb-3 border-b border-slate-700">
          {(status.stats.filtered_by_volume ?? 0) > 0 && (
            <span>거래량: <span className="text-orange-400">{status.stats.filtered_by_volume}</span></span>
          )}
          {(status.stats.filtered_by_btc ?? 0) > 0 && (
            <span>BTC: <span className="text-orange-400">{status.stats.filtered_by_btc}</span></span>
          )}
          {(status.stats.filtered_by_orderbook ?? 0) > 0 && (
            <span>호가창: <span className="text-orange-400">{status.stats.filtered_by_orderbook}</span></span>
          )}
        </div>
      )}

      {/* Active Positions */}
      {positions.length > 0 && (
        <div className="mb-4">
          <div className="text-sm text-slate-400 mb-2">활성 포지션</div>
          <div className="space-y-1">
            {positions.map(([symbol, pos]) => (
              <div key={symbol} className="bg-green-900/30 border border-green-700/50 rounded p-2 flex justify-between items-center">
                <div>
                  <span className="font-medium text-green-400">{symbol.replace('KRW-', '')}</span>
                  <span className="text-slate-400 text-xs ml-2">
                    {pos.hold_minutes}분
                  </span>
                </div>
                <div className="text-right">
                  <div className={`font-bold ${
                    parseFloat(pos.pnl_pct) >= 0 ? 'text-green-400' : 'text-red-400'
                  }`}>
                    {pos.pnl_pct}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Candidates */}
      <div>
        <div className="text-sm text-slate-400 mb-2">급락 후보 TOP 5</div>
        {candidates.length > 0 ? (
          <div className="space-y-1">
            {candidates.map((c) => (
              <div
                key={c.symbol}
                className={`rounded p-2 flex justify-between items-center text-sm ${
                  c.triggered
                    ? 'bg-red-900/50 border border-red-600'
                    : 'bg-slate-700/50'
                }`}
              >
                <div className="flex items-center gap-2">
                  <span className={`font-mono font-medium ${c.triggered ? 'text-red-400' : 'text-white'}`}>
                    {c.symbol.replace('KRW-', '')}
                  </span>
                  {c.triggered && (
                    <span className="text-xs bg-red-600 px-1 rounded">TRIGGERED</span>
                  )}
                </div>
                <div className="flex items-center gap-3">
                  <div className="text-right">
                    <div className={`font-bold ${
                      c.change_1m < 0 ? 'text-red-400' : 'text-green-400'
                    }`}>
                      {c.change_1m_pct}
                    </div>
                    <div className="text-xs text-slate-500">
                      기준: {c.threshold_pct}
                    </div>
                  </div>
                  {/* Progress bar showing proximity to threshold */}
                  <div className="w-16 h-2 bg-slate-600 rounded overflow-hidden">
                    <div
                      className={`h-full transition-all ${
                        c.proximity >= 1 ? 'bg-red-500' :
                        c.proximity >= 0.7 ? 'bg-yellow-500' : 'bg-slate-500'
                      }`}
                      style={{ width: `${Math.min(100, c.proximity * 100)}%` }}
                    />
                  </div>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="text-center text-slate-500 py-2 text-sm">
            급락 후보 없음
          </div>
        )}
      </div>
    </div>
  )
}
