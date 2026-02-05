'use client'

import { useEffect, useState } from 'react'
import { api, AttackCandidate, FilterStats, MonitoringCandidatesResponse } from '@/lib/api'

interface Props {
  refreshInterval?: number  // ms
}

// 점수별 배지 색상 (v5.4: L3=85, L2=70, L1=50)
function getScoreBadgeClass(score: number): string {
  if (score >= 85) return 'bg-green-600 text-white'        // L3 진입 가능
  if (score >= 70) return 'bg-yellow-500 text-black'       // L2 진입 가능
  if (score >= 50) return 'bg-orange-500 text-white'       // L1 진입 가능
  return 'bg-slate-600 text-slate-300'                     // 진입 불가
}

// 점수별 텍스트 색상 (v5.4: L3=85, L2=70, L1=50)
function getScoreTextClass(score: number): string {
  if (score >= 85) return 'text-green-400'                 // L3
  if (score >= 70) return 'text-yellow-400'                // L2
  if (score >= 50) return 'text-orange-400'                // L1
  return 'text-slate-400'                                  // 진입 불가
}

// 후보 카드 컴포넌트
function CandidateCard({ candidate, rank }: { candidate: AttackCandidate; rank: number }) {
  const symbolCode = candidate.symbol.replace('KRW-', '')
  const changeRatePct = (candidate.change_rate * 100).toFixed(1)
  const isPositive = candidate.change_rate > 0

  // 순위별 배경색
  const rankBg = rank === 1 ? 'bg-gradient-to-br from-yellow-500 to-yellow-700' :
                 rank === 2 ? 'bg-gradient-to-br from-slate-300 to-slate-500' :
                 rank === 3 ? 'bg-gradient-to-br from-amber-600 to-amber-800' :
                 'bg-slate-600'

  return (
    <div className="flex items-center gap-3 p-3 bg-slate-800 rounded-lg border border-slate-700 hover:border-slate-600 transition-colors">
      {/* 순위 */}
      <span className={`${rankBg} w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold text-white shrink-0`}>
        {rank}
      </span>

      {/* 심볼 정보 */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="font-bold text-white text-sm">{symbolCode}</span>
          <span className={`text-xs ${isPositive ? 'text-green-400' : 'text-red-400'}`}>
            {isPositive ? '+' : ''}{changeRatePct}%
          </span>
        </div>
        {candidate.distance_to_entry > 0 && (
          <div className="text-xs text-slate-500 mt-0.5">
            진입까지 -{candidate.distance_to_entry.toFixed(0)}점
          </div>
        )}
      </div>

      {/* 점수 */}
      <div className="text-right">
        <span className={`px-2 py-1 rounded text-sm font-bold ${getScoreBadgeClass(candidate.score)}`}>
          {candidate.score.toFixed(0)}점
        </span>
        {candidate.level > 0 && (
          <div className="text-xs text-green-400 mt-1">L{candidate.level} 진입가능</div>
        )}
      </div>
    </div>
  )
}

// 필터링 통계 컴포넌트
function FilterStatsCard({ stats }: { stats: FilterStats }) {
  return (
    <div className="bg-slate-800/50 rounded-lg p-3 border border-slate-700">
      <div className="text-xs text-slate-400 mb-2">오늘 필터링 현황</div>
      <div className="grid grid-cols-2 gap-2 text-xs">
        <div className="flex justify-between">
          <span className="text-slate-500">노출한도</span>
          <span className="text-slate-300">{stats.exposure_limit}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-slate-500">Anti-chase</span>
          <span className="text-slate-300">{stats.anti_chase}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-slate-500">과열</span>
          <span className="text-slate-300">{stats.overheat}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-slate-500 font-medium">총 차단</span>
          <span className="text-orange-400 font-medium">{stats.total}</span>
        </div>
      </div>
    </div>
  )
}

export default function AlgorithmMonitor({ refreshInterval = 3000 }: Props) {
  const [data, setData] = useState<MonitoringCandidatesResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const fetchData = async () => {
      try {
        const response = await api.getMonitoringCandidates(5, 50)
        setData(response)
        setError(null)
      } catch (err) {
        setError('모니터링 데이터를 불러올 수 없습니다')
        console.error('Monitoring fetch error:', err)
      } finally {
        setLoading(false)
      }
    }

    fetchData()
    const interval = setInterval(fetchData, refreshInterval)
    return () => clearInterval(interval)
  }, [refreshInterval])

  if (loading) {
    return (
      <div className="bg-slate-900 rounded-xl p-4 border border-slate-800">
        <h2 className="text-base font-semibold text-white mb-3 flex items-center gap-2">
          <span className="text-lg">&#127919;</span> {/* target emoji */}
          알고리즘 모니터링
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
          알고리즘 모니터링
        </h2>
        <div className="text-center py-4 text-red-400 text-sm">{error}</div>
      </div>
    )
  }

  const candidates = data?.attack_candidates || []
  const filterStats = data?.filter_stats || { anti_chase: 0, exposure_limit: 0, overheat: 0, total: 0 }

  return (
    <div className="bg-slate-900 rounded-xl p-4 border border-slate-800">
      {/* 헤더 */}
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-base font-semibold text-white flex items-center gap-2">
          <span className="text-lg">&#127919;</span>
          Attack 후보 Top 5
        </h2>
        <div className="text-xs text-slate-500">
          진입: {data?.entry_threshold || 50}점
        </div>
      </div>

      {/* 후보 목록 */}
      {candidates.length === 0 ? (
        <div className="text-center py-6 text-slate-500">
          <div className="text-2xl mb-2">&#128564;</div> {/* sleeping face */}
          <p className="text-sm">현재 모니터링 중인 후보가 없습니다</p>
          <p className="text-xs mt-1">50점 이상 종목만 표시됩니다</p>
        </div>
      ) : (
        <div className="space-y-2 mb-4">
          {candidates.map((candidate, idx) => (
            <CandidateCard key={candidate.symbol} candidate={candidate} rank={idx + 1} />
          ))}
        </div>
      )}

      {/* 필터링 통계 */}
      <FilterStatsCard stats={filterStats} />

      {/* 마지막 업데이트 */}
      <div className="mt-2 text-xs text-slate-600 text-right">
        {data?.cache_updated_at ? (
          `갱신: ${new Date(data.cache_updated_at).toLocaleTimeString('ko-KR')}`
        ) : (
          '갱신 대기 중...'
        )}
      </div>
    </div>
  )
}
