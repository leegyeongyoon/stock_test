'use client'

import { HourlyPnlResponse } from '@/lib/analytics-api'
import { formatKRW } from '@/lib/currency'

interface Props {
  data: HourlyPnlResponse | null
  loading?: boolean
}

export default function HourlyPnlChart({ data, loading }: Props) {
  if (loading) {
    return (
      <div className="bg-slate-800 rounded-lg border border-slate-700 p-6">
        <div className="animate-pulse">
          <div className="h-6 bg-slate-700 rounded w-1/3 mb-4"></div>
          <div className="h-48 bg-slate-700 rounded"></div>
        </div>
      </div>
    )
  }

  if (!data || !data.hourly_data || data.hourly_data.length === 0) {
    return (
      <div className="bg-slate-800 rounded-lg border border-slate-700 p-6">
        <h3 className="text-lg font-semibold mb-4">시간대별 수익</h3>
        <div className="h-48 flex items-center justify-center text-slate-400">
          데이터 없음
        </div>
      </div>
    )
  }

  const maxPnl = Math.max(...data.hourly_data.map((h) => Math.abs(h.pnl)), 1)

  return (
    <div className="bg-slate-800 rounded-lg border border-slate-700 p-6">
      <h3 className="text-lg font-semibold mb-4">시간대별 수익 (UTC)</h3>

      <div className="flex items-end gap-1 h-48">
        {data.hourly_data.map((hourData) => {
          const height = (Math.abs(hourData.pnl) / maxPnl) * 100
          const isProfit = hourData.pnl >= 0
          const isBest = hourData.hour === data.best_hour && hourData.pnl > 0
          const isWorst = hourData.hour === data.worst_hour && hourData.pnl < 0

          let barColor = isProfit ? 'bg-green-600/60' : 'bg-red-600/60'
          if (isBest) barColor = 'bg-green-500'
          if (isWorst) barColor = 'bg-red-500'

          return (
            <div
              key={hourData.hour}
              className="flex-1 flex flex-col items-center gap-1"
            >
              <div className="w-full flex-1 flex items-end">
                <div
                  className={`w-full rounded-t transition-all ${barColor}`}
                  style={{ height: `${Math.max(height, 4)}%` }}
                  title={`${hourData.hour}:00 - ${formatKRW(hourData.pnl)} (${hourData.trades_count}건)`}
                />
              </div>
              {hourData.hour % 4 === 0 && (
                <span className="text-xs text-slate-500">{hourData.hour}</span>
              )}
            </div>
          )
        })}
      </div>

      <div className="mt-4 flex justify-between text-sm">
        <div>
          <span className="text-slate-400">최고 시간대: </span>
          <span className="text-green-400 font-medium">
            {data.best_hour}:00 UTC
          </span>
        </div>
        <div>
          <span className="text-slate-400">최저 시간대: </span>
          <span className="text-red-400 font-medium">
            {data.worst_hour}:00 UTC
          </span>
        </div>
      </div>
    </div>
  )
}
