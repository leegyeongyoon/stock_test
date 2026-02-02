'use client'

import { AttackStatus, AttackMode } from '@/lib/api'
import { formatKRW } from '@/lib/currency'

interface Props {
  status: AttackStatus | null
  loading?: boolean
}

const modeColors: Record<AttackMode, string> = {
  OFF: 'bg-gray-600',
  NORMAL: 'bg-blue-600',
  PLUS: 'bg-orange-600',
  MAX: 'bg-red-600',
}

const modeLabels: Record<AttackMode, string> = {
  OFF: '비활성',
  NORMAL: '일반',
  PLUS: '플러스',
  MAX: '최대',
}

export default function AttackStatusCard({ status, loading }: Props) {
  if (loading) {
    return (
      <div className="bg-slate-800 rounded-lg border border-slate-700 p-6">
        <div className="animate-pulse space-y-3">
          <div className="h-6 bg-slate-700 rounded w-1/2"></div>
          <div className="h-10 bg-slate-700 rounded"></div>
        </div>
      </div>
    )
  }

  if (!status) {
    return (
      <div className="bg-slate-800 rounded-lg border border-slate-700 p-6">
        <div className="flex items-center gap-2 mb-4">
          <span className="w-3 h-3 rounded-full bg-orange-500" />
          <h3 className="font-semibold">Attack 전략</h3>
        </div>
        <p className="text-slate-400">Attack 상태 조회 불가</p>
      </div>
    )
  }

  return (
    <div className="bg-slate-800 rounded-lg border border-orange-500/50 p-6">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <span className="w-3 h-3 rounded-full bg-orange-500" />
          <h3 className="font-semibold">Attack 전략</h3>
        </div>
        <span className={`px-3 py-1 rounded text-sm font-medium ${modeColors[status.mode]}`}>
          {modeLabels[status.mode]}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-4 text-sm">
        <div>
          <span className="text-slate-400 block">활성 포지션</span>
          <div className="font-medium text-lg">{status.active_positions}개</div>
        </div>
        <div>
          <span className="text-slate-400 block">대기 시그널</span>
          <div className="font-medium text-lg">{status.pending_signals}개</div>
        </div>
        <div>
          <span className="text-slate-400 block">오늘 거래</span>
          <div className="font-medium text-lg">{status.stats.today_trades}건</div>
        </div>
        <div>
          <span className="text-slate-400 block">오늘 손익</span>
          <div className={`font-medium text-lg ${status.stats.today_pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
            {status.stats.today_pnl >= 0 ? '+' : ''}{formatKRW(status.stats.today_pnl)}
          </div>
        </div>
      </div>

      {/* Gate Status */}
      <div className="mt-4 pt-4 border-t border-slate-700">
        <div className="flex items-center gap-2">
          <span className={`w-2 h-2 rounded-full ${status.gate_status.can_enter ? 'bg-green-500' : 'bg-red-500'}`} />
          <span className="text-sm text-slate-400">
            {status.gate_status.can_enter ? '진입 가능' : status.gate_status.reason || '진입 불가'}
          </span>
        </div>
      </div>
    </div>
  )
}
