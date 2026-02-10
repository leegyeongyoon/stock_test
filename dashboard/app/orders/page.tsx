'use client'

import { useEffect, useState, useCallback } from 'react'
import { api, Order } from '@/lib/api'
import ConnectionStatus from '@/components/ConnectionStatus'
import { formatKRW, formatUpbitPrice } from '@/lib/currency'

const statusColors: Record<string, string> = {
  NEW: 'bg-gray-600',
  SENT: 'bg-blue-600',
  ACK: 'bg-blue-500',
  PARTIAL: 'bg-yellow-600',
  FILLED: 'bg-green-600',
  CANCELED: 'bg-gray-500',
  REJECTED: 'bg-red-600',
}

export default function OrdersPage() {
  const [orders, setOrders] = useState<Order[]>([])
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState<string>('')

  const fetchOrders = useCallback(async () => {
    try {
      const data = await api.getOrders(filter || undefined, 200)
      setOrders(data)
    } catch (e) {
      console.error('Failed to fetch orders:', e)
    } finally {
      setLoading(false)
    }
  }, [filter])

  useEffect(() => {
    fetchOrders()
    const interval = setInterval(fetchOrders, 5000)
    return () => clearInterval(interval)
  }, [fetchOrders])

  const statusLabels: Record<string, string> = {
    NEW: '신규',
    SENT: '전송됨',
    ACK: '확인됨',
    PARTIAL: '부분체결',
    FILLED: '체결완료',
    CANCELED: '취소됨',
    REJECTED: '거부됨',
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-2xl font-bold">주문 내역</h1>
          <p className="text-slate-400 text-sm">최근 주문 기록</p>
        </div>
        <div className="flex items-center gap-4">
          <ConnectionStatus />
          <select
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            className="bg-slate-700 border border-slate-600 rounded px-3 py-2 text-sm"
          >
            <option value="">전체 상태</option>
            <option value="NEW">신규</option>
            <option value="SENT">전송됨</option>
            <option value="ACK">확인됨</option>
            <option value="PARTIAL">부분체결</option>
            <option value="FILLED">체결완료</option>
            <option value="CANCELED">취소됨</option>
            <option value="REJECTED">거부됨</option>
          </select>
        </div>
      </div>

      {/* Orders Table */}
      <div className="bg-slate-800 rounded-lg border border-slate-700 overflow-hidden">
        {loading ? (
          <div className="p-8 text-center">
            <div className="animate-spin w-8 h-8 border-2 border-blue-500 border-t-transparent rounded-full mx-auto"></div>
          </div>
        ) : orders.length === 0 ? (
          <div className="p-8 text-center text-slate-400">주문 내역이 없습니다</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead className="bg-slate-900/50">
                <tr>
                  <th className="text-left p-3 text-slate-400 font-medium">시간</th>
                  <th className="text-left p-3 text-slate-400 font-medium">종목</th>
                  <th className="text-left p-3 text-slate-400 font-medium">전략</th>
                  <th className="text-left p-3 text-slate-400 font-medium">방향</th>
                  <th className="text-left p-3 text-slate-400 font-medium">유형</th>
                  <th className="text-right p-3 text-slate-400 font-medium">수량</th>
                  <th className="text-right p-3 text-slate-400 font-medium">가격</th>
                  <th className="text-right p-3 text-slate-400 font-medium">체결량</th>
                  <th className="text-left p-3 text-slate-400 font-medium">상태</th>
                </tr>
              </thead>
              <tbody>
                {orders.map((order) => (
                  <tr
                    key={order.order_id}
                    className="border-t border-slate-700 hover:bg-slate-700/50 transition-colors"
                  >
                    <td className="p-3 text-sm text-slate-400">
                      {new Date(order.created_at).toLocaleString('ko-KR', {
                        timeZone: 'Asia/Seoul',
                        month: '2-digit',
                        day: '2-digit',
                        hour: '2-digit',
                        minute: '2-digit',
                        second: '2-digit',
                        hour12: false
                      })}
                    </td>
                    <td className="p-3 font-medium">{order.symbol}</td>
                    <td className="p-3">
                      <span
                        className={`px-2 py-1 rounded text-xs ${
                          order.strategy === 'VOLATILE_OVERSOLD_BOUNCE'
                            ? 'bg-blue-900/50 text-blue-400'
                            : order.strategy === 'CRASH_RECOVERY'
                            ? 'bg-orange-900/50 text-orange-400'
                            : order.strategy === 'TRIPLE_BEARISH_REVERSAL'
                            ? 'bg-purple-900/50 text-purple-400'
                            : 'bg-slate-900/50 text-slate-400'
                        }`}
                      >
                        {order.strategy}
                      </span>
                    </td>
                    <td className="p-3">
                      <span
                        className={
                          order.side === 'BUY' ? 'text-green-400' : 'text-red-400'
                        }
                      >
                        {order.side === 'BUY' ? '매수' : '매도'}
                      </span>
                    </td>
                    <td className="p-3 text-slate-400">{order.order_type === 'MARKET' ? '시장가' : order.order_type === 'LIMIT' ? '지정가' : order.order_type}</td>
                    <td className="p-3 text-right font-mono">{order.quantity.toFixed(4)}</td>
                    <td className="p-3 text-right font-mono">
                      {order.price ? formatUpbitPrice(order.price) : '시장가'}
                    </td>
                    <td className="p-3 text-right font-mono">
                      {order.filled_quantity.toFixed(4)}
                      {order.avg_fill_price && (
                        <span className="text-slate-500 ml-1">
                          @{formatUpbitPrice(order.avg_fill_price)}
                        </span>
                      )}
                    </td>
                    <td className="p-3">
                      <span
                        className={`px-2 py-1 rounded text-xs text-white ${
                          statusColors[order.status] || 'bg-gray-600'
                        }`}
                      >
                        {statusLabels[order.status] || order.status}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
