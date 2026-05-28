import { Component, type ReactNode, type ErrorInfo } from 'react';

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

/**
 * Global error boundary — catches render errors to prevent the entire
 * app from crashing to a blank/solid-color screen.
 */
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, error: null };

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('[ErrorBoundary] Caught render error:', error, info.componentStack);
  }

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) return this.props.fallback;
      return (
        <div className="flex flex-col items-center justify-center h-full bg-gray-950 text-gray-300 p-6">
          <p className="text-sm font-medium text-red-400 mb-2">Something went wrong</p>
          <p className="text-xs text-gray-500 mb-4 max-w-md text-center">
            {this.state.error?.message || 'An unexpected error occurred'}
          </p>
          <button
            onClick={() => this.setState({ hasError: false, error: null })}
            className="px-3 py-1.5 text-xs bg-gray-800 hover:bg-gray-700 rounded border border-gray-700 transition-colors"
          >
            Try Again
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
