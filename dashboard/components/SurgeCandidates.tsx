'use client'

import { useState } from 'react'
import { SurgeCandidate } from '@/lib/api'
import { formatKRW } from '@/lib/currency'

interface Props {
  candidates: SurgeCandidate[]
  loading?: boolean
}

// 조건 설명 매핑
const CONDITION_INFO = {
  change_1m: {
    name: '1분 상승률',
    desc: '최근 1분간 가격 상승폭',
    target: '1.5% 이상',
    icon: '📈',
  },
  volume: {
    name: '거래량 배수',
    desc: '평균 대비 거래량',
    target: '3배 이상',
    icon: '📊',
  },
  volume_spike: {
    name: '거래량 급증',
    desc: '직전 1분 대비 급증',
    target: '2배 이상',
    icon: '⚡',
  },
  volume_accel: {
    name: '거래량 가속',
    desc: '거래량 증가 추세',
    target: '1.5배 이상',
    icon: '🚀',
  },
  vol_overheat: {
    name: '과열 체크',
    desc: '너무 늦은 진입 방지',
    target: '8배 미만',
    icon: '🌡️',
  },
  anti_chase: {
    name: '추격매수 방지',
    desc: '고점 추격 방지',
    target: 'ATR 1.5 이내',
    icon: '🛡️',
  },
}

// 조건 충족 여부 체크
function isConditionMet(score: number, threshold: number = 80): boolean {
  return score >= threshold
}

// 조건별 상태 뱃지
function ConditionBadge({
  met,
  label,
  value
}: {
  met: boolean
  label: string
  value: string
}) {
  return (
    <div className={`flex items-center gap-1.5 px-2 py-1 rounded-md text-xs ${
      met
        ? 'bg-green-900/40 text-green-400 border border-green-700/50'
        : 'bg-slate-800 text-slate-400 border border-slate-700'
    }`}>
      <span>{met ? '✓' : '○'}</span>
      <span className="font-medium">{label}</span>
      <span className="text-[10px] opacity-70">{value}</span>
    </div>
  )
}

// 조건 상태 칩 (작은 뱃지)
function ConditionChip({ met, label, value }: { met: boolean; label: string; value: string }) {
  return (
    <div className={`flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] ${
      met
        ? 'bg-green-900/50 text-green-400'
        : 'bg-slate-700/50 text-slate-500'
    }`}>
      <span>{met ? '✓' : '✗'}</span>
      <span className="font-medium">{label}</span>
      <span className="opacity-70">{value}</span>
    </div>
  )
}

// Top 5 컴팩트 카드 - 개선된 버전
function Top5Card({ candidate, rank }: { candidate: SurgeCandidate; rank: number }) {
  const scoreColor = candidate.total_score >= 90 ? 'text-green-400' :
                     candidate.total_score >= 80 ? 'text-blue-400' :
                     candidate.total_score >= 70 ? 'text-yellow-400' : 'text-slate-300'

  const rankBg = rank === 1 ? 'bg-gradient-to-br from-yellow-500 to-yellow-700' :
                 rank === 2 ? 'bg-gradient-to-br from-slate-300 to-slate-500' :
                 rank === 3 ? 'bg-gradient-to-br from-amber-600 to-amber-800' : 'bg-slate-600'

  const symbolCode = candidate.symbol.replace('KRW-', '')
  const displayName = candidate.korean_name || symbolCode

  // 충족된 조건 수 계산
  const conditions = [
    {
      met: isConditionMet(candidate.change_1m.score, 70),
      label: '상승률',
      value: `${candidate.change_1m.value?.toFixed(1)}%`
    },
    {
      met: isConditionMet(candidate.volume.score, 70),
      label: '거래량',
      value: `${candidate.volume.ratio?.toFixed(1)}배`
    },
    {
      met: isConditionMet(candidate.volume_spike?.score ?? 0, 70),
      label: '급증',
      value: `${candidate.volume_spike?.ratio?.toFixed(1) ?? 0}배`
    },
    {
      met: isConditionMet(candidate.volume_accel?.score ?? 0, 70),
      label: '가속',
      value: `${candidate.volume_accel?.ratio?.toFixed(1) ?? 0}배`
    },
    {
      met: candidate.vol_overheat.score >= 100,
      label: '과열',
      value: candidate.vol_overheat.score >= 100 ? 'OK' : 'NO'
    },
    {
      met: isConditionMet(candidate.anti_chase.score, 70),
      label: '추격',
      value: candidate.anti_chase.entry_type || '-'
    },
  ]
  const metCount = conditions.filter(c => c.met).length

  return (
    <div className="bg-slate-800 rounded-lg p-3 border border-slate-700 min-w-[280px] hover:border-slate-600 transition-colors">
      {/* 상단: 순위 + 코인명 + 점수 */}
      <div className="flex items-center gap-2 mb-2">
        <span className={`${rankBg} w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold text-white shrink-0`}>
          {rank}
        </span>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5">
            <span className="font-bold text-white">{symbolCode}</span>
            <span className="text-xs text-slate-400 truncate">{displayName}</span>
          </div>
        </div>
        <div className="text-right">
          <div className={`text-lg font-bold ${scoreColor}`}>
            {candidate.total_score.toFixed(0)}점
          </div>
          <div className="text-[10px] text-slate-500">{metCount}/6 충족</div>
        </div>
      </div>

      {/* 가격 + 변화율 */}
      <div className="flex items-center justify-between text-xs mb-2 pb-2 border-b border-slate-700">
        <span className="text-slate-400">{formatKRW(candidate.current_price)}</span>
        <span className={`font-medium ${(candidate.change_1m.value ?? 0) > 0 ? 'text-green-400' : 'text-red-400'}`}>
          {(candidate.change_1m.value ?? 0) > 0 ? '+' : ''}{candidate.change_1m.value?.toFixed(2)}%
        </span>
      </div>

      {/* 조건 충족 현황 - 라벨과 함께 표시 */}
      <div className="grid grid-cols-3 gap-1">
        {conditions.map((cond, idx) => (
          <ConditionChip key={idx} met={cond.met} label={cond.label} value={cond.value} />
        ))}
      </div>

      {/* 전일급등 태그 */}
      {candidate.is_hot_yesterday && (
        <div className="mt-2 pt-2 border-t border-slate-700">
          <span className="px-2 py-0.5 text-[10px] bg-orange-600/30 text-orange-400 rounded border border-orange-600/50">
            ⚠️ 전일 급등 종목
          </span>
        </div>
      )}
    </div>
  )
}

// 조건 상세 행
interface ConditionRowProps {
  icon: string
  name: string
  desc: string
  targetDesc: string
  score: number
  currentValue: string
  isMet: boolean
  isPassFail?: boolean  // vol_overheat처럼 통과/차단 여부만 중요한 경우
}

function ConditionRow({ icon, name, desc, targetDesc, score, currentValue, isMet, isPassFail }: ConditionRowProps) {
  return (
    <div className={`flex items-center gap-3 p-2 rounded-lg ${
      isMet ? 'bg-green-900/20' : 'bg-slate-800/50'
    }`}>
      <span className="text-lg">{icon}</span>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="font-medium text-white text-sm">{name}</span>
          <span className="text-[10px] text-slate-500">{desc}</span>
        </div>
        <div className="flex items-center gap-2 text-xs mt-0.5">
          <span className={`font-medium ${isMet ? 'text-green-400' : 'text-slate-400'}`}>
            {currentValue}
          </span>
          <span className="text-slate-600">목표: {targetDesc}</span>
        </div>
      </div>
      <div className="shrink-0">
        {isPassFail ? (
          <span className={`px-2 py-1 rounded text-xs font-bold ${
            isMet
              ? 'bg-green-600 text-white'
              : 'bg-red-600 text-white'
          }`}>
            {isMet ? '통과' : '차단'}
          </span>
        ) : (
          <div className="text-right">
            <div className={`text-sm font-bold ${
              isMet ? 'text-green-400' : score >= 50 ? 'text-yellow-400' : 'text-red-400'
            }`}>
              {score.toFixed(0)}점
            </div>
            <div className={`text-[10px] ${isMet ? 'text-green-500' : 'text-slate-500'}`}>
              {isMet ? '✓ 충족' : '미충족'}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

// 상세 카드
function CandidateCard({ candidate }: { candidate: SurgeCandidate }) {
  const [expanded, setExpanded] = useState(false)

  const symbolCode = candidate.symbol.replace('KRW-', '')
  const displayName = candidate.korean_name || symbolCode

  // 충족된 조건 수 계산
  const conditions = [
    isConditionMet(candidate.change_1m.score, 70),
    isConditionMet(candidate.volume.score, 70),
    isConditionMet(candidate.volume_spike?.score ?? 0, 70),
    isConditionMet(candidate.volume_accel?.score ?? 0, 70),
    candidate.vol_overheat.score >= 100,
    isConditionMet(candidate.anti_chase.score, 70),
  ]
  const metCount = conditions.filter(Boolean).length

  const scoreColor = candidate.total_score >= 90 ? 'text-green-400' :
                     candidate.total_score >= 80 ? 'text-blue-400' :
                     candidate.total_score >= 70 ? 'text-yellow-400' : 'text-slate-300'

  return (
    <div className="bg-slate-800 rounded-lg border border-slate-700 overflow-hidden">
      {/* Header */}
      <button
        className="w-full p-3 flex items-center justify-between hover:bg-slate-750 transition-colors"
        onClick={() => setExpanded(!expanded)}
      >
        <div className="flex items-center gap-3">
          <span className="text-base font-bold text-white">{symbolCode}</span>
          <span className="text-sm text-slate-400">{displayName}</span>
          {candidate.is_hot_yesterday && (
            <span className="px-1.5 py-0.5 text-[10px] bg-orange-600/30 text-orange-400 rounded">
              전일급등
            </span>
          )}
        </div>
        <div className="flex items-center gap-3">
          <span className="text-xs text-slate-500">{metCount}/6</span>
          <span className={`text-lg font-bold ${scoreColor}`}>
            {candidate.total_score.toFixed(0)}점
          </span>
          <svg
            className={`w-4 h-4 text-slate-400 transition-transform ${expanded ? 'rotate-180' : ''}`}
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
          </svg>
        </div>
      </button>

      {/* Expanded Content */}
      {expanded && (
        <div className="px-3 pb-3 border-t border-slate-700 pt-3 space-y-2">
          <ConditionRow
            icon="📈"
            name="1분 상승률"
            desc="최근 1분간 상승폭"
            targetDesc="1.5% 이상"
            score={candidate.change_1m.score}
            currentValue={`${candidate.change_1m.value?.toFixed(2)}%`}
            isMet={isConditionMet(candidate.change_1m.score, 70)}
          />
          <ConditionRow
            icon="📊"
            name="거래량 배수"
            desc="평균 대비"
            targetDesc="3배 이상"
            score={candidate.volume.score}
            currentValue={`${candidate.volume.ratio?.toFixed(1)}배`}
            isMet={isConditionMet(candidate.volume.score, 70)}
          />
          <ConditionRow
            icon="⚡"
            name="거래량 급증"
            desc="직전 1분 대비"
            targetDesc="2배 이상"
            score={candidate.volume_spike?.score ?? 0}
            currentValue={`${candidate.volume_spike?.ratio?.toFixed(1) ?? 0}배`}
            isMet={isConditionMet(candidate.volume_spike?.score ?? 0, 70)}
          />
          <ConditionRow
            icon="🚀"
            name="거래량 가속"
            desc="증가 추세"
            targetDesc="1.5배 이상"
            score={candidate.volume_accel?.score ?? 0}
            currentValue={`${candidate.volume_accel?.ratio?.toFixed(2) ?? 0}배`}
            isMet={isConditionMet(candidate.volume_accel?.score ?? 0, 70)}
          />
          <ConditionRow
            icon="🌡️"
            name="과열 체크"
            desc="너무 늦으면 차단"
            targetDesc="8배 미만"
            score={candidate.vol_overheat.score}
            currentValue={candidate.vol_overheat.score >= 100 ? '정상' : candidate.vol_overheat.reason || '과열'}
            isMet={candidate.vol_overheat.score >= 100}
            isPassFail={true}
          />
          <ConditionRow
            icon="🛡️"
            name="추격매수 방지"
            desc="고점 추격 방지"
            targetDesc="ATR 1.5 이내"
            score={candidate.anti_chase.score}
            currentValue={`${candidate.anti_chase.entry_type || '-'} (ATR ${candidate.anti_chase.dist_atr?.toFixed(2) || '-'})`}
            isMet={isConditionMet(candidate.anti_chase.score, 70)}
          />
        </div>
      )}
    </div>
  )
}

export default function SurgeCandidates({ candidates, loading }: Props) {
  const [showAll, setShowAll] = useState(false)
  const [showHelp, setShowHelp] = useState(false)

  if (loading) {
    return (
      <div className="bg-slate-900 rounded-xl p-6 border border-slate-800">
        <h2 className="text-lg font-semibold text-white mb-4">🎯 급등 근접 종목</h2>
        <div className="flex gap-3 overflow-x-auto pb-2">
          {[1, 2, 3, 4, 5].map((i) => (
            <div key={i} className="bg-slate-800 rounded-lg p-3 border border-slate-700 animate-pulse min-w-[240px]">
              <div className="flex items-center gap-3">
                <div className="w-6 h-6 bg-slate-700 rounded-full"></div>
                <div className="flex-1">
                  <div className="h-4 bg-slate-700 rounded w-20 mb-1"></div>
                  <div className="h-3 bg-slate-700 rounded w-16"></div>
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>
    )
  }

  if (!candidates || candidates.length === 0) {
    return (
      <div className="bg-slate-900 rounded-xl p-6 border border-slate-800">
        <h2 className="text-lg font-semibold text-white mb-4">🎯 급등 근접 종목</h2>
        <div className="text-center py-8 text-slate-500">
          <div className="text-4xl mb-3">😴</div>
          <p>현재 급등 조건에 근접한 종목이 없습니다</p>
          <p className="text-sm mt-1">종합 점수 70점 이상 종목만 표시됩니다</p>
        </div>
      </div>
    )
  }

  const top5 = candidates.slice(0, 5)
  const rest = candidates.slice(5)

  return (
    <div className="bg-slate-900 rounded-xl p-6 border border-slate-800">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <h2 className="text-lg font-semibold text-white">🎯 급등 근접 종목</h2>
          <button
            onClick={() => setShowHelp(!showHelp)}
            className="w-5 h-5 rounded-full bg-slate-700 text-slate-400 text-xs hover:bg-slate-600 hover:text-white transition-colors"
            title="도움말"
          >
            ?
          </button>
        </div>
        <span className="text-sm text-slate-400">
          {candidates.length}개 감지
        </span>
      </div>

      {/* 도움말 패널 */}
      {showHelp && (
        <div className="mb-4 p-4 bg-slate-800 rounded-lg border border-slate-700">
          <h3 className="font-medium text-white mb-3">📖 조건 설명</h3>
          <div className="grid grid-cols-2 gap-3 text-xs">
            <div className="flex items-start gap-2">
              <span>📈</span>
              <div>
                <div className="font-medium text-white">1분 상승률</div>
                <div className="text-slate-400">최근 1분간 1.5% 이상 상승</div>
              </div>
            </div>
            <div className="flex items-start gap-2">
              <span>📊</span>
              <div>
                <div className="font-medium text-white">거래량 배수</div>
                <div className="text-slate-400">평균 대비 3배 이상 거래량</div>
              </div>
            </div>
            <div className="flex items-start gap-2">
              <span>⚡</span>
              <div>
                <div className="font-medium text-white">거래량 급증</div>
                <div className="text-slate-400">직전 1분 대비 2배 이상 급증</div>
              </div>
            </div>
            <div className="flex items-start gap-2">
              <span>🚀</span>
              <div>
                <div className="font-medium text-white">거래량 가속</div>
                <div className="text-slate-400">거래량 증가 추세 감지</div>
              </div>
            </div>
            <div className="flex items-start gap-2">
              <span>🌡️</span>
              <div>
                <div className="font-medium text-white">과열 체크</div>
                <div className="text-slate-400">이미 급등한 종목 차단</div>
              </div>
            </div>
            <div className="flex items-start gap-2">
              <span>🛡️</span>
              <div>
                <div className="font-medium text-white">추격매수 방지</div>
                <div className="text-slate-400">고점에서 진입 방지</div>
              </div>
            </div>
          </div>
          <div className="mt-3 pt-3 border-t border-slate-700 text-xs text-slate-500">
            💡 6개 조건 중 많이 충족할수록 급등 가능성이 높습니다
          </div>
        </div>
      )}

      {/* Top 5 */}
      <div className="mb-4">
        <div className="flex items-center gap-2 mb-3">
          <span className="text-sm font-medium text-yellow-400">🏆 TOP 5</span>
          <span className="text-xs text-slate-500">급등 조건에 가장 근접</span>
        </div>
        <div className="flex gap-3 overflow-x-auto pb-2 scrollbar-thin scrollbar-thumb-slate-700">
          {top5.map((candidate, idx) => (
            <Top5Card key={candidate.symbol} candidate={candidate} rank={idx + 1} />
          ))}
        </div>
      </div>

      {/* 나머지 종목 */}
      {rest.length > 0 && (
        <div className="border-t border-slate-700 pt-4">
          <button
            onClick={() => setShowAll(!showAll)}
            className="flex items-center gap-2 text-sm text-slate-400 hover:text-white transition-colors mb-3"
          >
            <svg
              className={`w-4 h-4 transition-transform ${showAll ? 'rotate-180' : ''}`}
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
            </svg>
            {showAll ? '접기' : `나머지 ${rest.length}개 더 보기`}
          </button>

          {showAll && (
            <div className="space-y-2">
              {rest.map((candidate) => (
                <CandidateCard key={candidate.symbol} candidate={candidate} />
              ))}
            </div>
          )}
        </div>
      )}

      {/* 하단 안내 */}
      <div className="mt-4 pt-3 border-t border-slate-700">
        <div className="flex items-center justify-between text-xs text-slate-500">
          <span>점수 = 6개 조건의 가중 평균</span>
          <span>70점 이상만 표시</span>
        </div>
      </div>
    </div>
  )
}
