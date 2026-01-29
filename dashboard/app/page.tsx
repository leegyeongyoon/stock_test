'use client'

import { useEffect, useState, useCallback } from 'react'
import { api, Summary, Position, Event } from '@/lib/api'
import ConnectionStatus from '@/components/ConnectionStatus'
import SummaryCards from '@/components/SummaryCards'
import PositionsTable from '@/components/PositionsTable'
import EventsTimeline from '@/components/EventsTimeline'

export default function Dashboard() {
  const [summary, setSummary] = useState<Summary | null>(null)
  const [positions, setPositions] = useState<Position[]>([])
  const [events, setEvents] = useState<Event[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const fetchData = useCallback(async () => {
    try {
      const [summaryData, positionsData, eventsData] = await Promise.all([
        api.getSummary(),
        api.getPositions(),
        api.getEvents(undefined, 50),
      ])

      setSummary(summaryData)
      setPositions(positionsData)
      setEvents(eventsData)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to fetch data')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchData()

    // 3초마다 폴링
    const interval = setInterval(fetchData, 3000)

    return () => clearInterval(interval)
  }, [fetchData])

  const handlePause = async () => {
    try {
      await api.pauseBot('Manual pause from dashboard')
      fetchData()
    } catch (e) {
      alert(e instanceof Error ? e.message : 'Failed to pause')
    }
  }

  const handleResume = async () => {
    try {
      await api.resumeBot('Manual resume from dashboard')
      fetchData()
    } catch (e) {
      alert(e instanceof Error ? e.message : 'Failed to resume')
    }
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-2xl font-bold">Dashboard</h1>
          <p className="text-slate-400 text-sm">
            {summary?.is_paper ? 'Paper Trading Mode' : 'Live Trading Mode'}
          </p>
        </div>
        <div className="flex items-center gap-4">
          <ConnectionStatus />
          <div className="flex gap-2">
            <button
              onClick={handlePause}
              disabled={summary?.mode !== 'NORMAL'}
              className="px-4 py-2 bg-yellow-600 hover:bg-yellow-700 disabled:opacity-50 disabled:cursor-not-allowed rounded text-sm font-medium transition-colors"
            >
              Pause
            </button>
            <button
              onClick={handleResume}
              disabled={summary?.mode === 'NORMAL' || summary?.mode === 'HALT'}
              className="px-4 py-2 bg-green-600 hover:bg-green-700 disabled:opacity-50 disabled:cursor-not-allowed rounded text-sm font-medium transition-colors"
            >
              Resume
            </button>
          </div>
        </div>
      </div>

      {/* Error Banner */}
      {error && (
        <div className="bg-red-900/50 border border-red-700 text-red-300 px-4 py-3 rounded">
          {error}
        </div>
      )}

      {/* Summary Cards */}
      <SummaryCards summary={summary} loading={loading} />

      {/* Main Content */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Positions Table - 2/3 width */}
        <div className="lg:col-span-2">
          <PositionsTable positions={positions} loading={loading} />
        </div>

        {/* Events Timeline - 1/3 width */}
        <div>
          <EventsTimeline events={events} loading={loading} />
        </div>
      </div>
    </div>
  )
}
