'use client'

import { Summary } from '@/lib/api'
import { formatKRW, formatKRWCompact } from '@/lib/currency'

interface Props {
  summary: Summary | null
  loading?: boolean
}

interface CardProps {
  title: string
  value: string
  subValue?: string
  color?: 'default' | 'profit' | 'loss' | 'warning'
  loading?: boolean
}

function Card({ title, value, subValue, color = 'default', loading }: CardProps) {
  const colorClasses = {
    default: 'text-white',
    profit: 'text-green-400',
    loss: 'text-red-400',
    warning: 'text-yellow-400',
  }

  if (loading) {
    return (
      <div className="bg-slate-800 rounded-lg p-4 border border-slate-700">
        <div className="animate-pulse">
          <div className="h-4 bg-slate-700 rounded w-20 mb-2"></div>
          <div className="h-8 bg-slate-700 rounded w-32"></div>
        </div>
      </div>
    )
  }

  return (
    <div className="bg-slate-800 rounded-lg p-4 border border-slate-700">
      <div className="text-slate-400 text-sm mb-1">{title}</div>
      <div className={`text-2xl font-bold ${colorClasses[color]}`}>{value}</div>
      {subValue && (
        <div className="text-slate-500 text-sm mt-1">{subValue}</div>
      )}
    </div>
  )
}

export default function SummaryCards({ summary, loading }: Props) {
  if (loading || !summary) {
    return (
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        {[1, 2, 3, 4].map((i) => (
          <Card key={i} title="" value="" loading />
        ))}
      </div>
    )
  }

  const pnlColor = summary.pnl_today >= 0 ? 'profit' : 'loss'
  const modeColor = {
    NORMAL: 'profit' as const,
    SAFE: 'warning' as const,
    HALT: 'loss' as const,
  }[summary.mode]

  const modeLabel = {
    NORMAL: '정상',
    SAFE: '안전',
    HALT: '중단',
  }[summary.mode] || summary.mode

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
      <Card
        title="총 자산"
        value={formatKRW(summary.equity)}
        subValue={summary.is_paper ? '테스트 모드' : '실거래'}
      />
      <Card
        title="오늘 손익"
        value={formatKRW(summary.pnl_today)}
        subValue={`${summary.pnl_today_pct >= 0 ? '+' : ''}${(summary.pnl_today_pct * 100).toFixed(2)}%`}
        color={pnlColor}
      />
      <Card
        title="낙폭 (MDD)"
        value={`${(summary.drawdown * 100).toFixed(2)}%`}
        subValue={`익스포져: ${formatKRWCompact(summary.exposure)}`}
        color={summary.drawdown < -0.02 ? 'loss' : 'default'}
      />
      <Card
        title="운영 모드"
        value={modeLabel}
        subValue={`현금: ${formatKRWCompact(summary.cash)}`}
        color={modeColor}
      />
    </div>
  )
}
