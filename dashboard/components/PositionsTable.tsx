'use client'

import { Position } from '@/lib/api'

interface Props {
  positions: Position[]
  loading?: boolean
}

export default function PositionsTable({ positions, loading }: Props) {
  if (loading) {
    return (
      <div className="bg-slate-800 rounded-lg border border-slate-700 p-4">
        <h2 className="text-lg font-semibold mb-4">Positions</h2>
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
        <h2 className="text-lg font-semibold">Positions</h2>
      </div>

      {positions.length === 0 ? (
        <div className="p-8 text-center text-slate-400">
          No open positions
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead className="bg-slate-900/50">
              <tr>
                <th className="text-left p-3 text-slate-400 font-medium">Symbol</th>
                <th className="text-left p-3 text-slate-400 font-medium">Strategy</th>
                <th className="text-left p-3 text-slate-400 font-medium">Side</th>
                <th className="text-right p-3 text-slate-400 font-medium">Qty</th>
                <th className="text-right p-3 text-slate-400 font-medium">Avg Price</th>
                <th className="text-right p-3 text-slate-400 font-medium">Current</th>
                <th className="text-right p-3 text-slate-400 font-medium">uPnL</th>
                <th className="text-right p-3 text-slate-400 font-medium">%</th>
                <th className="text-right p-3 text-slate-400 font-medium">Notional</th>
              </tr>
            </thead>
            <tbody>
              {positions.map((pos, idx) => (
                <tr
                  key={`${pos.symbol}-${idx}`}
                  className="border-t border-slate-700 hover:bg-slate-700/50 transition-colors"
                >
                  <td className="p-3 font-medium">{pos.symbol}</td>
                  <td className="p-3">
                    <span
                      className={`px-2 py-1 rounded text-xs ${
                        pos.strategy === 'CORE'
                          ? 'bg-blue-900/50 text-blue-400'
                          : 'bg-purple-900/50 text-purple-400'
                      }`}
                    >
                      {pos.strategy}
                    </span>
                  </td>
                  <td className="p-3">
                    <span
                      className={
                        pos.side === 'BUY' ? 'text-green-400' : 'text-red-400'
                      }
                    >
                      {pos.side === 'BUY' ? 'LONG' : 'SHORT'}
                    </span>
                  </td>
                  <td className="p-3 text-right font-mono">{pos.quantity.toFixed(4)}</td>
                  <td className="p-3 text-right font-mono">
                    ${pos.avg_price.toLocaleString(undefined, { minimumFractionDigits: 2 })}
                  </td>
                  <td className="p-3 text-right font-mono">
                    ${pos.current_price.toLocaleString(undefined, { minimumFractionDigits: 2 })}
                  </td>
                  <td
                    className={`p-3 text-right font-mono ${
                      pos.unrealized_pnl >= 0 ? 'text-green-400' : 'text-red-400'
                    }`}
                  >
                    {pos.unrealized_pnl >= 0 ? '+' : ''}
                    ${pos.unrealized_pnl.toFixed(2)}
                  </td>
                  <td
                    className={`p-3 text-right font-mono font-bold ${
                      pos.unrealized_pnl >= 0 ? 'text-green-400' : 'text-red-400'
                    }`}
                  >
                    {(() => {
                      const costBasis = pos.avg_price * pos.quantity
                      const pnlPct = costBasis > 0 ? (pos.unrealized_pnl / costBasis) * 100 : 0
                      return `${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(2)}%`
                    })()}
                  </td>
                  <td className="p-3 text-right font-mono">
                    ${pos.notional.toLocaleString(undefined, { minimumFractionDigits: 0 })}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
