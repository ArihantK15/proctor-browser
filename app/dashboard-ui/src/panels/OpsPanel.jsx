import { useEffect, useState } from 'react'
import { useAuth } from '../lib/auth'

const CHECK_LABELS = {
  supabase: 'Supabase',
  redis: 'Redis',
  worker: 'Worker',
  email: 'Email',
  disk: 'Disk',
  storage_write: 'Storage Write',
  memory_pct: 'Memory',
}

const METRIC_LABELS = {
  active_sessions: 'Active Sessions',
  submit_failures_24h: 'Failed Submits 24h',
  queue_depth: 'Queue Depth',
  queue_started: 'Jobs Running',
  queue_failed: 'Failed Jobs',
  worker_heartbeat_age_sec: 'Worker Heartbeat Age',
  redis_connected_clients: 'Redis Clients',
  memory_pct: 'Memory Used',
}

export default function OpsPanel() {
  const { authFetch } = useAuth()
  const [status, setStatus] = useState(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  const load = async () => {
    setError('')
    try {
      const r = await authFetch('/api/v1/admin/status')
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        throw new Error(d.detail || `Status request failed (${r.status})`)
      }
      setStatus(await r.json())
    } catch (e) {
      setError(e.message || 'Failed to load operations status')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
    const id = setInterval(load, 30000)
    return () => clearInterval(id)
  }, [])

  if (loading) return <div style={{ padding: 40, color: 'var(--text-muted)' }}>Loading operations status...</div>
  if (error) return <div className="auth-err" style={{ margin: 20 }}>{error}</div>
  if (!status) return null

  const checks = status.checks || {}
  const metrics = status.metrics || {}
  const release = status.release || {}
  const overall = status.status || 'unknown'

  return (
    <div>
      <div className="stats-bar" style={{ marginBottom: 16 }}>
        <StatusTile label="Overall" value={overall} tone={overall === 'ok' ? 'ok' : 'bad'} />
        <StatusTile label="Uptime" value={`${status.uptime_sec || 0}s`} />
        <StatusTile label="Health Checks" value={status.health_checks || Object.keys(checks).length} />
        <StatusTile label="Last Refresh" value={new Date().toLocaleTimeString()} />
      </div>

      <section style={{ marginBottom: 24 }}>
        <div className="table-toolbar" style={{ marginBottom: 12 }}>
          <h3 style={{ margin: 0 }}>Release</h3>
        </div>
        <div className="tools-grid" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: 12 }}>
          <MetricCard label="Environment" value={release.environment || 'unknown'} />
          <MetricCard label="Version" value={release.version || 'unset'} />
          <MetricCard label="Commit" value={shortCommit(release.commit)} />
          <MetricCard label="Image" value={release.image || 'unset'} />
          <StatusCard label="Sentry" value={release.sentry_configured ? 'ok' : 'unavailable'} />
        </div>
      </section>

      <section style={{ marginBottom: 24 }}>
        <div className="table-toolbar" style={{ marginBottom: 12 }}>
          <h3 style={{ margin: 0 }}>Service Health</h3>
          <button className="btn btn-secondary btn-sm" onClick={load}>Refresh</button>
        </div>
        <div className="tools-grid" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: 12 }}>
          {Object.entries(checks).map(([key, value]) => (
            <StatusCard key={key} label={CHECK_LABELS[key] || titleize(key)} value={String(value)} />
          ))}
        </div>
      </section>

      <section>
        <div className="table-toolbar" style={{ marginBottom: 12 }}>
          <h3 style={{ margin: 0 }}>Live Operations</h3>
        </div>
        <div className="tools-grid" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: 12 }}>
          {Object.entries(metrics).map(([key, value]) => (
            <MetricCard key={key} label={METRIC_LABELS[key] || titleize(key)} value={formatMetric(key, value)} />
          ))}
        </div>
      </section>
    </div>
  )
}

function StatusTile({ label, value, tone = 'neutral' }) {
  const color = tone === 'ok' ? 'var(--emerald)' : tone === 'bad' ? 'var(--red)' : 'var(--accent)'
  return (
    <div className="stat-tile">
      <div className="stat-label">{label}</div>
      <div className="stat-value" style={{ color, textTransform: 'uppercase' }}>{value}</div>
    </div>
  )
}

function StatusCard({ label, value }) {
  const normalized = value.toLowerCase()
  const color = normalized === 'ok' ? 'var(--emerald)' : normalized === 'warning' || normalized === 'noop' ? 'var(--amber)' : 'var(--red)'
  return (
    <div className="tool-card">
      <div className="tool-card-body">
        <div style={{ fontSize: 11, color: 'var(--text-muted)', textTransform: 'uppercase', fontWeight: 700 }}>{label}</div>
        <div style={{ marginTop: 8, color, fontWeight: 700, fontSize: 20 }}>{value}</div>
      </div>
    </div>
  )
}

function MetricCard({ label, value }) {
  return (
    <div className="tool-card">
      <div className="tool-card-body">
        <div style={{ fontSize: 11, color: 'var(--text-muted)', textTransform: 'uppercase', fontWeight: 700 }}>{label}</div>
        <div style={{ marginTop: 8, color: 'var(--text-primary)', fontWeight: 700, fontSize: 22 }}>{value}</div>
      </div>
    </div>
  )
}

function titleize(key) {
  return key.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())
}

function formatMetric(key, value) {
  if (value === null || value === undefined) return 'Unavailable'
  if (key === 'worker_heartbeat_age_sec') return `${value}s`
  if (key === 'memory_pct') return `${value}%`
  return String(value)
}

function shortCommit(value) {
  if (!value) return 'unset'
  return String(value).slice(0, 12)
}
