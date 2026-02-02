'use client'

import { PeriodReturnsResponse } from '@/lib/analytics-api'
import { formatKRW } from '@/lib/currency'

interface Props {
  data: PeriodReturnsResponse | null
  loading?: boolean
}

export default function PeriodReturns({ data, loading }: Props) {
  if (loading) {
    return (
      <div className="bg-slate-800 rounded-lg border border-slate-700 p-6">
        <div className="animate-pulse space-y-4">
          <div className="h-6 bg-slate-700 rounded w-1/3"></div>
          <div className="grid grid-cols-4 gap-4">
            {[1, 2, 3, 4].map((i) => (
              <div key={i} className="h-16 bg-slate-700 rounded"></div>
            ))}
          </div>
        </div>
      </div>
    )
  }

  if (!data) {
    return (
      <div className="bg-slate-800 rounded-lg border border-slate-700 p-6">
        <h3 className="text-lg font-semibold mb-4">수익률 요약</h3>
        <p className="text-slate-400">데이터 없음</p>
      </div>
    )
  }

  const pnlColor = data.total_pnl >= 0 ? 'text-green-400' : 'text-red-400'

  return (
    <div className="bg-slate-800 rounded-lg border border-slate-700 p-6">
      <h3 className="text-lg font-semibold mb-4">수익률 요약</h3>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        <div className="bg-slate-700/50 rounded-lg p-4">
          <div className="text-slate-400 text-sm mb-1">총 수익</div>
          <div className={`text-2xl font-bold ${pnlColor}`}>
            {data.total_pnl >= 0 ? '+' : ''}{formatKRW(data.total_pnl)}
          </div>
        </div>
        <div className="bg-slate-700/50 rounded-lg p-4">
          <div className="text-slate-400 text-sm mb-1">수익률</div>
          <div className={`text-2xl font-bold ${pnlColor}`}>
            {data.total_pnl_pct >= 0 ? '+' : ''}
            {(data.total_pnl_pct * 100).toFixed(2)}%
          </div>
        </div>
        <div className="bg-slate-700/50 rounded-lg p-4">
          <div className="text-slate-400 text-sm mb-1">최대 드로다운</div>
          <div className="text-2xl font-bold text-red-400">
            {(data.max_drawdown * 100).toFixed(2)}%
          </div>
        </div>
        <div className="bg-slate-700/50 rounded-lg p-4">
          <div className="text-slate-400 text-sm mb-1">승률</div>
          <div className="text-2xl font-bold text-white">
            {(data.win_rate * 100).toFixed(1)}%
          </div>
        </div>
      </div>

      <div className="text-sm text-slate-400">
        {data.start_date} ~ {data.end_date}
      </div>
    </div>
  )
}
