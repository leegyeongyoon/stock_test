'use client'

import { AttackScore } from '@/lib/api'

interface Props {
  score: AttackScore | null
  loading?: boolean
}

const levelColors: Record<string, string> = {
  L1: 'text-yellow-400',
  L2: 'text-orange-400',
  L3: 'text-red-400',
  BLOCKED: 'text-gray-400',
}

const levelBgColors: Record<string, string> = {
  L1: 'bg-yellow-600',
  L2: 'bg-orange-600',
  L3: 'bg-red-600',
  BLOCKED: 'bg-gray-600',
}

const levelLabels: Record<string, string> = {
  L1: 'Level 1 (10%)',
  L2: 'Level 2 (30%)',
  L3: 'Level 3 (50%)',
  BLOCKED: '진입 불가',
}

export default function AttackScoreDisplay({ score, loading }: Props) {
  if (loading) {
    return (
      <div className="bg-slate-800 rounded-lg border border-slate-700 p-4">
        <div className="animate-pulse space-y-3">
          <div className="h-6 bg-slate-700 rounded w-1/2"></div>
          <div className="h-20 bg-slate-700 rounded"></div>
        </div>
      </div>
    )
  }

  if (!score) {
    return null
  }

  const level = score.level || 'BLOCKED'
  const levelColor = levelColors[level] || levelColors.BLOCKED
  const levelBgColor = levelBgColors[level] || levelBgColors.BLOCKED
  const levelLabel = levelLabels[level] || level

  return (
    <div className="bg-slate-800 rounded-lg border border-slate-700 p-4">
      <div className="flex items-center justify-between mb-4">
        <h4 className="font-medium">{score.symbol} Attack Score</h4>
        <span className={`px-2 py-1 rounded text-xs font-medium ${levelBgColor}`}>
          {levelLabel}
        </span>
      </div>

      {/* Total Score Bar */}
      <div className="mb-4">
        <div className="flex justify-between text-sm mb-1">
          <span className="text-slate-400">총점</span>
          <span className={`font-bold ${levelColor}`}>
            {score.total_score.toFixed(1)}
          </span>
        </div>
        <div className="h-3 bg-slate-700 rounded-full overflow-hidden">
          <div
            className={`h-full transition-all ${levelBgColor}`}
            style={{ width: `${Math.min(score.total_score, 100)}%` }}
          />
        </div>
      </div>

      {/* Component Breakdown */}
      <div className="space-y-2">
        {score.components.map((component) => (
          <div key={component.name} className="text-sm">
            <div className="flex justify-between">
              <span className="text-slate-400">{component.name}</span>
              <span className="font-mono">
                {component.weighted_value.toFixed(1)}
                <span className="text-slate-500 text-xs ml-1">
                  ({component.value.toFixed(1)} x {component.weight})
                </span>
              </span>
            </div>
            <div className="h-1.5 bg-slate-700 rounded-full overflow-hidden mt-1">
              <div
                className="h-full bg-orange-500"
                style={{ width: `${Math.min(Math.abs(component.value), 100)}%` }}
              />
            </div>
          </div>
        ))}
      </div>

      {/* Target Allocation */}
      <div className="mt-4 pt-4 border-t border-slate-700">
        <div className="flex justify-between">
          <span className="text-slate-400">목표 배분</span>
          <span className="font-medium">{(score.target_allocation * 100).toFixed(1)}%</span>
        </div>
      </div>
    </div>
  )
}
