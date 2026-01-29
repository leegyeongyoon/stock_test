'use client'

import { StrategyPnlResponse } from '@/lib/analytics-api'

interface Props {
  data: StrategyPnlResponse | null
  loading?: boolean
}

export default function StrategyPnlCard({ data, loading }: Props) {
  if (loading) {
    return (
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {[1, 2].map((i) => (
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

  if (!data) {
    return (
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="bg-slate-800 rounded-lg border border-slate-700 p-6">
          <p className="text-slate-400">데이터 없음</p>
        </div>
      </div>
    )
  }

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
      {data.strategies.map((strategy) => {
        const isProfit = strategy.realized_pnl >= 0
        const strategyLabel =
          strategy.strategy === 'CORE'
            ? 'Core (캐시앤캐리)'
            : 'Satellite (모멘텀)'
        const borderColor =
          strategy.strategy === 'CORE'
            ? 'border-blue-500/50'
            : 'border-purple-500/50'
        const dotColor =
          strategy.strategy === 'CORE' ? 'bg-blue-500' : 'bg-purple-500'

        return (
          <div
            key={strategy.strategy}
            className={`bg-slate-800 rounded-lg border ${borderColor} p-6`}
          >
            <div className="flex items-center gap-2 mb-4">
              <span className={`w-3 h-3 rounded-full ${dotColor}`} />
              <h3 className="font-semibold">{strategyLabel}</h3>
            </div>

            <div
              className={`text-3xl font-bold mb-4 ${
                isProfit ? 'text-green-400' : 'text-red-400'
              }`}
            >
              {isProfit ? '+' : ''}${strategy.realized_pnl.toFixed(2)}
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
