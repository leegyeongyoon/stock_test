'use client'

import { useEffect, useState } from 'react'
import { api, AttackCandidate } from '@/lib/api'

interface Props {
  candidates?: AttackCandidate[]  // 외부에서 주입 가능
  loading?: boolean
  refreshInterval?: number
}

// 점수별 색상
function getScoreColor(score: number): string {
  if (score >= 80) return 'text-green-400'
  if (score >= 70) return 'text-yellow-400'
  if (score >= 50) return 'text-orange-400'
  return 'text-slate-400'
}

function getScoreBgClass(score: number): string {
  if (score >= 80) return 'bg-green-600 text-white'
  if (score >= 70) return 'bg-yellow-500 text-black'
  if (score >= 50) return 'bg-orange-500 text-white'
  return 'bg-slate-600 text-slate-300'
}

// 컴포넌트 점수 바
function ComponentBar({ name, score, maxScore, details }: {
  name: string
  score: number
  maxScore: number
  details?: Record<string, unknown>
}) {
  const pct = maxScore > 0 ? (score / maxScore) * 100 : 0
  const isFull = pct >= 80
  const isHalf = pct >= 50 && pct < 80

  // 핵심 컴포넌트 강조
  const isKey = name === 'Candle Surge Bonus'

  return (
    <div className={`${isKey ? 'bg-slate-700/50 p-2 rounded border border-slate-600' : ''}`}>
      <div className="flex items-center justify-between text-xs mb-1">
        <span className={`${isKey ? 'text-yellow-400 font-medium' : 'text-slate-400'}`}>
          {isKey && '⚡ '}{name}
        </span>
        <span className={`font-medium ${isFull ? 'text-green-400' : isHalf ? 'text-yellow-400' : 'text-slate-500'}`}>
          {score.toFixed(0)}/{maxScore}
        </span>
      </div>
      <div className="h-1.5 bg-slate-700 rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full transition-all ${
            isFull ? 'bg-green-500' : isHalf ? 'bg-yellow-500' : 'bg-slate-500'
          }`}
          style={{ width: `${Math.min(pct, 100)}%` }}
        />
      </div>
      {isKey && details && (
        <div className="mt-1 text-[10px] text-slate-500">
          1m: {((details.change_1m as number) * 100).toFixed(1)}% |
          5m: {((details.change_5m as number) * 100).toFixed(1)}% |
          Trigger: {details.trigger as string || 'NONE'}
        </div>
      )}
    </div>
  )
}

// 후보 카드
function CandidateCard({ candidate, rank }: { candidate: AttackCandidate; rank: number }) {
  const [expanded, setExpanded] = useState(false)
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
    <div className={`bg-slate-800 rounded-lg border ${canEntry ? 'border-green-600' : 'border-slate-700'} overflow-hidden min-w-[300px]`}>
      {/* 헤더 */}
      <div
        className="p-3 cursor-pointer hover:bg-slate-750 transition-colors"
        onClick={() => setExpanded(!expanded)}
      >
        <div className="flex items-center gap-3">
          {/* 순위 */}
          <span className={`${rankBg} w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold text-white shrink-0`}>
            {rank}
          </span>

          {/* 심볼 + 변화율 */}
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <span className="font-bold text-white text-sm">{symbolCode}</span>
              <span className={`text-xs ${isPositive ? 'text-green-400' : 'text-red-400'}`}>
                {isPositive ? '+' : ''}{changeRatePct}%
              </span>
              {hasSurgeBonus && (
                <span className="px-1.5 py-0.5 text-[10px] bg-yellow-600 text-black rounded font-bold">
                  SURGE
                </span>
              )}
            </div>
            {candidate.distance_to_entry > 0 ? (
              <div className="text-xs text-slate-500 mt-0.5">
                진입까지 {candidate.distance_to_entry.toFixed(0)}점 부족
              </div>
            ) : canEntry && (
              <div className="text-xs text-green-400 mt-0.5">
                ✓ 진입 조건 충족
              </div>
            )}
          </div>

          {/* 점수 + 레벨 */}
          <div className="text-right">
            <span className={`px-2 py-1 rounded text-sm font-bold ${getScoreBgClass(candidate.score)}`}>
              {candidate.score.toFixed(0)}점
            </span>
            {canEntry && (
              <div className="text-xs text-green-400 mt-1">L{candidate.level}</div>
            )}
          </div>

          {/* 확장 아이콘 */}
          <svg
            className={`w-4 h-4 text-slate-400 transition-transform ${expanded ? 'rotate-180' : ''}`}
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
          </svg>
        </div>
      </div>

      {/* 상세 (확장 시) */}
      {expanded && (
        <div className="px-3 pb-3 border-t border-slate-700 pt-3 space-y-2">
          {candidate.components.map((comp, idx) => (
            <ComponentBar
              key={idx}
              name={comp.name}
              score={comp.score}
              maxScore={comp.max_score}
              details={comp.details}
            />
          ))}
          <div className="pt-2 border-t border-slate-700 text-xs text-slate-500">
            거래대금: ₩{(candidate.volume_24h / 100000000).toFixed(1)}억
          </div>
        </div>
      )}
    </div>
  )
}

export default function SurgeCandidates({ candidates: externalCandidates, loading: externalLoading, refreshInterval = 5000 }: Props) {
  const [candidates, setCandidates] = useState<AttackCandidate[]>(externalCandidates || [])
  const [loading, setLoading] = useState(externalLoading ?? true)
  const [error, setError] = useState<string | null>(null)
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)

  // 외부에서 주입되지 않으면 직접 fetch
  useEffect(() => {
    if (externalCandidates !== undefined) {
      setCandidates(externalCandidates)
      setLoading(false)
      return
    }

    const fetchData = async () => {
      try {
        // 항상 Top 5 표시 (min_score=0)
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
            <div key={i} className="bg-slate-800 rounded-lg p-3 border border-slate-700 animate-pulse min-w-[280px] h-24" />
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

  // 진입 가능한 것과 아닌 것 분리
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
        </div>
        <div className="text-xs text-slate-500">
          {lastUpdated && `갱신: ${lastUpdated.toLocaleTimeString('ko-KR')}`}
        </div>
      </div>

      {/* Top 5 후보 (항상 표시) */}
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
          <p className="text-sm mt-1">캐시 업데이트 대기 중...</p>
        </div>
      )}

      {/* 진입 가능 수 표시 */}
      <div className="mt-3 flex gap-4 text-xs">
        <span className="text-green-400">✓ 진입가능: {entryReady.length}개</span>
        <span className="text-slate-400">👀 대기중: {watching.length}개</span>
      </div>

      {/* 하단 안내 */}
      <div className="mt-4 pt-3 border-t border-slate-700">
        <div className="text-xs text-slate-500">
          <span className="text-yellow-400">⚡ Candle Surge Bonus</span>: 1분봉 +7% & RVOL 3x 또는 5분봉 +4% & RVOL 2x 시 발동 (최대 30점)
        </div>
      </div>
    </div>
  )
}
