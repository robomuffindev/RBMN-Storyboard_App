/**
 * ShortcodeRedirect — resolves a shortcode to its entity and navigates.
 *
 * URL: /s/{code} (e.g. /s/a3f9-img-0047)
 * Calls GET /api/shortcode/{code}, then navigates to the entity's
 * frontend_route.  Shows a small loading state in between.
 */
import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { resolveShortcode } from '@/api/client';

export default function ShortcodeRedirect() {
  const { code } = useParams<{ code: string }>();
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!code) {
      setError('No shortcode provided');
      return;
    }
    let cancelled = false;
    resolveShortcode(code)
      .then((res) => {
        if (cancelled) return;
        navigate(res.data.frontend_route, { replace: true });
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err?.response?.data?.detail ?? String(err));
      });
    return () => { cancelled = true; };
  }, [code, navigate]);

  if (error) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-950 text-gray-200">
        <div className="max-w-md p-6 rounded-lg bg-gray-900 border border-red-700/50">
          <h2 className="text-lg font-semibold text-red-300 mb-2">
            Shortcode not found
          </h2>
          <p className="text-sm text-gray-400 mb-3">
            <code className="text-purple-300">{code}</code> — {error}
          </p>
          <Link to="/" className="text-sm text-purple-300 hover:text-purple-200">
            ← Back home
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-950 text-gray-400">
      <div className="text-sm">Resolving {code}…</div>
    </div>
  );
}
