import { Helmet } from 'react-helmet-async'
import { Link } from 'wouter'
import { ArrowLeft } from 'lucide-react'
import Footer from '../components/Footer'

export default function Privacy() {
  return (
    <div className="min-h-screen bg-navy-950">
      <Helmet>
        <title>Privacy Policy — Procta</title>
        <meta name="description" content="Procta's privacy policy — we collect minimal data, never record video, and never share student data with third parties." />
        <meta property="og:title" content="Privacy Policy — Procta" />
        <meta property="og:description" content="We collect minimal data, never record video, and never share student data with third parties." />
        <meta property="og:type" content="website" />
        <meta property="og:url" content="https://www.procta.net/privacy" />
        <meta name="twitter:card" content="summary_large_image" />
        <link rel="canonical" href="https://www.procta.net/privacy" />
      </Helmet>
      <div className="mx-auto max-w-3xl px-6 pt-24 pb-16">
        <Link to="/" className="mb-8 inline-flex items-center gap-2 text-sm text-slate-500 transition-colors hover:text-white no-underline">
          <ArrowLeft size={16} />
          Back to home
        </Link>

        <h1 className="font-display text-3xl font-bold text-white md:text-4xl">Privacy Policy</h1>
        <p className="mt-2 text-sm text-slate-500">Last updated: April 2026</p>

        <div className="mt-10 space-y-8 text-sm leading-relaxed text-slate-400">
          <section>
            <h2 className="mb-3 text-lg font-semibold text-white">1. Information We Collect</h2>
            <p>
              Procta collects minimal data necessary to deliver exam proctoring services. This includes:
              student name, roll number, and email (provided during registration); exam responses and timestamps;
              AI-generated violation logs and risk scores. We do not collect biometric templates or record video.
            </p>
          </section>

          <section>
            <h2 className="mb-3 text-lg font-semibold text-white">2. How We Use Information</h2>
            <p>
              Data is used solely to provide proctoring services: authenticating students, monitoring exam sessions,
              generating risk scores, and producing reports for institutional administrators. We do not sell,
              rent, or share personal data with third parties for marketing purposes.
            </p>
          </section>

          <section>
            <h2 className="mb-3 text-lg font-semibold text-white">3. Camera and Audio Processing</h2>
            <p>
              Procta uses the device camera and microphone during exams for real-time AI analysis.
              Camera frames are processed locally by AI models to detect faces, gaze direction, and objects.
              Audio is analyzed for voice activity levels. Raw video and audio streams are not recorded,
              stored, or transmitted to our servers.
            </p>
          </section>

          <section>
            <h2 className="mb-3 text-lg font-semibold text-white">4. Data Storage and Security</h2>
            <p>
              All data is transmitted over TLS-encrypted connections. Database storage uses encryption at rest.
              Access to administrative functions requires authenticated credentials. We follow the principle of
              least privilege for all system access.
            </p>
          </section>

          <section>
            <h2 className="mb-3 text-lg font-semibold text-white">5. Data Retention</h2>
            <p>
              Exam data is retained for the duration specified by the administering institution.
              Institutions can request data deletion at any time. Student registration data is retained
              only as long as necessary for exam administration.
            </p>
          </section>

          <section>
            <h2 className="mb-3 text-lg font-semibold text-white">6. Your Rights</h2>
            <p>
              You have the right to access, correct, or delete your personal data. To exercise these rights,
              contact your institution's exam administrator or reach us at{' '}
              <a href="mailto:privacy@procta.net" className="text-accent-light hover:text-white no-underline">
                privacy@procta.net
              </a>.
            </p>
          </section>

          <section>
            <h2 className="mb-3 text-lg font-semibold text-white">7. Contact</h2>
            <p>
              For questions about this policy, contact us at{' '}
              <a href="mailto:privacy@procta.net" className="text-accent-light hover:text-white no-underline">
                privacy@procta.net
              </a>.
            </p>
          </section>

          <section>
            <h2 className="mb-3 text-lg font-semibold text-white">8. DPDP Act Compliance (India)</h2>
            <p className="mb-3">
              Procta is designed to comply with India's Digital Personal Data Protection Act, 2023. We follow data
              minimization principles, process exam data only with institutional consent (as the data fiduciary), and
              provide mechanisms for data access, correction, and deletion.
            </p>
            <p className="mb-3">
              <span className="text-white font-medium">Grievance Officer:</span> As required under Section 9(4) of the
              DPDP Act, individuals may contact our grievance officer for any concerns regarding their personal data:
            </p>
            <ul className="list-disc list-inside space-y-1 text-slate-400">
              <li>Name: Arihant Kaul</li>
              <li>Email: <a href="mailto:privacy@procta.net" className="text-accent-light hover:text-white no-underline">privacy@procta.net</a></li>
              <li>Response time: Within 48 business hours</li>
            </ul>
          </section>
        </div>
      </div>
      <Footer />
    </div>
  )
}
