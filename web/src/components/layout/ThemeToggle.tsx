import { Moon, Sun } from 'lucide-react';
import {
  useEffect,
  useRef,
  useState,
  type ComponentType,
  type ReactElement,
  type RefObject,
} from 'react';

import { Button } from '@/components/ui/button';
import { useTheme } from '@/lib/theme';

type TooltipComponent = ComponentType<{ content: string; children: ReactElement }>;

/** The tooltip, once its code has arrived (shared by every render). */
let LoadedTooltip: TooltipComponent | null = null;

/**
 * The tooltip library is large and only repeats the button's name, so it
 * loads after the first page is shown; until then, or if it never loads,
 * the plain button works the same. Wrapping the button in the tooltip
 * replaces its DOM node, so focus moves to the new node when the old one
 * had it.
 *
 * @returns Whether the tooltip has loaded.
 */
function useLateTooltip(button: RefObject<HTMLButtonElement | null>): boolean {
  const [loaded, setLoaded] = useState(LoadedTooltip !== null);
  const refocus = useRef(false);
  useEffect(() => {
    if (LoadedTooltip) return;
    let active = true;
    import('@/components/ui/tooltip').then(
      (module) => {
        LoadedTooltip = module.Tooltip;
        if (!active) return;
        refocus.current = document.activeElement === button.current;
        setLoaded(true);
      },
      () => undefined, // keep the plain button
    );
    return () => {
      active = false;
    };
  }, [button]);
  useEffect(() => {
    if (loaded && refocus.current) {
      refocus.current = false;
      button.current?.focus();
    }
  }, [loaded, button]);
  return loaded;
}

export function ThemeToggle() {
  const { theme, setTheme } = useTheme();
  const ref = useRef<HTMLButtonElement>(null);
  const tooltipLoaded = useLateTooltip(ref);
  const next = theme === 'dark' ? 'light' : 'dark';
  const button = (
    <Button
      ref={ref}
      variant="ghost"
      size="icon"
      onClick={() => setTheme(next)}
      aria-label={`Use ${next} theme`}
    >
      {theme === 'dark' ? <Sun aria-hidden="true" /> : <Moon aria-hidden="true" />}
    </Button>
  );
  return tooltipLoaded && LoadedTooltip ? (
    <LoadedTooltip content={`Use ${next} theme`}>{button}</LoadedTooltip>
  ) : (
    button
  );
}
