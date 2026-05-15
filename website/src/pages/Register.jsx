import { Helmet } from 'react-helmet-async'
import { useState } from 'react'

export default function Register() {
  const [name, setName] = useState('')
  const [roll, setRoll] = useState('')
  const [email, setEmail] = useState('')
  const [phone, setPhone] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [success, setSuccess] = useState(false)

  const handleSubmit = async (e) => {
    e.preventDefault()
    setError(''); setBusy(true)
    try {
      const r = await fetch('/api/v1/register-student', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          full_name: name.trim(),
          roll_number: roll.trim().toUpperCase(),
          email: email.trim().toLowerCase(),
          phone: phone.trim(),
          password,
        }),
      })
      const d = await r.json()
      if (!r.ok) throw new Error(d.detail || 'Registration failed')
      setSuccess(true)
    } catch (e) { setError(e.message) }
    finally { setBusy(false) }
  }

  if (success) {
    return (
      <div className="min-h-screen bg-navy-950 flex items-center justify-center p-6">
        <div style={{ maxWidth: 440, width: '100%', textAlign: 'center' }}>
          <h1 style={{ fontSize: 24, fontWeight: 700, color: 'white', marginBottom: 12 }}>Registration successful!</h1>
          <p style={{ color: 'var(--muted)', fontSize: 14, marginBottom: 24 }}>You can now log in and start your exam.</p>
          <a href="/student-react" className="btn btn-primary" style={{ textDecoration: 'none' }}>Go to Dashboard</a>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-navy-950 flex items-center justify-center p-6">
      <Helmet>
        <title>Student Registration — Procta</title>
        <meta name="description" content="Register as a student for an AI-proctored exam." />
        <link rel="canonical" href="https://app.procta.net/register" />
        <meta name="robots" content="noindex, nofollow" />
      </Helmet>
      <div style={{ maxWidth: 480, width: '100%' }}>
        <div style={{ textAlign: 'center', marginBottom: 28 }}>
          <h1 style={{ fontSize: 26, fontWeight: 700, color: 'white', margin: '0 0 6px' }}>Student Registration</h1>
          <p style={{ color: 'var(--muted)', fontSize: 14 }}>Enter your details to register for the exam.</p>
        </div>
        <form onSubmit={handleSubmit} style={{ background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 14, padding: 32 }}>
          <div className="fg"><label>Full name</label><input type="text" value={name} onChange={e => setName(e.target.value)} required style={{ width: '100%' }} /></div>
          <div className="fg"><label>Roll number</label><input type="text" value={roll} onChange={e => setRoll(e.target.value)} required style={{ width: '100%', textTransform: 'uppercase', fontFamily: 'monospace' }} /></div>
          <div className="fg"><label>Email</label><input type="email" value={email} onChange={e => setEmail(e.target.value)} required style={{ width: '100%' }} /></div>
          <div className="fg"><label>Phone <span style={{ fontWeight: 400 }}>(optional)</span></label><input type="tel" value={phone} onChange={e => setPhone(e.target.value)} style={{ width: '100%' }} /></div>
          <div className="fg"><label>Password</label><input type="password" value={password} onChange={e => setPassword(e.target.value)} required minLength={8} style={{ width: '100%' }} /></div>
          {error && <div style={{ color: 'var(--red)', fontSize: 13, marginBottom: 12 }}>{error}</div>}
          <button className="btn btn-primary" type="submit" disabled={busy} style={{ width: '100%', marginTop: 8 }}>{busy ? 'Registering...' : 'Register'}</button>
        </form>
        <p style={{ textAlign: 'center', color: 'var(--muted)', fontSize: 13, marginTop: 20 }}>
          Already registered? <a href="/student-react" style={{ color: 'var(--accent-light)' }}>Log in</a>
        </p>
      </div>
    </div>
  )
}
