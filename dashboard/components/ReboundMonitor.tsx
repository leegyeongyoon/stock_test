'use client'

import { useEffect, useState } from 'react'
import { api, ReboundMonitoringResponse, ReboundMode, ReboundPositionInfo, ReboundSignalInfo } from '@/lib/api'

interface Props {
  refreshInterval?: number  // ms
}

// 모드별 색상
function getModeColor(mode: string): string {
  switch (mode) {
    case 'AGGRESSIVE': return 'bg-red-600 text-white'
    case 'NORMAL': return 'bg-blue-600 text-white'
    case 'SAFE': return 'bg-green-600 text-white'
    default: return 'bg-slate-600 text-slate-300'
  }
}

// 점수별 배지 색상
function getScoreBadgeClass(score: number): string {
  if (score >= 85) return 'bg-green-600 text-white'
  if (score >= 70) return 'bg-yellow-500 text-black'
  if (score >= 55) return 'bg-orange-500 text-white'
  return 'bg-slate-600 text-slate-300'
}

// 포지션 상태 배지
function PositionStatusBadges({ pos }: { pos: ReboundPositionInfo }) {
  return (
    <div className="flex flex-wrap gap-1 mt-1">
      {pos.tp1_hit && (
        <span className="px-1.5 py-0.5 text-[10px] bg-green-700 text-white rounded">TP1</span>
      )}
      {pos.tp2_hit && (
        <span className="px-1.5 py-0.5 text-[10px] bg-blue-700 text-white rounded">TP2</span>
      )}
      {pos.be_stop_active && (
        <span className="px-1.5 py-0.5 text-[10px] bg-yellow-600 text-black rounded">BE</span>
      )}
      {pos.trailing_active && (
        <span className="px-1.5 py-0.5 text-[10px] bg-purple-600 text-white rounded">TRAIL</span>
      )}
    </div>
  )
}

// 포지션 카드
function PositionCard({ symbol, pos }: { symbol: string; pos: ReboundPositionInfo }) {
  const symbolCode = symbol.replace('KRW-', '')
  const pnlPct = pos.entry_price > 0
    ? ((pos.highest_price - pos.entry_price) / pos.entry_price * 100)
    : 0
  const isProfit = pnlPct >= 0

  return (
    <div className="flex items-center gap-3 p-3 bg-slate-800 rounded-lg border border-slate-700">
      {/* 심볼 */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="font-bold text-white text-sm">{symbolCode}</span>
          <span className={`text-xs ${isProfit ? 'text-green-400' : 'text-red-400'}`}>
            {isProfit ? '+' : ''}{pnlPct.toFixed(2)}%
          </span>
        </div>
        <div className="text-xs text-slate-500 mt-0.5">
          진입: ₩{pos.entry_price.toLocaleString()} | 최고: ₩{pos.highest_price.toLocaleString()}
        </div>
        <PositionStatusBadges pos={pos} />
      </div>

      {/* 보유 시간 */}
      <div className="text-right text-xs text-slate-400">
        {pos.hold_time_min.toFixed(0)}분
      </div>
    </div>
  )
}

// 대기 시그널 카드
function SignalCard({ symbol, signal }: { symbol: string; signal: ReboundSignalInfo }) {
  const symbolCode = symbol.replace('KRW-', '')

  return (
    <div className="flex items-center gap-3 p-2 bg-slate-800/50 rounded border border-slate-700/50">
      <span className="font-medium text-white text-sm">{symbolCode}</span>
      <span className={`px-1.5 py-0.5 rounded text-xs font-bold ${getScoreBadgeClass(signal.score)}`}>
        {signal.score.toFixed(0)}점
      </span>
      <span className="text-xs text-green-400">L{signal.level}</span>
    </div>
  )
}

// 통계 카드
function StatsCard({ stats }: { stats: ReboundMonitoringResponse['stats'] }) {
  return (
    <div className="bg-slate-800/50 rounded-lg p-3 border border-slate-700">
      <div className="text-xs text-slate-400 mb-2">거래 통계</div>
      <div className="grid grid-cols-2 gap-2 text-xs">
        <div className="flex justify-between">
          <span className="text-slate-500">총 거래</span>
          <span className="text-slate-300">{stats.total_trades}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-slate-500">승률</span>
          <span className={`font-medium ${stats.win_rate >= 50 ? 'text-green-400' : 'text-red-400'}`}>
            {stats.win_rate.toFixed(1)}%
          </span>
        </div>
        <div className="flex justify-between">
          <span className="text-slate-500">TP1 횟수</span>
          <span className="text-green-400">{stats.tp1_hits}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-slate-500">TP2 횟수</span>
          <span className="text-blue-400">{stats.tp2_hits}</span>
        </div>
        <div className="flex justify-between col-span-2">
          <span className="text-slate-500">손절 횟수</span>
          <span className="text-red-400">{stats.stop_loss_hits}</span>
        </div>
      </div>
    </div>
  )
}

// 필터 상태 카드
function FilterStatusCard({ filters }: { filters: ReboundMonitoringResponse['filters'] }) {
  const cooldownCount = Object.values(filters.cooldowns).filter(c => c.is_in_cooldown).length

  return (
    <div className="bg-slate-800/50 rounded-lg p-3 border border-slate-700">
      <div className="text-xs text-slate-400 mb-2">필터 상태</div>
      <div className="grid grid-cols-2 gap-2 text-xs">
        <div className="flex justify-between">
          <span className="text-slate-500">쿨다운 중</span>
          <span className={cooldownCount > 0 ? 'text-orange-400' : 'text-slate-300'}>
            {cooldownCount}개
          </span>
        </div>
        <div className="flex justify-between">
          <span className="text-slate-500">연속손실 제한</span>
          <span className="text-slate-300">{filters.filter_settings.max_consecutive_losses}회</span>
        </div>
        <div className="flex justify-between">
          <span className="text-slate-500">변동성 필터</span>
          <span className={filters.filter_settings.disable_on_volatile ? 'text-green-400' : 'text-slate-500'}>
            {filters.filter_settings.disable_on_volatile ? 'ON' : 'OFF'}
          </span>
        </div>
        <div className="flex justify-between">
          <span className="text-slate-500">추세 필터</span>
          <span className={filters.filter_settings.disable_in_downtrend ? 'text-green-400' : 'text-slate-500'}>
            {filters.filter_settings.disable_in_downtrend ? 'ON' : 'OFF'}
          </span>
        </div>
      </div>
    </div>
  )
}

// 모드 선택 버튼
function ModeSelector({
  currentMode,
  onModeChange,
  disabled
}: {
  currentMode: string
  onModeChange: (mode: ReboundMode) => void
  disabled: boolean
}) {
  const modes: ReboundMode[] = ['OFF', 'SAFE', 'NORMAL', 'AGGRESSIVE']

  return (
    <div className="flex gap-1">
      {modes.map((mode) => (
        <button
          key={mode}
          onClick={() => onModeChange(mode)}
          disabled={disabled || mode === currentMode}
          className={`px-2 py-1 text-xs rounded transition-colors ${
            mode === currentMode
              ? getModeColor(mode)
              : 'bg-slate-700 text-slate-400 hover:bg-slate-600'
          } ${disabled ? 'opacity-50 cursor-not-allowed' : ''}`}
        >
          {mode}
        </button>
      ))}
    </div>
  )
}

export default function ReboundMonitor({ refreshInterval = 3000 }: Props) {
  const [data, setData] = useState<ReboundMonitoringResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [changingMode, setChangingMode] = useState(false)

  const fetchData = async () => {
    try {
      const response = await api.getReboundStatus()
      setData(response)
      setError(null)
    } catch (err) {
      setError('Rebound 모니터링 데이터를 불러올 수 없습니다')
      console.error('Rebound monitoring fetch error:', err)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchData()
    const interval = setInterval(fetchData, refreshInterval)
    return () => clearInterval(interval)
  }, [refreshInterval])

  const handleModeChange = async (mode: ReboundMode) => {
    setChangingMode(true)
    try {
      await api.setReboundMode(mode)
      await fetchData()  // 즉시 새로고침
    } catch (err) {
      console.error('Mode change error:', err)
    } finally {
      setChangingMode(false)
    }
  }

  if (loading) {
    return (
      <div className="bg-slate-900 rounded-xl p-4 border border-slate-800">
        <h2 className="text-base font-semibold text-white mb-3 flex items-center gap-2">
          <span className="text-lg">&#127919;</span>
          Rebound Scalper
        </h2>
        <div className="space-y-2">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-14 bg-slate-800 rounded-lg animate-pulse" />
          ))}
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="bg-slate-900 rounded-xl p-4 border border-slate-800">
        <h2 className="text-base font-semibold text-white mb-3 flex items-center gap-2">
          <span className="text-lg">&#127919;</span>
          Rebound Scalper
        </h2>
        <div className="text-center py-4 text-red-400 text-sm">{error}</div>
      </div>
    )
  }

  const positions = data?.positions || {}
  const signals = data?.pending_signals || {}
  const stats = data?.stats || { total_trades: 0, win_rate: 0, tp1_hits: 0, tp2_hits: 0, stop_loss_hits: 0 }
  const filters = data?.filters || {
    cooldowns: {},
    filter_settings: {
      max_consecutive_losses: 3,
      cooldown_minutes: 120,
      disable_on_volatile: true,
      disable_in_downtrend: true,
    }
  }

  const positionCount = Object.keys(positions).length
  const signalCount = Object.keys(signals).length

  return (
    <div className="bg-slate-900 rounded-xl p-4 border border-slate-800">
      {/* 헤더 */}
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-base font-semibold text-white flex items-center gap-2">
          <span className="text-lg">&#128640;</span>
          Rebound Scalper
          <span className={`ml-2 px-2 py-0.5 text-xs rounded ${getModeColor(data?.mode || 'OFF')}`}>
            {data?.mode || 'OFF'}
          </span>
        </h2>
        <div className="flex items-center gap-2">
          <span className={`w-2 h-2 rounded-full ${data?.enabled ? 'bg-green-500' : 'bg-slate-500'}`} />
          <span className="text-xs text-slate-400">{data?.enabled ? '활성' : '비활성'}</span>
        </div>
      </div>

      {/* 모드 선택 */}
      <div className="mb-4">
        <ModeSelector
          currentMode={data?.mode || 'OFF'}
          onModeChange={handleModeChange}
          disabled={changingMode}
        />
      </div>

      {/* 활성 포지션 */}
      <div className="mb-4">
        <div className="text-xs text-slate-400 mb-2">활성 포지션 ({positionCount})</div>
        {positionCount === 0 ? (
          <div className="text-center py-3 text-slate-500 text-sm bg-slate-800/30 rounded">
            보유 포지션 없음
          </div>
        ) : (
          <div className="space-y-2">
            {Object.entries(positions).map(([symbol, pos]) => (
              <PositionCard key={symbol} symbol={symbol} pos={pos} />
            ))}
          </div>
        )}
      </div>

      {/* 대기 시그널 */}
      {signalCount > 0 && (
        <div className="mb-4">
          <div className="text-xs text-slate-400 mb-2">대기 시그널 ({signalCount})</div>
          <div className="flex flex-wrap gap-2">
            {Object.entries(signals).map(([symbol, signal]) => (
              <SignalCard key={symbol} symbol={symbol} signal={signal} />
            ))}
          </div>
        </div>
      )}

      {/* 통계 및 필터 */}
      <div className="grid grid-cols-2 gap-2">
        <StatsCard stats={stats} />
        <FilterStatusCard filters={filters} />
      </div>
    </div>
  )
}
