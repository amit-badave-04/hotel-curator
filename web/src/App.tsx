import { CSPProvider } from '@base-ui/react/csp-provider';
import { QueryClientProvider, type QueryClient } from '@tanstack/react-query';
import type { ReactNode } from 'react';

import { TooltipProvider } from '@/components/ui/tooltip';
import { CompareProvider } from '@/lib/compare';
import { ThemeProvider } from '@/lib/theme';

/** Providers shared by the app and the tests. */
export function Providers({ client, children }: { client: QueryClient; children: ReactNode }) {
  return (
    // The strict Content-Security-Policy forbids inline <style>; Base UI is told not to add any.
    <CSPProvider disableStyleElements>
      <QueryClientProvider client={client}>
        <ThemeProvider>
          <CompareProvider>
            <TooltipProvider delay={300}>{children}</TooltipProvider>
          </CompareProvider>
        </ThemeProvider>
      </QueryClientProvider>
    </CSPProvider>
  );
}
