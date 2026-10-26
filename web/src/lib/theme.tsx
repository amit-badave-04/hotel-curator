import {
  createContext,
  use,
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';

import { readStorage, writeStorage } from './storage';

export type Theme = 'light' | 'dark';

const STORAGE_KEY = 'hotel-curator:theme';
const DARK_QUERY = '(prefers-color-scheme: dark)';

export function storedTheme(): Theme | null {
  const value = readStorage(STORAGE_KEY);
  return value === 'light' || value === 'dark' ? value : null;
}

export function systemTheme(): Theme {
  return typeof window !== 'undefined' && window.matchMedia?.(DARK_QUERY).matches
    ? 'dark'
    : 'light';
}

/** Set the theme on <html>; CSS tokens switch on [data-theme]. */
export function applyTheme(theme: Theme): void {
  document.documentElement.dataset.theme = theme;
}

interface ThemeContextValue {
  theme: Theme;
  setTheme: (theme: Theme) => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [choice, setChoice] = useState<Theme | null>(storedTheme);
  const [system, setSystem] = useState<Theme>(systemTheme);
  const theme = choice ?? system;

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  useEffect(() => {
    const media = window.matchMedia?.(DARK_QUERY);
    if (!media) return undefined;
    const onChange = (event: MediaQueryListEvent) => setSystem(event.matches ? 'dark' : 'light');
    media.addEventListener('change', onChange);
    return () => media.removeEventListener('change', onChange);
  }, []);

  const setTheme = useCallback((next: Theme) => {
    setChoice(next);
    writeStorage(STORAGE_KEY, next);
  }, []);

  const value = useMemo(() => ({ theme, setTheme }), [theme, setTheme]);
  return <ThemeContext value={value}>{children}</ThemeContext>;
}

export function useTheme(): ThemeContextValue {
  const value = use(ThemeContext);
  if (!value) throw new Error('useTheme must be used inside ThemeProvider');
  return value;
}
