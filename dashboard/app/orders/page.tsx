'use client'

import { useEffect, useState, useCallback } from 'react'
import { api, Order } from '@/lib/api'
import ConnectionStatus from '@/components/ConnectionStatus'

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

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-2xl font-bold">Orders</h1>
          <p className="text-slate-400 text-sm">Recent order history</p>
        </div>
        <div className="flex items-center gap-4">
          <ConnectionStatus />
          <select
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            className="bg-slate-700 border border-slate-600 rounded px-3 py-2 text-sm"
          >
            <option value="">All Status</option>
            <option value="NEW">New</option>
            <option value="SENT">Sent</option>
            <option value="ACK">Acknowledged</option>
            <option value="PARTIAL">Partial</option>
            <option value="FILLED">Filled</option>
            <option value="CANCELED">Canceled</option>
            <option value="REJECTED">Rejected</option>
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
          <div className="p-8 text-center text-slate-400">No orders found</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead className="bg-slate-900/50">
                <tr>
                  <th className="text-left p-3 text-slate-400 font-medium">Time</th>
                  <th className="text-left p-3 text-slate-400 font-medium">Symbol</th>
                  <th className="text-left p-3 text-slate-400 font-medium">Strategy</th>
                  <th className="text-left p-3 text-slate-400 font-medium">Side</th>
                  <th className="text-left p-3 text-slate-400 font-medium">Type</th>
                  <th className="text-right p-3 text-slate-400 font-medium">Qty</th>
                  <th className="text-right p-3 text-slate-400 font-medium">Price</th>
                  <th className="text-right p-3 text-slate-400 font-medium">Filled</th>
                  <th className="text-left p-3 text-slate-400 font-medium">Status</th>
                </tr>
              </thead>
              <tbody>
                {orders.map((order) => (
                  <tr
                    key={order.order_id}
                    className="border-t border-slate-700 hover:bg-slate-700/50 transition-colors"
                  >
                    <td className="p-3 text-sm text-slate-400">
                      {new Date(order.created_at).toLocaleTimeString()}
                    </td>
                    <td className="p-3 font-medium">{order.symbol}</td>
                    <td className="p-3">
                      <span
                        className={`px-2 py-1 rounded text-xs ${
                          order.strategy === 'CORE'
                            ? 'bg-blue-900/50 text-blue-400'
                            : 'bg-purple-900/50 text-purple-400'
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
                        {order.side}
                      </span>
                    </td>
                    <td className="p-3 text-slate-400">{order.order_type}</td>
                    <td className="p-3 text-right font-mono">{order.quantity.toFixed(4)}</td>
                    <td className="p-3 text-right font-mono">
                      {order.price ? `$${order.price.toFixed(2)}` : 'MARKET'}
                    </td>
                    <td className="p-3 text-right font-mono">
                      {order.filled_quantity.toFixed(4)}
                      {order.avg_fill_price && (
                        <span className="text-slate-500 ml-1">
                          @${order.avg_fill_price.toFixed(2)}
                        </span>
                      )}
                    </td>
                    <td className="p-3">
                      <span
                        className={`px-2 py-1 rounded text-xs text-white ${
                          statusColors[order.status] || 'bg-gray-600'
                        }`}
                      >
                        {order.status}
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
