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
