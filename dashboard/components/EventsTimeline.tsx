'use client'

import { Event } from '@/lib/api'

interface Props {
  events: Event[]
  loading?: boolean
  maxItems?: number
}

const levelColors = {
  INFO: 'bg-blue-500',
  WARNING: 'bg-yellow-500',
  ERROR: 'bg-red-500',
  CRITICAL: 'bg-red-700',
}

const levelTextColors = {
  INFO: 'text-blue-400',
  WARNING: 'text-yellow-400',
  ERROR: 'text-red-400',
  CRITICAL: 'text-red-300',
}

const levelLabels: Record<string, string> = {
  INFO: '정보',
  WARNING: '경고',
  ERROR: '오류',
  CRITICAL: '심각',
}

export default function EventsTimeline({ events, loading, maxItems = 20 }: Props) {
  if (loading) {
    return (
      <div className="bg-slate-800 rounded-lg border border-slate-700 p-4">
        <h2 className="text-lg font-semibold mb-4">이벤트 로그</h2>
        <div className="animate-pulse space-y-3">
          {[1, 2, 3, 4, 5].map((i) => (
            <div key={i} className="h-12 bg-slate-700 rounded"></div>
          ))}
        </div>
      </div>
    )
  }

  const displayEvents = events.slice(0, maxItems)

  return (
    <div className="bg-slate-800 rounded-lg border border-slate-700 overflow-hidden">
      <div className="p-4 border-b border-slate-700">
        <h2 className="text-lg font-semibold">이벤트 로그</h2>
      </div>

      {displayEvents.length === 0 ? (
        <div className="p-8 text-center text-slate-400">
          이벤트 없음
        </div>
      ) : (
        <div className="divide-y divide-slate-700 max-h-[400px] overflow-y-auto">
          {displayEvents.map((event) => (
            <div
              key={event.id}
              className="p-3 hover:bg-slate-700/30 transition-colors"
            >
              <div className="flex items-start gap-3">
                <div
                  className={`w-2 h-2 rounded-full mt-2 ${
                    levelColors[event.level]
                  }`}
                />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span
                      className={`text-xs font-medium ${
                        levelTextColors[event.level]
                      }`}
                    >
                      {levelLabels[event.level] || event.level}
                    </span>
                    <span className="text-xs text-slate-500">
                      {event.event_type}
                    </span>
                    <span className="text-xs text-slate-600">
                      {new Date(event.timestamp).toLocaleTimeString('ko-KR', { timeZone: 'Asia/Seoul' })}
                    </span>
                  </div>
                  <p className="text-sm text-slate-300 mt-1 break-words">
                    {event.message}
                  </p>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
