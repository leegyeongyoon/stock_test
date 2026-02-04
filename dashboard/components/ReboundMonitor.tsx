'use client'

import { useEffect, useState } from 'react'
import { api, ReboundMonitoringResponse, ReboundMode, ReboundPositionInfo, ReboundSignalInfo, ReboundCandidate } from '@/lib/api'

interface Props {
  refreshInterval?: number
}

// 모드별 색상 및 한글
function getModeInfo(mode: string): { color: string; label: string } {
  switch (mode) {
    case 'AGGRESSIVE': return { color: 'bg-red-600 text-white', label: '공격' }
    case 'NORMAL': return { color: 'bg-blue-600 text-white', label: '일반' }
    case 'SAFE': return { color: 'bg-green-600 text-white', label: '안전' }
    default: return { color: 'bg-slate-600 text-slate-300', label: '꺼짐' }
  }
}

// 컴포넌트 이름 한글화
function getKoreanName(name: string): string {
  const nameMap: Record<string, string> = {
    'Support Proximity': '지지선 근접도',
    'RSI Oversold': 'RSI 과매도',
    'Orderbook Imbalance': '호가 불균형',
    'Reversal Pattern': '반등 패턴',
    'Volatility Score': '변동성 점수',
  }
  return nameMap[name] || name
}

// 점수 설명 생성
function getScoreExplanation(name: string, details?: Record<string, unknown>): string {
  if (!details) return ''

  switch (name) {
    case 'Support Proximity': {
      const vwapDist = ((details.vwap_distance as number || 0) * 100).toFixed(2)
      const sma20Dist = ((details.sma20_distance as number || 0) * 100).toFixed(2)
      const bbLowerDist = ((details.bb_lower_distance as number || 0) * 100).toFixed(2)
      return `VWAP: ${vwapDist}% | SMA20: ${sma20Dist}% | BB하단: ${bbLowerDist}%`
    }
    case 'RSI Oversold': {
      const rsi = (details.rsi as number || 50).toFixed(0)
      const zone = details.zone as string || 'NEUTRAL'
      const zoneKr = zone === 'OVERSOLD' ? '과매도' : zone === 'CONFIRM' ? '반등확인' : '중립'
      return `RSI: ${rsi} (${zoneKr})`
    }
    case 'Orderbook Imbalance': {
      const ratio = (details.bid_ask_ratio as number || 1).toFixed(2)
      return `매수/매도 비율: ${ratio}x`
    }
    case 'Reversal Pattern': {
      const candle1m = details.candle_1m as string || 'FLAT'
      const momentum5m = details.momentum_5m as string || 'NEUTRAL'
      const candleKr = candle1m.includes('GREEN') ? '양봉' : candle1m === 'FLAT' ? '보합' : '음봉'
      const momentumKr = momentum5m.includes('REVERSAL') ? '반등중' : momentum5m === 'TURNING' ? '전환중' : '하락중'
      return `1분봉: ${candleKr} | 5분추세: ${momentumKr}`
    }
    case 'Volatility Score': {
      const atrZone = details.atr_zone as string || 'NORMAL'
      const rangeZone = details.range_zone as string || 'NORMAL'
      const atrKr = atrZone === 'OPTIMAL' ? '적정' : atrZone === 'HIGH' ? '높음' : '낮음'
      const rangeKr = rangeZone === 'OPTIMAL' ? '적정' : rangeZone === 'HIGH' ? '높음' : '낮음'
      return `ATR: ${atrKr} | 변동폭: ${rangeKr}`
    }
    default:
      return ''
  }
}

// 점수별 배지 색상
function getScoreBgClass(score: number): string {
  if (score >= 70) return 'bg-green-600 text-white'
  if (score >= 55) return 'bg-yellow-500 text-black'
  if (score >= 40) return 'bg-orange-500 text-white'
  return 'bg-slate-600 text-slate-300'
}

// 포지션 상태 배지
function PositionStatusBadges({ pos }: { pos: ReboundPositionInfo }) {
  return (
    <div className="flex flex-wrap gap-1 mt-1">
      {pos.tp1_hit && (
        <span className="px-1.5 py-0.5 text-[10px] bg-green-700 text-white rounded">1차익절</span>
      )}
      {pos.tp2_hit && (
        <span className="px-1.5 py-0.5 text-[10px] bg-blue-700 text-white rounded">2차익절</span>
      )}
      {pos.be_stop_active && (
        <span className="px-1.5 py-0.5 text-[10px] bg-yellow-600 text-black rounded">본전스탑</span>
      )}
      {pos.trailing_active && (
        <span className="px-1.5 py-0.5 text-[10px] bg-purple-600 text-white rounded">추적스탑</span>
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
      <div className="text-right text-xs text-slate-400">
        {pos.hold_time_min.toFixed(0)}분
      </div>
    </div>
  )
}

// 후보 종목 카드 (상세 항상 표시)
function CandidateCard({ candidate, rank }: { candidate: ReboundCandidate; rank: number }) {
  const symbolCode = candidate.symbol.replace('KRW-', '')
  const canEntry = candidate.level > 0
  const changeRatePct = (candidate.change_rate * 100).toFixed(1)
  const isPositive = candidate.change_rate > 0

  return (
    <div className={`bg-slate-800/80 rounded-lg border ${canEntry ? 'border-green-600' : 'border-slate-700'} overflow-hidden`}>
      {/* 헤더 */}
      <div className="p-2 flex items-center gap-2">
        <span className="w-5 h-5 rounded-full bg-slate-600 flex items-center justify-center text-[10px] font-bold text-white">
          {rank}
        </span>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1">
            <span className="font-bold text-white text-xs">{symbolCode}</span>
            <span className={`text-[10px] ${isPositive ? 'text-green-400' : 'text-red-400'}`}>
              {isPositive ? '+' : ''}{changeRatePct}%
            </span>
          </div>
        </div>
        <div className="text-right">
          <span className={`px-1.5 py-0.5 rounded text-xs font-bold ${getScoreBgClass(candidate.score)}`}>
            {candidate.score.toFixed(0)}점
          </span>
          {canEntry ? (
            <div className="text-[10px] text-green-400 mt-0.5">L{candidate.level} 가능</div>
          ) : (
            <div className="text-[10px] text-slate-500 mt-0.5">{candidate.distance_to_entry.toFixed(0)}점 부족</div>
          )}
        </div>
      </div>

      {/* 상세 (항상 표시) */}
      <div className="px-2 pb-2 space-y-1">
        {candidate.components.map((comp, idx) => {
          const pct = comp.max_score > 0 ? (comp.score / comp.max_score) * 100 : 0
          const isFull = pct >= 80
          const isHalf = pct >= 50 && pct < 80

          return (
            <div key={idx}>
              <div className="flex items-center justify-between text-[10px] mb-0.5">
                <span className="text-slate-400">{getKoreanName(comp.name)}</span>
                <span className={`font-medium ${isFull ? 'text-green-400' : isHalf ? 'text-yellow-400' : 'text-slate-500'}`}>
                  {comp.score.toFixed(0)}/{comp.max_score}
                </span>
              </div>
              <div className="h-1 bg-slate-700 rounded-full overflow-hidden mb-0.5">
                <div
                  className={`h-full rounded-full transition-all ${
                    isFull ? 'bg-green-500' : isHalf ? 'bg-yellow-500' : 'bg-slate-500'
                  }`}
                  style={{ width: `${Math.min(pct, 100)}%` }}
                />
              </div>
              <div className="text-[9px] text-slate-500">
                {getScoreExplanation(comp.name, comp.details)}
              </div>
            </div>
          )
        })}
      </div>
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
          <span className="text-slate-500">1차익절</span>
          <span className="text-green-400">{stats.tp1_hits}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-slate-500">2차익절</span>
          <span className="text-blue-400">{stats.tp2_hits}</span>
        </div>
        <div className="flex justify-between col-span-2">
          <span className="text-slate-500">손절</span>
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
          <span className="text-slate-500">쿨다운</span>
          <span className={cooldownCount > 0 ? 'text-orange-400' : 'text-slate-300'}>
            {cooldownCount}개
          </span>
        </div>
        <div className="flex justify-between">
          <span className="text-slate-500">연속손실</span>
          <span className="text-slate-300">{filters.filter_settings.max_consecutive_losses}회</span>
        </div>
        <div className="flex justify-between">
          <span className="text-slate-500">변동성</span>
          <span className={filters.filter_settings.disable_on_volatile ? 'text-green-400' : 'text-slate-500'}>
            {filters.filter_settings.disable_on_volatile ? 'ON' : 'OFF'}
          </span>
        </div>
        <div className="flex justify-between">
          <span className="text-slate-500">추세</span>
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
  const modes: { mode: ReboundMode; label: string }[] = [
    { mode: 'OFF', label: '꺼짐' },
    { mode: 'SAFE', label: '안전' },
    { mode: 'NORMAL', label: '일반' },
    { mode: 'AGGRESSIVE', label: '공격' },
  ]

  return (
    <div className="flex gap-1">
      {modes.map(({ mode, label }) => {
        const { color } = getModeInfo(mode)
        return (
          <button
            key={mode}
            onClick={() => onModeChange(mode)}
            disabled={disabled || mode === currentMode}
            className={`px-2 py-1 text-xs rounded transition-colors ${
              mode === currentMode
                ? color
                : 'bg-slate-700 text-slate-400 hover:bg-slate-600'
            } ${disabled ? 'opacity-50 cursor-not-allowed' : ''}`}
          >
            {label}
          </button>
        )
      })}
    </div>
  )
}

export default function ReboundMonitor({ refreshInterval = 3000 }: Props) {
  const [data, setData] = useState<ReboundMonitoringResponse | null>(null)
  const [candidates, setCandidates] = useState<ReboundCandidate[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [changingMode, setChangingMode] = useState(false)

  const fetchData = async () => {
    try {
      const [statusResponse, candidatesResponse] = await Promise.all([
        api.getReboundStatus(),
        api.getReboundCandidates(5, 0),
      ])
      setData(statusResponse)
      setCandidates(candidatesResponse.candidates)
      setError(null)
    } catch (err) {
      setError('Rebound 데이터를 불러올 수 없습니다')
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
      await fetchData()
    } catch (err) {
      console.error('Mode change error:', err)
    } finally {
      setChangingMode(false)
    }
  }

  if (loading) {
    return (
      <div className="bg-slate-900 rounded-xl p-4 border border-slate-800">
        <h2 className="text-base font-semibold text-white mb-3">🎯 반등 스캘퍼</h2>
        <div className="space-y-2">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-24 bg-slate-800 rounded-lg animate-pulse" />
          ))}
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="bg-slate-900 rounded-xl p-4 border border-slate-800">
        <h2 className="text-base font-semibold text-white mb-3">🎯 반등 스캘퍼</h2>
        <div className="text-center py-4 text-red-400 text-sm">{error}</div>
      </div>
    )
  }

  const positions = data?.positions || {}
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
  const { color: modeColor, label: modeLabel } = getModeInfo(data?.mode || 'OFF')

  return (
    <div className="bg-slate-900 rounded-xl p-4 border border-slate-800">
      {/* 헤더 */}
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-base font-semibold text-white flex items-center gap-2">
          🎯 반등 스캘퍼
          <span className={`ml-1 px-2 py-0.5 text-xs rounded ${modeColor}`}>
            {modeLabel}
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

      {/* Top 5 후보 종목 (상세 표시) */}
      <div className="mb-4">
        <div className="text-xs text-slate-400 mb-2">반등 후보 Top 5 (진입: 55점+)</div>
        {candidates.length > 0 ? (
          <div className="space-y-2">
            {candidates.map((c, idx) => (
              <CandidateCard key={c.symbol} candidate={c} rank={idx + 1} />
            ))}
          </div>
        ) : (
          <div className="text-center py-3 text-slate-500 text-xs bg-slate-800/30 rounded">
            후보 데이터 대기 중...
          </div>
        )}
      </div>

      {/* 활성 포지션 */}
      {positionCount > 0 && (
        <div className="mb-4">
          <div className="text-xs text-slate-400 mb-2">보유 포지션 ({positionCount})</div>
          <div className="space-y-2">
            {Object.entries(positions).map(([symbol, pos]) => (
              <PositionCard key={symbol} symbol={symbol} pos={pos} />
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
