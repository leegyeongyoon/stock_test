'use client'

import { PeriodType } from '@/lib/analytics-api'

interface Props {
  value: PeriodType
  onChange: (period: PeriodType) => void
}

const periods: { value: PeriodType; label: string }[] = [
  { value: '1w', label: '1주일' },
  { value: '1m', label: '1개월' },
  { value: '3m', label: '3개월' },
  { value: '6m', label: '6개월' },
  { value: '1y', label: '1년' },
]

export default function PeriodSelector({ value, onChange }: Props) {
  return (
    <div className="flex gap-2">
      {periods.map((p) => (
        <button
          key={p.value}
          onClick={() => onChange(p.value)}
          className={`px-3 py-1.5 rounded text-sm font-medium transition-colors ${
            value === p.value
              ? 'bg-blue-600 text-white'
              : 'bg-slate-700 text-slate-300 hover:bg-slate-600'
          }`}
        >
          {p.label}
        </button>
      ))}
    </div>
  )
}
