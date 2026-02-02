'use client'

import { useEffect, useState } from 'react'
import { api } from '@/lib/api'

interface Props {
  pollingInterval?: number
}

export default function ConnectionStatus({ pollingInterval = 5000 }: Props) {
  const [connected, setConnected] = useState(true)
  const [lastUpdate, setLastUpdate] = useState<Date | null>(null)

  useEffect(() => {
    const checkConnection = async () => {
      try {
        await api.getHealth()
        setConnected(true)
        setLastUpdate(new Date())
      } catch {
        setConnected(false)
      }
    }

    checkConnection()
    const interval = setInterval(checkConnection, pollingInterval)

    return () => clearInterval(interval)
  }, [pollingInterval])

  return (
    <div className="flex items-center gap-2">
      <div
        className={`w-2 h-2 rounded-full ${
          connected ? 'bg-green-500' : 'bg-red-500'
        }`}
      />
      <span className="text-sm text-slate-400">
        {connected ? '연결됨' : '연결 끊김'}
      </span>
      {lastUpdate && connected && (
        <span className="text-xs text-slate-500">
          {lastUpdate.toLocaleTimeString()}
        </span>
      )}
    </div>
  )
}
