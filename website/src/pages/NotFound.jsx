import { Link } from 'wouter'

export default function NotFound() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-navy-950 p-8">
      <div className="max-w-md text-center">
        <h1 className="font-display text-6xl font-bold text-white">404</h1>
        <p className="mt-4 text-lg text-slate-400">
          Page not found. The link may be broken or the page has moved.
        </p>
        <Link to="/" className="mt-6 inline-block rounded-lg bg-accent px-6 py-3 text-sm font-medium text-white no-underline transition-colors hover:bg-accent-light">
          Go home
        </Link>
      </div>
    </div>
  )
}
