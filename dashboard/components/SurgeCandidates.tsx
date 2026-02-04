'use client'

import { useEffect, useState } from 'react'
import { api, AttackCandidate } from '@/lib/api'

interface Props {
  candidates?: AttackCandidate[]
  loading?: boolean
  refreshInterval?: number
}

// 컴포넌트 이름 한글화
function getKoreanName(name: string): string {
  const nameMap: Record<string, string> = {
    'Breakout Strength': '돌파 강도',
    'Volume Expansion': '거래량 확대',
    'Trend Context': '추세 맥락',
    'Orderbook Quality': '호가 품질',
    'Overheat Penalty': '과열 페널티',
    'Candle Surge Bonus': '급등 보너스',
  }
  return nameMap[name] || name
}

// 점수 설명 생성
function getScoreExplanation(name: string, score: number, maxScore: number, details?: Record<string, unknown>): string {
  if (!details) return ''

  switch (name) {
    case 'Breakout Strength': {
      const breakoutPct = ((details.breakout_pct as number) * 100).toFixed(1)
      const vwapDist = ((details.vwap_distance as number) * 100).toFixed(2)
      const closePos = ((details.close_pos as number) * 100).toFixed(0)
      return `돌파: ${breakoutPct}% | VWAP대비: +${vwapDist}% | 캔들위치: ${closePos}%`
    }
    case 'Volume Expansion': {
      const rvol = (details.rvol as number).toFixed(1)
      const vol24h = ((details.volume_24h as number) / 100000000).toFixed(1)
      return `RVOL: ${rvol}x | 24h거래대금: ${vol24h}억`
    }
    case 'Trend Context': {
      const changeRate = ((details.change_rate as number) * 100).toFixed(1)
      return `일간변화율: ${changeRate}%`
    }
    case 'Orderbook Quality': {
      const spread = details.spread_bps as number
      const depthRatio = (details.depth_ratio as number).toFixed(2)
      return `스프레드: ${spread}bp | 호가비율: ${depthRatio}`
    }
    case 'Overheat Penalty': {
      const dailyPenalty = details.daily_penalty as number
      const hourlyPenalty = details.hourly_penalty as number
      const rvolPenalty = details.rvol_penalty as number
      if (score === 0) return '과열 없음'
      return `일간: ${dailyPenalty}점 | 시간: ${hourlyPenalty}점 | RVOL: ${rvolPenalty}점`
    }
    case 'Candle Surge Bonus': {
      const change1m = ((details.change_1m as number) * 100).toFixed(1)
      const rvol1m = (details.rvol_1m as number).toFixed(1)
      const change5m = ((details.change_5m as number) * 100).toFixed(1)
      const rvol5m = (details.rvol_5m as number).toFixed(1)
      const trigger = details.trigger as string
      if (trigger === 'NONE') {
        return `1분봉: +${change1m}%(RVOL ${rvol1m}x) | 5분봉: +${change5m}%(RVOL ${rvol5m}x) → 미충족`
      }
      return `${trigger} 트리거 발동! 1분봉: +${change1m}% | 5분봉: +${change5m}%`
    }
    default:
      return ''
  }
}

// 점수별 배경색
function getScoreBgClass(score: number): string {
  if (score >= 80) return 'bg-green-600 text-white'
  if (score >= 70) return 'bg-yellow-500 text-black'
  if (score >= 50) return 'bg-orange-500 text-white'
  return 'bg-slate-600 text-slate-300'
}

// 후보 카드 (항상 펼쳐진 상태)
function CandidateCard({ candidate, rank }: { candidate: AttackCandidate; rank: number }) {
  const symbolCode = candidate.symbol.replace('KRW-', '')

  const rankBg = rank === 1 ? 'bg-gradient-to-br from-yellow-500 to-yellow-700' :
                 rank === 2 ? 'bg-gradient-to-br from-slate-300 to-slate-500' :
                 rank === 3 ? 'bg-gradient-to-br from-amber-600 to-amber-800' :
                 'bg-slate-600'

  const canEntry = candidate.level > 0
  const changeRatePct = (candidate.change_rate * 100).toFixed(1)
  const isPositive = candidate.change_rate > 0

  // Candle Surge Bonus 찾기
  const surgeBonusComp = candidate.components.find(c => c.name === 'Candle Surge Bonus')
  const hasSurgeBonus = surgeBonusComp && surgeBonusComp.score > 0

  return (
    <div className={`bg-slate-800 rounded-lg border ${canEntry ? 'border-green-600' : 'border-slate-700'} overflow-hidden min-w-[320px] max-w-[360px]`}>
      {/* 헤더 */}
      <div className="p-3 bg-slate-800/80">
        <div className="flex items-center gap-3">
          {/* 순위 */}
          <span className={`${rankBg} w-8 h-8 rounded-full flex items-center justify-center text-sm font-bold text-white shrink-0`}>
            {rank}
          </span>

          {/* 심볼 + 한국어 이름 + 변화율 */}
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <span className="font-bold text-white">{symbolCode}</span>
              {candidate.korean_name && (
                <span className="text-xs text-slate-400">{candidate.korean_name}</span>
              )}
              <span className={`text-sm ${isPositive ? 'text-green-400' : 'text-red-400'}`}>
                {isPositive ? '+' : ''}{changeRatePct}%
              </span>
              {hasSurgeBonus && (
                <span className="px-1.5 py-0.5 text-[10px] bg-yellow-600 text-black rounded font-bold animate-pulse">
                  급등!
                </span>
              )}
            </div>
            <div className="text-xs text-slate-500">
              거래대금: ₩{(candidate.volume_24h / 100000000).toFixed(1)}억
            </div>
          </div>

          {/* 점수 + 레벨 */}
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

      {/* 상세 (항상 표시) */}
      <div className="px-3 pb-3 pt-2 space-y-1.5 bg-slate-850">
        {candidate.components.map((comp, idx) => {
          const pct = comp.max_score > 0 ? (comp.score / comp.max_score) * 100 : 0
          const isFull = pct >= 80
          const isHalf = pct >= 50 && pct < 80
          const isKey = comp.name === 'Candle Surge Bonus'
          const isPenalty = comp.name === 'Overheat Penalty'

          return (
            <div key={idx} className={`${isKey ? 'bg-slate-700/50 p-2 rounded border border-yellow-600/50' : ''}`}>
              <div className="flex items-center justify-between text-xs mb-0.5">
                <span className={`${isKey ? 'text-yellow-400 font-medium' : isPenalty ? 'text-red-400' : 'text-slate-400'}`}>
                  {isKey && '⚡ '}{isPenalty && '🔥 '}{getKoreanName(comp.name)}
                </span>
                <span className={`font-medium ${
                  isPenalty && comp.score < 0 ? 'text-red-400' :
                  isFull ? 'text-green-400' :
                  isHalf ? 'text-yellow-400' : 'text-slate-500'
                }`}>
                  {comp.score.toFixed(0)}/{comp.max_score}
                </span>
              </div>
              <div className="h-1 bg-slate-700 rounded-full overflow-hidden mb-1">
                <div
                  className={`h-full rounded-full transition-all ${
                    isPenalty && comp.score < 0 ? 'bg-red-500' :
                    isFull ? 'bg-green-500' :
                    isHalf ? 'bg-yellow-500' : 'bg-slate-500'
                  }`}
                  style={{ width: `${Math.min(Math.abs(pct), 100)}%` }}
                />
              </div>
              <div className="text-[10px] text-slate-500">
                {getScoreExplanation(comp.name, comp.score, comp.max_score, comp.details)}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

export default function SurgeCandidates({ candidates: externalCandidates, loading: externalLoading, refreshInterval = 5000 }: Props) {
  const [candidates, setCandidates] = useState<AttackCandidate[]>(externalCandidates || [])
  const [loading, setLoading] = useState(externalLoading ?? true)
  const [error, setError] = useState<string | null>(null)
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)

  useEffect(() => {
    if (externalCandidates !== undefined) {
      setCandidates(externalCandidates)
      setLoading(false)
      return
    }

    const fetchData = async () => {
      try {
        const response = await api.getMonitoringCandidates(5, 0)
        setCandidates(response.attack_candidates)
        setLastUpdated(new Date())
        setError(null)
      } catch (err) {
        setError('데이터를 불러올 수 없습니다')
        console.error('Attack candidates fetch error:', err)
      } finally {
        setLoading(false)
      }
    }

    fetchData()
    const interval = setInterval(fetchData, refreshInterval)
    return () => clearInterval(interval)
  }, [externalCandidates, refreshInterval])

  if (loading) {
    return (
      <div className="bg-slate-900 rounded-xl p-6 border border-slate-800">
        <h2 className="text-lg font-semibold text-white mb-4">🎯 Attack 후보 종목</h2>
        <div className="flex gap-3 overflow-x-auto pb-2">
          {[1, 2, 3, 4, 5].map((i) => (
            <div key={i} className="bg-slate-800 rounded-lg p-3 border border-slate-700 animate-pulse min-w-[320px] h-48" />
          ))}
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="bg-slate-900 rounded-xl p-6 border border-slate-800">
        <h2 className="text-lg font-semibold text-white mb-4">🎯 Attack 후보 종목</h2>
        <div className="text-center py-8 text-red-400">{error}</div>
      </div>
    )
  }

  const entryReady = candidates.filter(c => c.level > 0)
  const watching = candidates.filter(c => c.level === 0)

  return (
    <div className="bg-slate-900 rounded-xl p-6 border border-slate-800">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3">
          <h2 className="text-lg font-semibold text-white">🎯 Attack 후보 종목</h2>
          <span className="px-2 py-0.5 text-xs bg-slate-700 text-slate-300 rounded">
            진입: 80점+
          </span>
          <span className="text-green-400 text-xs">✓ {entryReady.length}개</span>
          <span className="text-slate-400 text-xs">👀 {watching.length}개</span>
        </div>
        <div className="text-xs text-slate-500">
          {lastUpdated && `${lastUpdated.toLocaleTimeString('ko-KR')}`}
        </div>
      </div>

      {/* Top 5 후보 */}
      {candidates.length > 0 ? (
        <div className="flex gap-3 overflow-x-auto pb-2">
          {candidates.map((c, idx) => (
            <CandidateCard key={c.symbol} candidate={c} rank={idx + 1} />
          ))}
        </div>
      ) : (
        <div className="text-center py-8 text-slate-500">
          <div className="text-4xl mb-3">😴</div>
          <p>현재 Attack 후보 데이터가 없습니다</p>
        </div>
      )}

      {/* 하단 안내 */}
      <div className="mt-4 pt-3 border-t border-slate-700">
        <div className="text-xs text-slate-500">
          <span className="text-yellow-400">⚡ 급등 보너스 조건</span>: 1분봉 +7% & RVOL 3배 또는 5분봉 +4% & RVOL 2배 시 발동 (최대 30점)
        </div>
      </div>
    </div>
  )
}
