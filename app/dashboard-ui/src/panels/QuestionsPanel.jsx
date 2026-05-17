import { useState, useEffect, useCallback } from 'react'
import { useAuth } from '../lib/auth'

export default function QuestionsPanel({ currentExamId }) {
  const { authFetch } = useAuth()
  const [questions, setQuestions] = useState([])
  const [bankQuestions, setBankQuestions] = useState([])
  const [selectedIdx, setSelectedIdx] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [bankLoading, setBankLoading] = useState(false)
  const [bankSearch, setBankSearch] = useState('')
  const [activeTab, setActiveTab] = useState('bank') // bank | generate | import
  const [aiPrompt, setAiPrompt] = useState('')
  const [aiGenerating, setAiGenerating] = useState(false)
  const [mutationError, setMutationError] = useState('')

  const loadQuestions = useCallback(async () => {
    if (!currentExamId) { setLoading(false); return }
    setError('')
    try {
      const r = await authFetch(`/api/v1/admin/questions?exam_id=${encodeURIComponent(currentExamId)}`)
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        throw new Error(d.detail || `Failed to load questions (${r.status})`)
      }
      setQuestions((await r.json()).questions || [])
    } catch (e) {
      setError(e.message || 'Failed to load questions')
    } finally { setLoading(false) }
  }, [currentExamId, authFetch])

  const loadBank = useCallback(async () => {
    setBankLoading(true)
    try {
      const r = await authFetch(`/api/v1/admin/question-bank?exam_id=${encodeURIComponent(currentExamId)}`)
      if (r.ok) setBankQuestions((await r.json()).questions || [])
    } catch (err) { console.error('QuestionsPanel: load bank failed', err) }
    finally { setBankLoading(false) }
  }, [currentExamId, authFetch])

  useEffect(() => { if (currentExamId) { loadQuestions(); loadBank() } }, [currentExamId, loadQuestions, loadBank])

  const addQuestion = async () => {
    if (!currentExamId) return
    try {
      const r = await authFetch('/api/v1/admin/questions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ exam_id: currentExamId, question: 'New question', options: { A: '', B: '', C: '', D: '' }, correct: 'A' }),
      })
      if (r.ok) { loadQuestions(); setSelectedIdx(0) }
    } catch (err) { console.error('QuestionsPanel: add question failed', err) }
  }

  const saveQuestion = async (q, idx) => {
    if (!currentExamId || !q.id) return
    try {
      await authFetch('/api/v1/admin/questions', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ exam_id: currentExamId, question_id: q.id, question: q.question, options: q.options, correct: q.correct }),
      })
    } catch (err) { console.error('QuestionsPanel: save question failed', err) }
  }

  const deleteQuestion = async (qid) => {
    if (!confirm('Delete this question?')) return
    try {
      await authFetch(`/api/v1/admin/questions/${qid}?exam_id=${encodeURIComponent(currentExamId)}`, { method: 'DELETE' })
      loadQuestions()
      setSelectedIdx(null)
    } catch (err) { console.error('QuestionsPanel: delete question failed', err) }
  }

  const bankToExam = async (qid) => {
    try {
      const r = await authFetch('/api/v1/admin/question-bank/to-exam', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ exam_id: currentExamId, question_id: qid }),
      })
      if (r.ok) loadQuestions()
    } catch (err) { console.error('QuestionsPanel: bank to exam failed', err) }
  }

  const runAiGenerate = async () => {
    if (!aiPrompt.trim()) return
    setAiGenerating(true)
    try {
      const r = await authFetch('/api/v1/admin/question-bank/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt: aiPrompt, count: 5, exam_id: currentExamId, save_to: 'exam' }),
      })
      if (r.ok) { loadQuestions(); setAiPrompt(''); setActiveTab('bank') }
    } catch (err) { console.error('QuestionsPanel: AI generate failed', err) }
    finally { setAiGenerating(false) }
  }

  if (!currentExamId) return <div style={{ textAlign: 'center', padding: 60, color: 'var(--text-muted)' }}>Select an exam to manage questions.</div>
  if (loading) return <div className="loading" style={{ textAlign: 'center', padding: 60 }}>Loading questions...</div>
  if (error) return <div className="auth-err" style={{ margin: 20 }}>{error} <button className="btn-link" onClick={loadQuestions} style={{ marginLeft: 8 }}>Retry</button></div>

  const filteredBank = bankQuestions.filter(q =>
    !bankSearch || q.question?.toLowerCase().includes(bankSearch)
  )

  return (
    <div className="qx-shell" style={{ display: 'flex', gap: 16, height: 'calc(100vh - 200px)' }}>
      {/* Sidebar - question list */}
      <div className="q-sidebar" style={{ width: 280, flexShrink: 0, display: 'flex', flexDirection: 'column', background: 'var(--surface-1)', borderRadius: 'var(--radius-xl)', border: '1px solid var(--border-subtle)' }}>
        <div className="qx-toolbar-top" style={{ padding: 12 }}>
          <button className="btn btn-primary btn-sm" onClick={addQuestion} style={{ width: '100%' }}>+ Add Question</button>
        </div>
        <div className="q-list" style={{ flex: 1, overflowY: 'auto' }}>
          {questions.length === 0 && <div className="q-list-empty" style={{ padding: 16, color: 'var(--text-muted)', fontSize: 12, textAlign: 'center' }}>No questions yet. Click "Add" above.</div>}
          {questions.map((q, i) => (
            <div key={q.id || i} className={`q-row ${selectedIdx === i ? 'active' : ''}`}
              style={{
                padding: '10px 12px', cursor: 'pointer', borderBottom: '1px solid var(--border-subtle)',
                background: selectedIdx === i ? 'var(--accent-bg)' : undefined,
                color: selectedIdx === i ? 'var(--accent-light)' : 'var(--text)',
                fontSize: 12,
              }}
              onClick={() => setSelectedIdx(i)}
            >
              Q{i + 1}: {(q.question || '(empty)').slice(0, 60)}
            </div>
          ))}
        </div>
      </div>

      {/* Content - question editor */}
      <div className="q-content" style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 12, overflow: 'auto' }}>
        {selectedIdx == null && <div style={{ textAlign: 'center', padding: 60, color: 'var(--text-muted)' }}>Select a question to edit.</div>}
        {selectedIdx != null && questions[selectedIdx] && (() => {
          const q = { ...questions[selectedIdx] }
          const update = (field, value) => {
            const updated = [...questions]
            updated[selectedIdx] = { ...updated[selectedIdx], [field]: value }
            setQuestions(updated)
          }
          return (
            <div className="q-card" style={{ background: 'var(--surface-1)', borderRadius: 'var(--radius-xl)', border: '1px solid var(--border-subtle)', padding: 20 }}>
              <div style={{ marginBottom: 12 }}>
                <label style={{ fontSize: 11, color: 'var(--text-muted)', fontWeight: 600, textTransform: 'uppercase', letterSpacing: 0.04, display: 'block', marginBottom: 6 }}>Question Text</label>
                <textarea className="input" style={{ width: '100%', minHeight: 60, resize: 'vertical' }} value={q.question || ''} onChange={(e) => update('question', e.target.value)} />
              </div>
              <div style={{ marginBottom: 12 }}>
                <label style={{ fontSize: 11, color: 'var(--text-muted)', fontWeight: 600, textTransform: 'uppercase', letterSpacing: 0.04, display: 'block', marginBottom: 6 }}>Options</label>
                {['A', 'B', 'C', 'D'].map(k => (
                  <div key={k} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                    <span style={{ fontSize: 12, fontWeight: 600, color: q.correct === k ? 'var(--emerald)' : 'var(--text-muted)', width: 20 }}>{k}.</span>
                    <input className="input" style={{ flex: 1 }} value={q.options?.[k] || ''} onChange={(e) => update('options', { ...q.options, [k]: e.target.value })} />
                    <span style={{ fontSize: 10, color: 'var(--text-muted)', cursor: 'pointer' }} onClick={() => update('correct', k)}>
                      {q.correct === k ? '✅ Correct' : 'Mark correct'}
                    </span>
                  </div>
                ))}
              </div>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <button className="btn btn-primary btn-sm" onClick={() => saveQuestion(questions[selectedIdx], selectedIdx)}>Save Question</button>
                <button className="btn btn-secondary btn-sm" onClick={() => deleteQuestion(questions[selectedIdx]?.id)} style={{ color: 'var(--red)' }}>Delete</button>
              </div>
            </div>
          )
        })()}
      </div>

      {/* AI/Bank panel */}
      <div className="q-aipanel" style={{ width: 320, flexShrink: 0, display: 'flex', flexDirection: 'column', background: 'var(--surface-1)', borderRadius: 'var(--radius-xl)', border: '1px solid var(--border-subtle)' }}>
        <div style={{ display: 'flex', borderBottom: '1px solid var(--border-subtle)' }}>
          {['bank', 'generate', 'import'].map(tab => (
            <button key={tab} className={`btn btn-ghost btn-sm`} style={{
              flex: 1, padding: '10px 0', fontSize: 11, fontWeight: 600, textTransform: 'uppercase',
              borderBottom: activeTab === tab ? '2px solid var(--accent)' : '2px solid transparent',
              color: activeTab === tab ? 'var(--accent-light)' : 'var(--text-muted)',
            }} onClick={() => setActiveTab(tab)}>
              {tab === 'generate' ? 'AI Gen' : tab.charAt(0).toUpperCase() + tab.slice(1)}
            </button>
          ))}
        </div>
        <div style={{ flex: 1, overflow: 'auto', padding: 12 }}>
          {activeTab === 'bank' && (
            <>
              <input className="input" style={{ width: '100%', marginBottom: 8, padding: '6px 10px', fontSize: 12 }} placeholder="Search bank…" value={bankSearch} onChange={(e) => setBankSearch(e.target.value.toLowerCase())} />
              {bankLoading && <div style={{ color: 'var(--text-muted)', fontSize: 12, textAlign: 'center', padding: 20 }}>Loading...</div>}
              {!bankLoading && filteredBank.length === 0 && <div style={{ color: 'var(--text-muted)', fontSize: 12, textAlign: 'center', padding: 20 }}>No questions in bank.</div>}
              {!bankLoading && filteredBank.map((bq, i) => (
                <div key={bq.id || i} style={{ padding: '8px 0', borderBottom: '1px solid var(--border-subtle)', fontSize: 12 }}>
                  <div style={{ color: 'var(--text)', marginBottom: 4 }}>{(bq.question || '').slice(0, 80)}</div>
                  <button className="btn btn-ghost btn-sm" style={{ fontSize: 10, padding: '2px 6px', color: 'var(--accent-light)' }} onClick={() => bankToExam(bq.id)}>+ Add to exam</button>
                </div>
              ))}
            </>
          )}
          {activeTab === 'generate' && (
            <div>
              <textarea className="input" style={{ width: '100%', minHeight: 100, resize: 'vertical', marginBottom: 8 }} placeholder="Describe the questions you want the AI to generate…" value={aiPrompt} onChange={(e) => setAiPrompt(e.target.value)} />
              <button className="btn btn-primary btn-sm" style={{ width: '100%' }} onClick={runAiGenerate} disabled={aiGenerating}>{aiGenerating ? 'Generating...' : 'Generate'}</button>
            </div>
          )}
          {activeTab === 'import' && <div style={{ padding: 20, textAlign: 'center', color: 'var(--text-muted)', fontSize: 12 }}>Import from CSV or question bank.</div>}
        </div>
      </div>
    </div>
  )
}
