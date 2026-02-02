'use client'

import { AttackPosition, AttackSignal } from '@/lib/api'
import { formatKRW, formatUpbitPrice, formatQuantity } from '@/lib/currency'

interface Props {
  positions: AttackPosition[]
  signals: AttackSignal[]
  loading?: boolean
}

const levelColors: Record<string, string> = {
  L1: 'bg-yellow-600',
  L2: 'bg-orange-600',
  L3: 'bg-red-600',
}

const statusColors: Record<string, string> = {
  CONFIRMED: 'bg-green-600',
  PENDING: 'bg-yellow-600',
  EXPIRED: 'bg-gray-600',
}

const statusLabels: Record<string, string> = {
  CONFIRMED: '확정',
  PENDING: '대기',
  EXPIRED: '만료',
}

export default function AttackPositionsTable({ positions, signals, loading }: Props) {
  if (loading) {
    return (
      <div className="bg-slate-800 rounded-lg border border-slate-700 p-4">
        <h2 className="text-lg font-semibold mb-4">Attack 포지션</h2>
        <div className="animate-pulse space-y-3">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-10 bg-slate-700 rounded"></div>
          ))}
        </div>
      </div>
    )
  }

  return (
    <div className="bg-slate-800 rounded-lg border border-orange-500/30 overflow-hidden">
      <div className="p-4 border-b border-slate-700">
        <h2 className="text-lg font-semibold">Attack 포지션 & 시그널</h2>
      </div>

      {/* Pending Signals */}
      {signals.length > 0 && (
        <div className="p-4 bg-orange-900/20 border-b border-slate-700">
          <h3 className="text-sm font-medium text-orange-400 mb-2">대기 중인 시그널</h3>
          <div className="space-y-2">
            {signals.map((signal) => (
              <div key={signal.symbol} className="flex items-center justify-between text-sm">
                <div className="flex items-center gap-2">
                  <span className="font-mono">{signal.symbol}</span>
                  <span className={`px-1.5 py-0.5 rounded text-xs ${levelColors[signal.level] || 'bg-gray-600'}`}>
                    {signal.level}
                  </span>
                </div>
                <div className="flex items-center gap-2 text-slate-400">
                  <span>Score: {signal.score.toFixed(1)}</span>
                  <span className={`px-1.5 py-0.5 rounded text-xs ${statusColors[signal.status]}`}>
                    {statusLabels[signal.status] || signal.status}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Active Positions */}
      {positions.length === 0 ? (
        <div className="p-8 text-center text-slate-400">
          활성 Attack 포지션이 없습니다
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead className="bg-slate-900/50">
              <tr>
                <th className="text-left p-3 text-slate-400 font-medium">종목</th>
                <th className="text-left p-3 text-slate-400 font-medium">레벨</th>
                <th className="text-right p-3 text-slate-400 font-medium">수량</th>
                <th className="text-right p-3 text-slate-400 font-medium">평균가</th>
                <th className="text-right p-3 text-slate-400 font-medium">현재가</th>
                <th className="text-right p-3 text-slate-400 font-medium">미실현</th>
                <th className="text-right p-3 text-slate-400 font-medium">%</th>
              </tr>
            </thead>
            <tbody>
              {positions.map((pos) => (
                <tr
                  key={pos.symbol}
                  className="border-t border-slate-700 hover:bg-slate-700/50 transition-colors"
                >
                  <td className="p-3 font-medium font-mono">{pos.symbol}</td>
                  <td className="p-3">
                    <span className={`px-2 py-1 rounded text-xs ${levelColors[pos.attack_level] || 'bg-gray-600'}`}>
                      {pos.attack_level}
                    </span>
                  </td>
                  <td className="p-3 text-right font-mono">{formatQuantity(pos.quantity)}</td>
                  <td className="p-3 text-right font-mono">{formatUpbitPrice(pos.avg_price)}</td>
                  <td className="p-3 text-right font-mono">{formatUpbitPrice(pos.current_price)}</td>
                  <td className={`p-3 text-right font-mono ${pos.unrealized_pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                    {pos.unrealized_pnl >= 0 ? '+' : ''}{formatKRW(pos.unrealized_pnl)}
                  </td>
                  <td className={`p-3 text-right font-mono font-bold ${pos.unrealized_pnl_pct >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                    {pos.unrealized_pnl_pct >= 0 ? '+' : ''}{pos.unrealized_pnl_pct.toFixed(2)}%
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
