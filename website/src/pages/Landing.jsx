import { Helmet } from 'react-helmet-async'
import Navbar from '../components/Navbar'
import Hero from '../components/Hero'
import CapabilityProof from '../components/CapabilityProof'
import Problem from '../components/Problem'
import USPs from '../components/USPs'
import HowItWorks from '../components/HowItWorks'
import Features from '../components/Features'
import Demo from '../components/Demo'
import UseCases from '../components/UseCases'
import Trust from '../components/Trust'
import PrivacySection from '../components/PrivacySection'
import Comparison from '../components/Comparison'
import FAQ from '../components/FAQ'
import CTA from '../components/CTA'
import Footer from '../components/Footer'

export default function Landing() {
  return (
    <div className="min-h-screen">
      <Helmet>
        <title>Procta — AI Online Proctoring & Secure Exam Browser (India)</title>
        <meta name="description" content="AI online proctoring for Indian colleges & coaching institutes: Procta Secure Browser (PSB), on-device AI monitoring, phone-cam room scan, live invigilation & auto-grading." />
        <meta property="og:title" content="Procta — AI Online Proctoring & Secure Exam Browser (India)" />
        <meta property="og:description" content="AI-powered online exam proctoring for India — Procta Secure Browser (PSB) lockdown, on-device AI monitoring, phone-camera room scan, live invigilation and automated grading." />
        <meta property="og:type" content="website" />
        <meta property="og:url" content="https://www.procta.net" />
        <meta property="og:image" content="https://www.procta.net/og-image.png" />
        <link rel="canonical" href="https://www.procta.net" />
        {/* Structured data: protects the brand result (Organization) and earns
            product rich-results for proctoring queries (SoftwareApplication). */}
        <script type="application/ld+json">{JSON.stringify({
          "@context": "https://schema.org",
          "@graph": [
            {
              "@type": "Organization",
              "name": "Procta",
              "url": "https://www.procta.net",
              "logo": "https://www.procta.net/icon-512.png",
              "description": "AI-powered online exam proctoring platform for colleges and coaching institutes in India.",
              "sameAs": []
            },
            {
              "@type": "SoftwareApplication",
              "name": "Procta",
              "applicationCategory": "EducationalApplication",
              "operatingSystem": "Windows, macOS",
              "description": "AI online proctoring with the Procta Secure Browser (PSB) lockdown, on-device AI monitoring, phone-camera room scan, live invigilation, and automated grading.",
              "url": "https://www.procta.net"
            }
          ]
        })}</script>
      </Helmet>
      <Navbar />
      <Hero />
      <CapabilityProof />
      <Problem />
      <USPs />
      <HowItWorks />
      <Features />
      <Demo />
      <UseCases />
      <Trust />
      <PrivacySection />
      <Comparison />
      <FAQ />
      <CTA />
      <Footer />
    </div>
  )
}
