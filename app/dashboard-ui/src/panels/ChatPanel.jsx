import { useState, useEffect, useRef, useCallback } from 'react'
import { useAuth } from '../lib/auth'
import { API_BASE } from '../config'

const WS_RECONNECT_BASE_MS = 1000
const WS_RECONNECT_MAX_MS = 30000

export default function ChatPanel() {
  const { authFetch } = useAuth()
  const [students, setStudents] = useState([])
  const [activeSid, setActiveSid] = useState(null)
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [connected, setConnected] = useState(false)
  const [statusMsg, setStatusMsg] = useState('')
  const wsRef = useRef(null)
  const chatEndRef = useRef(null)
  const reconnectTimerRef = useRef(null)
  const reconnectAttemptsRef = useRef(0)
  const unmountedRef = useRef(false)

  useEffect(() => {
    unmountedRef.current = false
    connectWS()
    return () => {
      unmountedRef.current = true
      clearTimeout(reconnectTimerRef.current)
      if (wsRef.current) wsRef.current.close()
    }
  }, [])

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const connectWS = useCallback(async () => {
    if (unmountedRef.current) return
    const token = localStorage.getItem('procta_token')
    if (!token) return
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
    const host = location.host
    try {
      const ws = new WebSocket(`${proto}//${host}/ws/chat/teacher`, [token])
      ws.onopen = () => {
        if (unmountedRef.current) { ws.close(); return }
        setConnected(true)
        setStatusMsg('')
        reconnectAttemptsRef.current = 0
      }
      ws.onmessage = (ev) => {
        try {
          const data = JSON.parse(ev.data)
          if (data.type === 'student_list') setStudents(data.students || [])
          else if (data.type === 'msg' || data.type === 'broadcast') {
            setMessages(prev => {
              const updated = data.type === 'broadcast'
                ? prev.map(m => m.type === 'broadcast_pending' ? { ...m, ...data } : m)
                : [...prev, { from: 'student', text: data.text, ts: data.ts }]
              return updated
            })
          } else if (data.type === 'history') {
            setMessages((data.messages || []).map(m => ({
              from: m.from === 'teacher' ? 'teacher' : 'student',
              text: m.text,
              ts: m.ts,
            })))
          }
        } catch (_) { setStatusMsg('Chat received an unreadable update. New messages may be delayed.') }
      }
      ws.onclose = () => {
        setConnected(false)
        if (unmountedRef.current) return
        setStatusMsg('Chat disconnected. Reconnecting...')
        // Exponential backoff reconnect
        const attempts = reconnectAttemptsRef.current
        const delay = Math.min(WS_RECONNECT_BASE_MS * 2 ** attempts, WS_RECONNECT_MAX_MS)
        reconnectAttemptsRef.current = attempts + 1
        reconnectTimerRef.current = setTimeout(connectWS, delay)
      }
      wsRef.current = ws
    } catch (_) { setStatusMsg('Chat connection failed. Please refresh or try again.') }
  }, [])

  const selectStudent = (sid) => {
    setActiveSid(sid)
    setMessages([])
    if (wsRef.current) {
      wsRef.current.send(JSON.stringify({ type: 'select_student', session_id: sid }))
    }
  }

  const sendMsg = (e) => {
    e.preventDefault()
    if (!input.trim() || !wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return
    wsRef.current.send(JSON.stringify({ type: 'msg', text: input.trim(), session_id: activeSid }))
    setMessages(prev => [...prev, { from: 'teacher', text: input.trim(), ts: Date.now() }])
    setInput('')
  }

  const sendBroadcast = () => {
    const text = prompt('Broadcast message to all online students:')
    if (text && wsRef.current) {
      wsRef.current.send(JSON.stringify({ type: 'broadcast', text }))
      setMessages(prev => [...prev, { from: 'teacher', text, ts: Date.now(), type: 'broadcast_pending' }])
    }
  }

  return (
    <div className="chat-wrap">
      <aside className="chat-roster">
        <div className="chat-roster-head">
          <div>
            <div className="chat-roster-title">Active Students</div>
            <div className="chat-roster-sub" id="chat-roster-sub">{connected ? 'Connected' : 'Not connected'}</div>
          </div>
          <button className="btn btn-ghost btn-sm" onClick={sendBroadcast} title="Send a message to every online student">Broadcast</button>
        </div>
        {statusMsg && <div className="auth-err" style={{ margin: 10, fontSize: 12 }}>{statusMsg}</div>}
        <div className="chat-roster-body">
          {students.length === 0 && <div className="chat-empty">No students online yet.</div>}
          {students.map(s => (
            <div key={s.session_id} className={`chat-row ${activeSid === s.session_id ? 'active' : ''}`} onClick={() => selectStudent(s.session_id)}>
              <div className="dot" />
              <div className="meta">
                <div className="name">{s.name || s.roll}</div>
                <div className="roll">{s.roll}</div>
              </div>
            </div>
          ))}
        </div>
      </aside>
      <section className="chat-thread">
        {!activeSid ? (
          <>
            <div className="chat-thread-head">
              <div className="chat-thread-title">Select a student</div>
              <div className="chat-thread-sub">Messages are ephemeral — nothing is stored after the exam ends.</div>
            </div>
            <div className="chat-thread-body">
              <div className="chat-empty-lg">Pick a student on the left to start chatting.</div>
            </div>
          </>
        ) : (
          <>
            <div className="chat-thread-head">
              <div className="chat-thread-title">{students.find(s => s.session_id === activeSid)?.name || 'Chat'}</div>
              <div className="chat-thread-sub">{activeSid}</div>
            </div>
            <div className="chat-thread-body">
              {messages.length === 0 && <div className="chat-empty-lg">No messages yet. Say hi.</div>}
              {messages.map((m, i) => (
                <div key={i} className={`chat-msg ${m.from === 'teacher' ? 'from-teacher' : 'from-student'}`}>
                  {m.text}
                  {m.ts && <span className="ts">{new Date(m.ts).toLocaleTimeString()}</span>}
                </div>
              ))}
              <div ref={chatEndRef} />
            </div>
            <form className="chat-composer" onSubmit={sendMsg}>
              <textarea id="chat-input" placeholder="Type a reply… (Enter to send, Shift+Enter for newline)" rows={2} value={input} onChange={(e) => setInput(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) sendMsg(e) }} />
              <button type="submit" className="btn btn-primary btn-sm" id="chat-send">Send</button>
            </form>
          </>
        )}
      </section>
    </div>
  )
}
