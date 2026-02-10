'use client'

import { Position } from '@/lib/api'
import { formatKRW, formatUpbitPrice, formatQuantity } from '@/lib/currency'

interface Props {
  positions: Position[]
  loading?: boolean
}

function getStrategyStyle(strategy: string): { bg: string; text: string } {
  switch (strategy) {
    case 'VOLATILE_OVERSOLD_BOUNCE':
      return { bg: 'bg-blue-900/50', text: 'text-blue-400' }
    case 'CRASH_RECOVERY':
      return { bg: 'bg-orange-900/50', text: 'text-orange-400' }
    case 'TRIPLE_BEARISH_REVERSAL':
      return { bg: 'bg-purple-900/50', text: 'text-purple-400' }
    default:
      return { bg: 'bg-slate-900/50', text: 'text-slate-400' }
  }
}

export default function PositionsTable({ positions, loading }: Props) {
  if (loading) {
    return (
      <div className="bg-slate-800 rounded-lg border border-slate-700 p-4">
        <h2 className="text-lg font-semibold mb-4">보유 포지션</h2>
        <div className="animate-pulse space-y-3">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-10 bg-slate-700 rounded"></div>
          ))}
        </div>
      </div>
    )
  }

  return (
    <div className="bg-slate-800 rounded-lg border border-slate-700 overflow-hidden">
      <div className="p-4 border-b border-slate-700">
        <h2 className="text-lg font-semibold">보유 포지션</h2>
      </div>

      {positions.length === 0 ? (
        <div className="p-8 text-center text-slate-400">
          보유 포지션이 없습니다
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead className="bg-slate-900/50">
              <tr>
                <th className="text-left p-3 text-slate-400 font-medium">종목</th>
                <th className="text-left p-3 text-slate-400 font-medium">전략</th>
                <th className="text-right p-3 text-slate-400 font-medium">수량</th>
                <th className="text-right p-3 text-slate-400 font-medium">평균가</th>
                <th className="text-right p-3 text-slate-400 font-medium">현재가</th>
                <th className="text-right p-3 text-slate-400 font-medium">미실현</th>
                <th className="text-right p-3 text-slate-400 font-medium">%</th>
                <th className="text-right p-3 text-slate-400 font-medium">금액</th>
              </tr>
            </thead>
            <tbody>
              {positions.map((pos, idx) => {
                const style = getStrategyStyle(pos.strategy)
                const costBasis = pos.avg_price * pos.quantity
                const pnlPct = costBasis > 0 ? (pos.unrealized_pnl / costBasis) * 100 : 0

                return (
                  <tr
                    key={`${pos.symbol}-${idx}`}
                    className="border-t border-slate-700 hover:bg-slate-700/50 transition-colors"
                  >
                    <td className="p-3 font-medium font-mono">{pos.symbol}</td>
                    <td className="p-3">
                      <span className={`px-2 py-1 rounded text-xs ${style.bg} ${style.text}`}>
                        {pos.strategy}
                      </span>
                    </td>
                    <td className="p-3 text-right font-mono">{formatQuantity(pos.quantity)}</td>
                    <td className="p-3 text-right font-mono">
                      {formatUpbitPrice(pos.avg_price)}
                    </td>
                    <td className="p-3 text-right font-mono">
                      {formatUpbitPrice(pos.current_price)}
                    </td>
                    <td
                      className={`p-3 text-right font-mono ${
                        pos.unrealized_pnl >= 0 ? 'text-green-400' : 'text-red-400'
                      }`}
                    >
                      {pos.unrealized_pnl >= 0 ? '+' : ''}
                      {formatKRW(pos.unrealized_pnl)}
                    </td>
                    <td
                      className={`p-3 text-right font-mono font-bold ${
                        pnlPct >= 0 ? 'text-green-400' : 'text-red-400'
                      }`}
                    >
                      {pnlPct >= 0 ? '+' : ''}{pnlPct.toFixed(2)}%
                    </td>
                    <td className="p-3 text-right font-mono">
                      {formatKRW(pos.notional)}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
