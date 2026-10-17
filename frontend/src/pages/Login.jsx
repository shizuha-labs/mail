import { Mail } from 'lucide-react'

export default function Login() {
  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-primary-50 to-primary-100 p-4">
      <div className="w-full max-w-md">
        {/* Logo */}
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-16 h-16 bg-primary-600 rounded-full mb-4">
            <Mail className="w-8 h-8 text-white" />
          </div>
          <h1 className="text-2xl font-bold text-gray-900">Shizuha Mail</h1>
          <p className="text-gray-600 mt-2">Sign in to your account</p>
        </div>

        {/* Shizuha ID is the single supported authentication authority. */}
        <div className="bg-white rounded-xl shadow-lg p-8">
          {/* Sign in with Shizuha ID — OAuth authz-code + PKCE (PLAT-353).
              Primary login path; redirects to the ID consent/login screen and
              returns with RS256 tokens via the backend BFF callback. */}
          <button
            type="button"
            onClick={() => { window.location.href = `${import.meta.env.VITE_API_BASE || '/api'}/auth/oauth/login/` }}
            className="w-full bg-gray-900 text-white py-3 rounded-lg font-medium hover:bg-gray-800 focus:ring-4 focus:ring-gray-300 transition-all"
          >
            Sign in with Shizuha ID
          </button>
          <div className="mt-6 text-center text-sm text-gray-600">
            <p>
              New to Shizuha?{' '}
              <a href="/id/register" className="text-primary-600 hover:text-primary-700 font-medium">
                Sign up with Shizuha ID
              </a>
            </p>
          </div>
        </div>
      </div>
    </div>
  )
}
