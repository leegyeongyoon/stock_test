/**
 * API 클라이언트 - Upbit Trading System
 */

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8086'

// ============================================
// Core Types
// ============================================

export interface Summary {
  equity: number
  pnl_today: number
  pnl_today_pct: number
  drawdown: number
  exposure: number
  cash: number
  mode: 'NORMAL' | 'SAFE' | 'HALT'
  is_paper: boolean
  updated_at: string
}

export interface Position {
  symbol: string
  strategy: 'CORE' | 'SATELLITE' | 'ATTACK'
  side: 'BUY'  // Upbit spot only - no short positions
  quantity: number
  avg_price: number
  current_price: number
  unrealized_pnl: number
  realized_pnl: number
  notional: number
  opened_at: string
}

export interface Event {
  id: string
  timestamp: string
  level: 'INFO' | 'WARNING' | 'ERROR' | 'CRITICAL'
  event_type: 'SYSTEM' | 'ORDER' | 'RISK' | 'STRATEGY' | 'CONNECTION' | 'ATTACK' | 'FILTER' | 'SATELLITE' | 'PULLBACK'
  message: string
  details?: Record<string, unknown>
}

export interface Order {
  order_id: string
  exchange_order_id?: string
  symbol: string
  strategy: 'CORE' | 'SATELLITE' | 'ATTACK'
  side: 'BUY' | 'SELL'  // SELL for exit orders
  order_type: 'MARKET' | 'LIMIT'
  quantity: number
  price?: number
  filled_quantity: number
  avg_fill_price?: number
  status: 'NEW' | 'SENT' | 'ACK' | 'PARTIAL' | 'FILLED' | 'CANCELED' | 'REJECTED'
  created_at: string
  updated_at: string
}

export interface Health {
  status: string
  db_connected: boolean
  engine_running: boolean
  exchange_connected: boolean
  last_heartbeat?: string
}

export interface Mode {
  mode: 'NORMAL' | 'SAFE' | 'HALT'
  reason: string
  changed_at: string
}

export interface UserModeStatus {
  requested_mode: string
  requested_mode_display: string
  effective_mode: string
  effective_mode_display: string
  downgrade_reason: string | null
  config: {
    max_position_pct: number
    max_concurrent_positions: number
    dd_halt_threshold: number
    daily_loss_limit: number
    correlation_warn: number
    correlation_action: number
    execute_trades: boolean
  }
  timestamp: string
}

// ============================================
// Capital Profile Types
// ============================================

export type CapitalMode = 'GROWTH' | 'PRESERVE'

export interface CapitalProfileConfig {
  risk_per_trade_min: number
  risk_per_trade_max: number
  total_risk_limit: number
  allowed_attack_levels: number[]
  allowed_attack_modes: string[]
  slippage_guard_multiplier: number
}

export interface HysteresisStatus {
  current_mode: CapitalMode
  consecutive_preserve_days: number
  consecutive_growth_days: number
  preserve_threshold_krw: number
  growth_threshold_krw: number
  preserve_days_required: number
  growth_days_required: number
  days_until_preserve: number
  days_until_growth: number
}

export interface CapitalProfileStatus {
  enabled: boolean
  current_mode: CapitalMode
  config: CapitalProfileConfig
  hysteresis: HysteresisStatus
}

// ============================================
// Attack Module Types
// ============================================

export type AttackMode = 'OFF' | 'NORMAL' | 'PLUS' | 'MAX'

export interface AttackScoreComponent {
  name: string
  value: number
  weight: number
  weighted_value: number
  description?: string
}

export interface AttackScore {
  symbol: string
  total_score: number
  level: string  // 'L1' | 'L2' | 'L3' | 'BLOCKED'
  target_allocation: number
  components: AttackScoreComponent[]
  timestamp: string
}

export interface AttackPosition {
  symbol: string
  quantity: number
  avg_price: number
  current_price: number
  unrealized_pnl: number
  unrealized_pnl_pct: number
  attack_level: string
  entry_score: number
  entered_at: string
}

export interface AttackSignal {
  symbol: string
  side: 'BUY'
  score: number
  level: string
  detected_at: string
  status: 'PENDING' | 'CONFIRMED' | 'EXPIRED'
}

export interface AttackGateStatus {
  can_enter: boolean
  reason: string | null
}

export interface AttackStats {
  today_trades: number
  today_pnl: number
  win_rate: number
}

export interface AttackStatus {
  mode: AttackMode
  enabled: boolean
  active_positions: number
  pending_signals: number
  gate_status: AttackGateStatus
  stats: AttackStats
}

export interface AttackGateResult {
  allowed: boolean
  reason: string | null
  checks: {
    dd_tier_check: boolean
    regime_check: boolean
    daily_loss_check: boolean
    volatility_check: boolean
    score_level_check: boolean
  }
}

export interface AttackGateCheckResponse {
  symbol: string
  score: {
    total: number
    level: string
  }
  gate_result: AttackGateResult
}

export interface AttackPositionsResponse {
  active_positions: AttackPosition[]
  pending_signals: AttackSignal[]
}

// ============================================
// Surge Candidates Types
// ============================================

export interface SurgeConditionScore {
  score: number
  value?: number
  ratio?: number
  target?: number
  reason?: string
  dist_breakout?: number
  dist_atr?: number
  entry_type?: string
}

export interface SurgeCandidate {
  symbol: string
  current_price: number
  korean_name: string
  english_name: string
  change_1m: SurgeConditionScore
  volume: SurgeConditionScore
  volume_spike: SurgeConditionScore  // 직전 대비 급증
  volume_accel: SurgeConditionScore  // 거래량 가속도
  vol_overheat: SurgeConditionScore
  anti_chase: SurgeConditionScore
  total_score: number
  is_hot_yesterday: boolean
}

export interface SurgeCandidatesResponse {
  candidates: SurgeCandidate[]
  total_scanned: number
  filtered_count: number
}

export interface AttackSetModeResponse {
  success: boolean
  mode: AttackMode
  status: AttackStatus
}

// ============================================
// Monitoring Types
// ============================================

export interface AttackCandidate {
  symbol: string
  score: number
  level: number
  distance_to_entry: number
  change_rate: number
  volume_24h: number
  components: {
    name: string
    score: number
    max_score: number
    details?: Record<string, unknown>
  }[]
}

export interface FilterStats {
  anti_chase: number
  exposure_limit: number
  overheat: number
  total: number
}

export interface MonitoringCandidatesResponse {
  attack_candidates: AttackCandidate[]
  filter_stats: FilterStats
  cache_updated_at: string | null
  entry_threshold: number
}

// ============================================
// Rebound Scalper Types (v4.2)
// ============================================

export type ReboundMode = 'OFF' | 'SAFE' | 'NORMAL' | 'AGGRESSIVE'

export interface ReboundPositionInfo {
  symbol: string
  entry_price: number
  current_qty: number
  tp1_hit: boolean
  tp2_hit: boolean
  be_stop_active: boolean
  trailing_active: boolean
  highest_price: number
  hold_time_min: number
}

export interface ReboundSignalInfo {
  symbol: string
  score: number
  level: number
  reason: string
}

export interface ReboundStats {
  total_trades: number
  win_rate: number
  tp1_hits: number
  tp2_hits: number
  stop_loss_hits: number
}

export interface ReboundFilterInfo {
  cooldowns: Record<string, {
    is_in_cooldown: boolean
    cooldown_until: string | null
    consecutive_losses: number
    total_loss_in_window: number
  }>
  filter_settings: {
    max_consecutive_losses: number
    cooldown_minutes: number
    disable_on_volatile: boolean
    disable_in_downtrend: boolean
  }
}

export interface ReboundCandidate {
  symbol: string
  score: number
  level: number
  distance_to_entry: number
  change_rate: number
  volume_24h: number
  rsi: number
  support_distance_pct: number
  orderbook_ratio: number
  components: {
    name: string
    score: number
    max_score: number
    details?: Record<string, unknown>
  }[]
}

export interface ReboundCandidatesResponse {
  candidates: ReboundCandidate[]
  cache_updated_at: string | null
  entry_threshold: number
}

export interface ReboundMonitoringResponse {
  strategy: string
  enabled: boolean
  mode: string
  positions: Record<string, ReboundPositionInfo>
  pending_signals: Record<string, ReboundSignalInfo>
  stats: ReboundStats
  filters: ReboundFilterInfo
}

export interface ReboundFullStatus {
  enabled: boolean
  mode: string
  min_level: number
  max_positions: number
  active_positions: number
  pending_signals: number
  settings: {
    stop_loss_pct: number
    tp1_pct: number
    tp1_ratio: number
    tp2_pct: number
    tp2_ratio: number
    trailing_stop_pct: number
    time_stop_hours: number
  }
  stats: ReboundStats & {
    avg_pnl: number
  }
  filter_data: ReboundFilterInfo
  positions: Record<string, unknown>
  signals: Record<string, unknown>
}

export interface ReboundSetModeResponse {
  success: boolean
  message: string
  enabled: boolean
  mode: ReboundMode
}

// ============================================
// API Client
// ============================================

class ApiClient {
  private baseUrl: string

  constructor(baseUrl: string = API_BASE_URL) {
    this.baseUrl = baseUrl
  }

  private async fetch<T>(endpoint: string, options?: RequestInit): Promise<T> {
    const url = `${this.baseUrl}/api${endpoint}`

    const response = await fetch(url, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        ...options?.headers,
      },
    })

    if (!response.ok) {
      throw new Error(`API Error: ${response.status} ${response.statusText}`)
    }

    return response.json()
  }

  // ---- Core API ----

  async getHealth(): Promise<Health> {
    return this.fetch<Health>('/health')
  }

  async getMode(): Promise<Mode> {
    return this.fetch<Mode>('/mode')
  }

  async getSummary(): Promise<Summary> {
    return this.fetch<Summary>('/summary')
  }

  async getPositions(): Promise<Position[]> {
    return this.fetch<Position[]>('/positions')
  }

  async getOrders(status?: string, limit: number = 100): Promise<Order[]> {
    const params = new URLSearchParams()
    if (status) params.set('status', status)
    params.set('limit', limit.toString())
    return this.fetch<Order[]>(`/orders?${params}`)
  }

  async getEvents(level?: string, limit: number = 100): Promise<Event[]> {
    const params = new URLSearchParams()
    if (level) params.set('level', level)
    params.set('limit', limit.toString())
    return this.fetch<Event[]>(`/events?${params}`)
  }

  async pauseBot(reason?: string): Promise<{ success: boolean; mode: string; message: string }> {
    return this.fetch('/bot/pause', {
      method: 'POST',
      body: JSON.stringify({ reason }),
    })
  }

  async resumeBot(reason?: string): Promise<{ success: boolean; mode: string; message: string }> {
    return this.fetch('/bot/resume', {
      method: 'POST',
      body: JSON.stringify({ reason }),
    })
  }

  async flattenPositions(confirm: boolean = false): Promise<{ success: boolean; message: string }> {
    return this.fetch('/bot/flatten', {
      method: 'POST',
      body: JSON.stringify({ confirm }),
    })
  }

  async getUserMode(): Promise<UserModeStatus> {
    return this.fetch<UserModeStatus>('/user/mode')
  }

  async setUserMode(mode: string): Promise<UserModeStatus> {
    return this.fetch<UserModeStatus>('/user/mode', {
      method: 'POST',
      body: JSON.stringify({ mode }),
    })
  }

  // ---- Attack Module API ----

  async getAttackStatus(): Promise<AttackStatus> {
    return this.fetch<AttackStatus>('/attack/status')
  }

  async setAttackMode(mode: AttackMode): Promise<AttackSetModeResponse> {
    return this.fetch<AttackSetModeResponse>('/attack/set-mode', {
      method: 'POST',
      body: JSON.stringify({ mode }),
    })
  }

  async getAttackScore(symbol: string): Promise<AttackScore> {
    return this.fetch<AttackScore>(`/attack/score/${symbol}`)
  }

  async getAttackPositions(): Promise<AttackPositionsResponse> {
    return this.fetch<AttackPositionsResponse>('/attack/positions')
  }

  async checkAttackGate(symbol: string): Promise<AttackGateCheckResponse> {
    return this.fetch<AttackGateCheckResponse>(`/attack/gate-check/${symbol}`)
  }

  // ---- Capital Profile API ----

  async getCapitalProfileStatus(): Promise<CapitalProfileStatus> {
    return this.fetch<CapitalProfileStatus>('/capital-profile/status')
  }

  // ---- Surge Candidates API ----

  async getSurgeCandidates(minScore: number = 70, limit: number = 20): Promise<SurgeCandidatesResponse> {
    const params = new URLSearchParams()
    params.set('min_score', minScore.toString())
    params.set('limit', limit.toString())
    return this.fetch<SurgeCandidatesResponse>(`/surge/candidates?${params}`)
  }

  // ---- Monitoring API ----

  async getMonitoringCandidates(limit: number = 5, minScore: number = 50): Promise<MonitoringCandidatesResponse> {
    const params = new URLSearchParams()
    params.set('limit', limit.toString())
    params.set('min_score', minScore.toString())
    return this.fetch<MonitoringCandidatesResponse>(`/monitoring/candidates?${params}`)
  }

  async getFilterStats(): Promise<FilterStats> {
    return this.fetch<FilterStats>('/monitoring/filter-stats')
  }

  // ---- Rebound Scalper API ----

  async getReboundStatus(): Promise<ReboundMonitoringResponse> {
    return this.fetch<ReboundMonitoringResponse>('/monitoring/rebound')
  }

  async getReboundFullStatus(): Promise<ReboundFullStatus> {
    return this.fetch<ReboundFullStatus>('/monitoring/rebound/full-status')
  }

  async setReboundMode(mode: ReboundMode): Promise<ReboundSetModeResponse> {
    return this.fetch<ReboundSetModeResponse>(`/monitoring/rebound/mode?mode=${mode}`, {
      method: 'POST',
    })
  }

  async getReboundPositions(): Promise<{ count: number; positions: Record<string, unknown> }> {
    return this.fetch('/monitoring/rebound/positions')
  }

  async getReboundFilters(): Promise<ReboundFilterInfo> {
    return this.fetch<ReboundFilterInfo>('/monitoring/rebound/filters')
  }

  async getReboundCandidates(limit: number = 5, minScore: number = 0): Promise<ReboundCandidatesResponse> {
    const params = new URLSearchParams()
    params.set('limit', limit.toString())
    params.set('min_score', minScore.toString())
    return this.fetch<ReboundCandidatesResponse>(`/monitoring/rebound/candidates?${params}`)
  }
}

export const api = new ApiClient()
