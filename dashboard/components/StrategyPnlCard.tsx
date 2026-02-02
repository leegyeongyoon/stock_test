'use client'

import { StrategyPnlResponse } from '@/lib/analytics-api'
import { formatKRW } from '@/lib/currency'

interface Props {
  data: StrategyPnlResponse | null
  loading?: boolean
}

function getStrategyLabel(strategy: string): string {
  switch (strategy) {
    case 'CORE':
      return 'Core (방어형 현금비중)'
    case 'SATELLITE':
      return 'Satellite (모멘텀)'
    case 'ATTACK':
      return 'Attack (공격형)'
    default:
      return strategy
  }
}

function getStrategyStyle(strategy: string): { border: string; dot: string } {
  switch (strategy) {
    case 'CORE':
      return { border: 'border-blue-500/50', dot: 'bg-blue-500' }
    case 'SATELLITE':
      return { border: 'border-purple-500/50', dot: 'bg-purple-500' }
    case 'ATTACK':
      return { border: 'border-orange-500/50', dot: 'bg-orange-500' }
    default:
      return { border: 'border-slate-500/50', dot: 'bg-slate-500' }
  }
}

export default function StrategyPnlCard({ data, loading }: Props) {
  if (loading) {
    return (
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {[1, 2, 3].map((i) => (
          <div
            key={i}
            className="bg-slate-800 rounded-lg border border-slate-700 p-6"
          >
            <div className="animate-pulse space-y-3">
              <div className="h-6 bg-slate-700 rounded w-1/2"></div>
              <div className="h-10 bg-slate-700 rounded"></div>
              <div className="grid grid-cols-2 gap-4">
                <div className="h-12 bg-slate-700 rounded"></div>
                <div className="h-12 bg-slate-700 rounded"></div>
              </div>
            </div>
          </div>
        ))}
      </div>
    )
  }

  if (!data || !data.strategies || data.strategies.length === 0) {
    return (
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        <div className="bg-slate-800 rounded-lg border border-slate-700 p-6">
          <p className="text-slate-400">데이터 없음</p>
        </div>
      </div>
    )
  }

  // Determine grid columns based on number of strategies
  const gridCols = data.strategies.length <= 2
    ? 'grid-cols-1 md:grid-cols-2'
    : 'grid-cols-1 md:grid-cols-2 lg:grid-cols-3'

  return (
    <div className={`grid ${gridCols} gap-4`}>
      {data.strategies.map((strategy) => {
        const isProfit = strategy.realized_pnl >= 0
        const strategyLabel = getStrategyLabel(strategy.strategy)
        const style = getStrategyStyle(strategy.strategy)

        return (
          <div
            key={strategy.strategy}
            className={`bg-slate-800 rounded-lg border ${style.border} p-6`}
          >
            <div className="flex items-center gap-2 mb-4">
              <span className={`w-3 h-3 rounded-full ${style.dot}`} />
              <h3 className="font-semibold">{strategyLabel}</h3>
            </div>

            <div
              className={`text-3xl font-bold mb-4 ${
                isProfit ? 'text-green-400' : 'text-red-400'
              }`}
            >
              {isProfit ? '+' : ''}{formatKRW(strategy.realized_pnl)}
            </div>

            <div className="grid grid-cols-2 gap-4 text-sm">
              <div>
                <span className="text-slate-400 block">거래 수</span>
                <div className="font-medium text-lg">{strategy.trades_count}건</div>
              </div>
              <div>
                <span className="text-slate-400 block">승률</span>
                <div className="font-medium text-lg">
                  {(strategy.win_rate * 100).toFixed(1)}%
                </div>
              </div>
            </div>
          </div>
        )
      })}
    </div>
  )
}
