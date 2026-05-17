import { Helmet } from 'react-helmet-async'
import Navbar from '../components/Navbar'
import Hero from '../components/Hero'
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
        <title>Procta — AI Exam Proctoring Made Simple</title>
        <meta name="description" content="Procta is the AI-powered exam proctoring platform that runs inside a secure browser. No installs, no biometrics, no student data sharing. Start free." />
        <meta property="og:title" content="Procta — AI Exam Proctoring Made Simple" />
        <meta property="og:description" content="AI-powered exam proctoring inside a secure browser. No installs, no biometrics, no student data sharing." />
        <meta property="og:type" content="website" />
        <meta property="og:url" content="https://procta.net" />
        <meta property="og:image" content="https://procta.net/og-image.png" />
        <link rel="canonical" href="https://procta.net" />
      </Helmet>
      <Navbar />
      <Hero />
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
