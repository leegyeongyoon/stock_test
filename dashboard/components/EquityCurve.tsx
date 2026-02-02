'use client'

import { EquityCurveResponse } from '@/lib/analytics-api'
import { formatKRW } from '@/lib/currency'

interface Props {
  data: EquityCurveResponse | null
  loading?: boolean
}

export default function EquityCurve({ data, loading }: Props) {
  if (loading) {
    return (
      <div className="bg-slate-800 rounded-lg border border-slate-700 p-6">
        <div className="animate-pulse">
          <div className="h-6 bg-slate-700 rounded w-1/4 mb-4"></div>
          <div className="h-64 bg-slate-700 rounded"></div>
        </div>
      </div>
    )
  }

  if (!data || !data.data || data.data.length === 0) {
    return (
      <div className="bg-slate-800 rounded-lg border border-slate-700 p-6">
        <h3 className="text-lg font-semibold mb-4">자산 곡선</h3>
        <div className="h-64 flex items-center justify-center text-slate-400">
          데이터 없음
        </div>
      </div>
    )
  }

  const points = data.data
  const minEquity = Math.min(...points.map((p) => p.equity))
  const maxEquity = Math.max(...points.map((p) => p.equity))
  const range = maxEquity - minEquity || 1

  const width = 100
  const height = 100
  const padding = 5

  const pathData = points
    .map((p, i) => {
      const x = padding + (i / (points.length - 1)) * (width - 2 * padding)
      const y =
        padding +
        (height - 2 * padding) -
        ((p.equity - minEquity) / range) * (height - 2 * padding)
      return `${i === 0 ? 'M' : 'L'} ${x} ${y}`
    })
    .join(' ')

  const areaPath =
    pathData +
    ` L ${width - padding} ${height - padding} L ${padding} ${height - padding} Z`

  const returnColor =
    data.total_return_pct >= 0 ? 'text-green-400' : 'text-red-400'
  const strokeColor = data.total_return_pct >= 0 ? '#4ade80' : '#f87171'
  const fillColor =
    data.total_return_pct >= 0 ? 'rgba(74, 222, 128, 0.1)' : 'rgba(248, 113, 113, 0.1)'

  return (
    <div className="bg-slate-800 rounded-lg border border-slate-700 p-6">
      <div className="flex justify-between items-center mb-4">
        <h3 className="text-lg font-semibold">자산 곡선</h3>
        <div className={`text-xl font-bold ${returnColor}`}>
          {data.total_return_pct >= 0 ? '+' : ''}
          {(data.total_return_pct * 100).toFixed(2)}%
        </div>
      </div>

      <svg viewBox={`0 0 ${width} ${height}`} className="w-full h-64">
        <defs>
          <linearGradient id="areaGradient" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={strokeColor} stopOpacity="0.3" />
            <stop offset="100%" stopColor={strokeColor} stopOpacity="0" />
          </linearGradient>
        </defs>
        <path d={areaPath} fill="url(#areaGradient)" />
        <path d={pathData} fill="none" stroke={strokeColor} strokeWidth="0.8" />
      </svg>

      <div className="flex justify-between text-sm text-slate-400 mt-2">
        <span>{formatKRW(data.start_equity)}</span>
        <span>{formatKRW(data.end_equity)}</span>
      </div>
    </div>
  )
}
