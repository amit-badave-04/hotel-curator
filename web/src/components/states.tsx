import { CircleAlert, RotateCcw, SearchX } from 'lucide-react';
import type { ReactNode } from 'react';

import { ApiError } from '@/api/fetcher';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { cn } from '@/lib/utils';

interface ErrorStateProps {
  error: unknown;
  title?: string;
  onRetry?: () => void;
  children?: ReactNode;
  className?: string;
}

/** Shows the API's message and request id (for support), and a retry. */
export function ErrorState({
  error,
  title = 'Something went wrong',
  onRetry,
  children,
  className,
}: ErrorStateProps) {
  const apiError = error instanceof ApiError ? error : null;
  const message =
    apiError?.message ?? (error instanceof Error ? error.message : 'An unexpected error happened.');
  return (
    <div
      role="alert"
      className={cn(
        'rounded-lg border border-weak-border bg-weak-bg p-4 text-foreground',
        className,
      )}
    >
      <div className="flex items-start gap-3">
        <CircleAlert aria-hidden="true" className="mt-0.5 size-5 shrink-0 text-weak" />
        <div className="min-w-0 space-y-2">
          <p className="font-semibold">{title}</p>
          <p>{message}</p>
          {apiError?.retryAfter != null && (
            <p>
              Try again in {apiError.retryAfter} {apiError.retryAfter === 1 ? 'second' : 'seconds'}.
            </p>
          )}
          {apiError?.requestId && (
            <p className="text-sm text-muted-foreground">
              Request id: <code className="font-mono break-all">{apiError.requestId}</code>
            </p>
          )}
          {children}
          {onRetry && (
            <Button variant="outline" size="sm" onClick={onRetry}>
              <RotateCcw aria-hidden="true" />
              Try again
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}

interface EmptyStateProps {
  title: string;
  children?: ReactNode;
  className?: string;
}

export function EmptyState({ title, children, className }: EmptyStateProps) {
  return (
    <div
      className={cn(
        'flex flex-col items-center gap-3 rounded-lg border border-dashed p-8 text-center',
        className,
      )}
    >
      <SearchX aria-hidden="true" className="size-8 text-muted-foreground" />
      <p className="text-lg font-semibold">{title}</p>
      {children}
    </div>
  );
}

/** Skeleton lines with a screen-reader announcement. */
export function LoadingBlock({ label, lines = 3 }: { label: string; lines?: number }) {
  return (
    <div role="status" aria-live="polite" className="space-y-3">
      <span className="sr-only">{label}</span>
      {Array.from({ length: lines }, (_, i) => (
        <Skeleton key={i} className={cn('h-5', i % 3 === 2 ? 'w-2/3' : 'w-full')} />
      ))}
    </div>
  );
}
