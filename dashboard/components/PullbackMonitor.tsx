'use client'

import { useEffect, useState } from 'react'
import { api, PullbackMonitoringResponse, PullbackMode, PullbackPositionInfo, PullbackCandidate } from '@/lib/api'

interface Props {
  refreshInterval?: number
}

function getModeInfo(mode: string): { color: string; label: string } {
  switch (mode) {
    case 'AGGRESSIVE': return { color: 'bg-red-600 text-white', label: '공격' }
    case 'NORMAL': return { color: 'bg-blue-600 text-white', label: '일반' }
    case 'SAFE': return { color: 'bg-green-600 text-white', label: '안전' }
    default: return { color: 'bg-slate-600 text-slate-300', label: '꺼짐' }
  }
}

function getComponentKoreanName(name: string): string {
  const nameMap: Record<string, string> = {
    'Recent Surge': '최근 급등',
    'Pullback Depth': '눌림 깊이',
    'Support Level': '지지선 근접',
    'Accumulation Signal': '축적 신호',
    'Reversal Sign': '반등 조짐',
  }
  return nameMap[name] || name
}

function getScoreBgClass(score: number): string {
  if (score >= 85) return 'bg-green-600 text-white'
  if (score >= 70) return 'bg-yellow-500 text-black'
  if (score >= 55) return 'bg-orange-500 text-white'
  return 'bg-slate-600 text-slate-300'
}

function PositionCard({ symbol, pos }: { symbol: string; pos: PullbackPositionInfo }) {
  const symbolCode = symbol.replace('KRW-', '')
  const isProfit = pos.pnl_pct >= 0
  const refPrice = pos.is_averaged_down ? pos.avg_down_price : pos.entry_price

  return (
    <div className="flex items-center gap-3 p-3 bg-slate-800 rounded-lg border border-slate-700">
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="font-bold text-white text-sm">{symbolCode}</span>
          <span className={`text-xs ${isProfit ? 'text-green-400' : 'text-red-400'}`}>
            {isProfit ? '+' : ''}{pos.pnl_pct.toFixed(2)}%
          </span>
          {pos.is_averaged_down && (
            <span className="px-1 py-0.5 text-[9px] bg-yellow-600 text-black rounded font-bold">물타기</span>
          )}
        </div>
        <div className="text-xs text-slate-500 mt-0.5">
          {pos.is_averaged_down ? (
            <>원가: ₩{pos.entry_price.toLocaleString()} → 평균: ₩{pos.avg_down_price.toLocaleString()}</>
          ) : (
            <>진입: ₩{pos.entry_price.toLocaleString()}</>
          )}
        </div>
        <div className="flex flex-wrap gap-1 mt-1">
          <span className="px-1.5 py-0.5 text-[10px] bg-red-700 text-white rounded">
            SL ₩{pos.stop_loss.toLocaleString()}
          </span>
          {pos.is_averaged_down ? (
            <span className="px-1.5 py-0.5 text-[10px] bg-yellow-600 text-black rounded">
              본전+₩{Math.round(refPrice * 1.0015).toLocaleString()}
            </span>
          ) : (
            <span className="px-1.5 py-0.5 text-[10px] bg-green-700 text-white rounded">
              TP ₩{pos.take_profit.toLocaleString()}
            </span>
          )}
          {pos.trailing_active && (
            <span className="px-1.5 py-0.5 text-[10px] bg-purple-600 text-white rounded">추적스탑</span>
          )}
        </div>
      </div>
      <div className="text-right text-xs text-slate-400">
        {pos.hold_time_min.toFixed(0)}분
      </div>
    </div>
  )
}

function CandidateCard({ candidate, rank }: { candidate: PullbackCandidate; rank: number }) {
  const symbolCode = candidate.symbol.replace('KRW-', '')
  const canEntry = candidate.level > 0
  const changeRatePct = (candidate.change_rate * 100).toFixed(1)
  const isPositive = candidate.change_rate > 0

  const rankBg = rank === 1 ? 'bg-gradient-to-br from-yellow-500 to-yellow-700' :
                 rank === 2 ? 'bg-gradient-to-br from-slate-300 to-slate-500' :
                 rank === 3 ? 'bg-gradient-to-br from-amber-600 to-amber-800' :
                 'bg-slate-600'

  return (
    <div className={`bg-slate-800 rounded-lg border ${canEntry ? 'border-green-600' : 'border-slate-700'} overflow-hidden min-w-[260px] max-w-[300px]`}>
      <div className="p-3 bg-slate-800/80">
        <div className="flex items-center gap-3">
          <span className={`${rankBg} w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold text-white shrink-0`}>
            {rank}
          </span>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <span className="font-bold text-white text-sm">{symbolCode}</span>
              {candidate.korean_name && (
                <span className="text-xs text-slate-400">{candidate.korean_name}</span>
              )}
            </div>
            <div className="flex items-center gap-2 mt-0.5">
              <span className={`text-xs ${isPositive ? 'text-green-400' : 'text-red-400'}`}>
                {isPositive ? '+' : ''}{changeRatePct}%
              </span>
            </div>
          </div>
          <div className="text-right">
            <span className={`px-2 py-1 rounded text-sm font-bold ${getScoreBgClass(candidate.score)}`}>
              {candidate.score.toFixed(0)}점
            </span>
            {canEntry ? (
              <div className="text-xs text-green-400 mt-1">L{candidate.level} 진입가능</div>
            ) : (
              <div className="text-xs text-slate-500 mt-1">{candidate.distance_to_entry.toFixed(0)}점 부족</div>
            )}
          </div>
        </div>
      </div>

      <div className="px-3 pb-3 pt-2 space-y-1.5 bg-slate-850">
        {candidate.components.map((comp, idx) => {
          const pct = comp.max_score > 0 ? (comp.score / comp.max_score) * 100 : 0
          const isFull = pct >= 80
          const isHalf = pct >= 50 && pct < 80

          return (
            <div key={idx}>
              <div className="flex items-center justify-between text-xs mb-0.5">
                <span className="text-slate-400">{getComponentKoreanName(comp.name)}</span>
                <span className={`font-medium ${isFull ? 'text-green-400' : isHalf ? 'text-yellow-400' : 'text-slate-500'}`}>
                  {comp.score.toFixed(0)}/{comp.max_score}
                </span>
              </div>
              <div className="h-1 bg-slate-700 rounded-full overflow-hidden">
                <div
                  className={`h-full rounded-full transition-all ${
                    isFull ? 'bg-green-500' : isHalf ? 'bg-yellow-500' : 'bg-slate-500'
                  }`}
                  style={{ width: `${Math.min(pct, 100)}%` }}
                />
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function StatsCard({ stats }: { stats: PullbackMonitoringResponse['stats'] }) {
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
          <span className="text-slate-500">익절</span>
          <span className="text-green-400">{stats.take_profit_hits}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-slate-500">트레일링</span>
          <span className="text-blue-400">{stats.trailing_hits}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-slate-500">손절</span>
          <span className="text-red-400">{stats.stop_loss_hits}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-slate-500">본전청산</span>
          <span className="text-yellow-400">{stats.breakeven_hits}</span>
        </div>
      </div>
    </div>
  )
}

function SettingsCard({ settings }: { settings: PullbackMonitoringResponse['settings'] }) {
  return (
    <div className="bg-slate-800/50 rounded-lg p-3 border border-slate-700">
      <div className="text-xs text-slate-400 mb-2">청산 설정</div>
      <div className="grid grid-cols-2 gap-2 text-xs">
        <div className="flex justify-between">
          <span className="text-slate-500">손절</span>
          <span className="text-red-400">{(settings.stop_loss_pct * 100).toFixed(1)}%</span>
        </div>
        <div className="flex justify-between">
          <span className="text-slate-500">1차 익절</span>
          <span className="text-green-400">+{(settings.take_profit_1_pct * 100).toFixed(1)}%</span>
        </div>
        <div className="flex justify-between">
          <span className="text-slate-500">트레일링 트리거</span>
          <span className="text-blue-400">+{(settings.trailing_trigger_pct * 100).toFixed(1)}%</span>
        </div>
        <div className="flex justify-between">
          <span className="text-slate-500">트레일링 스탑</span>
          <span className="text-purple-400">고점-{(settings.trailing_stop_pct * 100).toFixed(1)}%</span>
        </div>
        <div className="flex justify-between col-span-2">
          <span className="text-slate-500">타임스톱</span>
          <span className="text-orange-400">{settings.time_stop_hours}시간</span>
        </div>
      </div>
    </div>
  )
}

function ModeSelectorButtons({
  currentMode,
  onModeChange,
  disabled,
}: {
  currentMode: string
  onModeChange: (mode: PullbackMode) => void
  disabled: boolean
}) {
  const modes: { mode: PullbackMode; label: string }[] = [
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

export default function PullbackMonitor({ refreshInterval = 3000 }: Props) {
  const [data, setData] = useState<PullbackMonitoringResponse | null>(null)
  const [candidates, setCandidates] = useState<PullbackCandidate[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [changingMode, setChangingMode] = useState(false)

  const fetchData = async () => {
    try {
      const [statusResponse, candidatesResponse] = await Promise.all([
        api.getPullbackStatus(),
        api.getPullbackCandidates(5, 0),
      ])
      setData(statusResponse)
      setCandidates(candidatesResponse.candidates)
      setError(null)
    } catch (err) {
      setError('Pullback 데이터를 불러올 수 없습니다')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchData()
    const interval = setInterval(fetchData, refreshInterval)
    return () => clearInterval(interval)
  }, [refreshInterval])

  const handleModeChange = async (mode: PullbackMode) => {
    setChangingMode(true)
    try {
      await api.setPullbackMode(mode)
      await fetchData()
    } catch {
      // mode change failed
    } finally {
      setChangingMode(false)
    }
  }

  if (loading) {
    return (
      <div className="bg-slate-900 rounded-xl p-6 border border-slate-800">
        <h2 className="text-lg font-semibold text-white mb-4">📉 눌림목 매수</h2>
        <div className="flex gap-3 overflow-x-auto pb-2">
          {[1, 2, 3, 4, 5].map((i) => (
            <div key={i} className="bg-slate-800 rounded-lg p-3 border border-slate-700 animate-pulse min-w-[260px] h-40" />
          ))}
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="bg-slate-900 rounded-xl p-6 border border-slate-800">
        <h2 className="text-lg font-semibold text-white mb-4">📉 눌림목 매수</h2>
        <div className="text-center py-8 text-red-400">{error}</div>
      </div>
    )
  }

  const positions = data?.positions || {}
  const stats = data?.stats || {
    total_trades: 0, winning_trades: 0, losing_trades: 0,
    total_pnl_pct: 0, win_rate: 0,
    stop_loss_hits: 0, trailing_hits: 0, time_stop_hits: 0, take_profit_hits: 0, breakeven_hits: 0,
  }
  const settings = data?.settings || {
    stop_loss_pct: -0.015, take_profit_1_pct: 0.01,
    trailing_trigger_pct: 0.015, trailing_stop_pct: 0.005, time_stop_hours: 2,
  }

  const positionCount = Object.keys(positions).length
  const { color: modeColor, label: modeLabel } = getModeInfo(data?.mode || 'OFF')

  return (
    <div className="bg-slate-900 rounded-xl p-6 border border-slate-800">
      {/* 헤더 */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3">
          <h2 className="text-lg font-semibold text-white">📉 눌림목 매수</h2>
          <span className={`px-2 py-0.5 text-xs rounded ${modeColor}`}>
            {modeLabel}
          </span>
          <span className={`w-2 h-2 rounded-full ${data?.enabled ? 'bg-green-500' : 'bg-slate-500'}`} />
          <span className="text-xs text-slate-400">{data?.enabled ? '활성' : '비활성'}</span>
        </div>
        <ModeSelectorButtons
          currentMode={data?.mode || 'OFF'}
          onModeChange={handleModeChange}
          disabled={changingMode}
        />
      </div>

      {/* Top 5 후보 종목 (가로 스크롤) */}
      <div className="mb-4">
        <div className="flex items-center justify-between mb-2">
          <div className="text-xs text-slate-400">눌림목 후보 Top 5 (진입: 55점+)</div>
          <div className="flex items-center gap-2 text-xs">
            <span className="text-green-400">{candidates.filter(c => c.level > 0).length}개 진입가능</span>
            <span className="text-slate-500">{candidates.filter(c => c.level === 0).length}개 대기</span>
          </div>
        </div>
        {candidates.length > 0 ? (
          <div className="flex gap-3 overflow-x-auto pb-2">
            {candidates.map((c, idx) => (
              <CandidateCard key={c.symbol} candidate={c} rank={idx + 1} />
            ))}
          </div>
        ) : (
          <div className="text-center py-4 text-slate-500 text-xs bg-slate-800/30 rounded">
            후보 데이터 대기 중...
          </div>
        )}
      </div>

      {/* 활성 포지션 + 통계/설정 */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {positionCount > 0 && (
          <div className="lg:col-span-1">
            <div className="text-xs text-slate-400 mb-2">보유 포지션 ({positionCount})</div>
            <div className="space-y-2">
              {Object.entries(positions).map(([symbol, pos]) => (
                <PositionCard key={symbol} symbol={symbol} pos={pos} />
              ))}
            </div>
          </div>
        )}

        <div className={positionCount > 0 ? '' : 'lg:col-span-1'}>
          <StatsCard stats={stats} />
        </div>

        <div className={positionCount > 0 ? '' : 'lg:col-span-1'}>
          <SettingsCard settings={settings} />
        </div>
      </div>

      {/* 하단 안내 */}
      <div className="mt-4 pt-3 border-t border-slate-700">
        <div className="text-xs text-slate-500">
          <span className="text-orange-400">📉 눌림목 진입 조건</span>: 급등 이력 + 눌림 깊이 + 지지선 근접 + 반등 조짐 (L1=55점, L2=70점, L3=85점)
          <span className="ml-2 text-slate-600">| SL {(settings.stop_loss_pct * 100).toFixed(1)}% / TP +{(settings.take_profit_1_pct * 100).toFixed(1)}%</span>
        </div>
      </div>
    </div>
  )
}
