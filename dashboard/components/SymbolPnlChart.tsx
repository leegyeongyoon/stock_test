'use client'

import { SymbolPnlResponse } from '@/lib/analytics-api'
import { formatKRW } from '@/lib/currency'

interface Props {
  data: SymbolPnlResponse | null
  loading?: boolean
}

export default function SymbolPnlChart({ data, loading }: Props) {
  if (loading) {
    return (
      <div className="bg-slate-800 rounded-lg border border-slate-700 p-6">
        <div className="animate-pulse space-y-3">
          <div className="h-6 bg-slate-700 rounded w-1/3"></div>
          {[1, 2, 3, 4, 5].map((i) => (
            <div key={i} className="h-8 bg-slate-700 rounded"></div>
          ))}
        </div>
      </div>
    )
  }

  if (!data || data.symbols.length === 0) {
    return (
      <div className="bg-slate-800 rounded-lg border border-slate-700 p-6">
        <h3 className="text-lg font-semibold mb-4">종목별 수익</h3>
        <p className="text-slate-400">거래 기록 없음</p>
      </div>
    )
  }

  const maxPnl = Math.max(...data.symbols.map((s) => Math.abs(s.total_pnl)), 1)

  return (
    <div className="bg-slate-800 rounded-lg border border-slate-700 p-6">
      <h3 className="text-lg font-semibold mb-4">종목별 수익</h3>

      <div className="space-y-3">
        {data.symbols.slice(0, 10).map((symbol) => {
          const barWidth = (Math.abs(symbol.total_pnl) / maxPnl) * 100
          const isProfit = symbol.total_pnl >= 0

          return (
            <div key={symbol.symbol} className="flex items-center gap-3">
              <div className="w-24 font-mono text-sm truncate" title={symbol.symbol}>
                {symbol.symbol}
              </div>
              <div className="flex-1 h-6 bg-slate-700 rounded overflow-hidden relative">
                <div
                  className={`h-full transition-all ${
                    isProfit ? 'bg-green-600' : 'bg-red-600'
                  }`}
                  style={{ width: `${Math.max(barWidth, 2)}%` }}
                />
              </div>
              <div
                className={`w-28 text-right font-mono text-sm ${
                  isProfit ? 'text-green-400' : 'text-red-400'
                }`}
              >
                {isProfit ? '+' : ''}{formatKRW(symbol.total_pnl)}
              </div>
              <div className="w-16 text-right text-slate-400 text-sm">
                {symbol.trades_count}건
              </div>
            </div>
          )
        })}
      </div>

      <div className="mt-4 pt-4 border-t border-slate-700 flex justify-between text-sm">
        <span className="text-slate-400">총 실현 수익</span>
        <span
          className={data.total_realized >= 0 ? 'text-green-400' : 'text-red-400'}
        >
          {data.total_realized >= 0 ? '+' : ''}{formatKRW(data.total_realized)}
        </span>
      </div>
    </div>
  )
}
