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
        <title>Procta — AI Proctored Exams, Secure Browser, Phone Cam & Automated Grading</title>
        <meta name="description" content="Procta runs secure online exams for colleges and coaching institutes: Electron lockdown browser, on-device AI proctoring, phone-camera room scan, live teacher dashboard, AI grading, scorecards, LTI, Razorpay billing, and 3,500-student architecture headroom." />
        <meta property="og:title" content="Procta — AI Proctored Exams, Secure Browser, Phone Cam & Automated Grading" />
        <meta property="og:description" content="Complete exam workflow: secure browser, on-device AI proctoring, phone cam, live dashboard, AI grading, scorecards, LTI, and 3,500-student architecture headroom." />
        <meta property="og:type" content="website" />
        <meta property="og:url" content="https://procta.net" />
        <meta property="og:image" content="https://procta.net/og-image.png" />
        <link rel="canonical" href="https://procta.net" />
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
