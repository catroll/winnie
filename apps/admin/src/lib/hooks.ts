import { useCallback, useEffect, useRef, useState } from 'react';

export type AsyncState<T> =
  | { status: 'loading'; data: null; error: null }
  | { status: 'success'; data: T; error: null }
  | { status: 'error'; data: null; error: Error };

export type AsyncResult<T> =
  | { status: 'loading'; data: null; error: null; reload: () => void }
  | { status: 'success'; data: T; error: null; reload: () => void }
  | { status: 'error'; data: null; error: Error; reload: () => void };

/**
 * Run an async loader and track its state. `deps` behave like effect
 * dependencies; `reload()` re-runs the loader without changing them.
 */
export function useApi<T>(
  loader: () => Promise<T>,
  deps: readonly unknown[],
): AsyncResult<T> {
  const [state, setState] = useState<AsyncState<T>>({
    status: 'loading',
    data: null,
    error: null,
  });
  const [revision, setRevision] = useState(0);
  const loaderRef = useRef(loader);
  loaderRef.current = loader;

  useEffect(() => {
    let alive = true;
    setState({ status: 'loading', data: null, error: null });
    loaderRef.current().then(
      (data) => {
        if (alive) {
          setState({ status: 'success', data, error: null });
        }
      },
      (error: unknown) => {
        if (alive) {
          setState({
            status: 'error',
            data: null,
            error: error instanceof Error ? error : new Error(String(error)),
          });
        }
      },
    );
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, revision]);

  const reload = useCallback(() => setRevision((value) => value + 1), []);
  return { ...state, reload };
}

/** Keep the document title in sync with the active page. */
export function useTitle(title: string): void {
  useEffect(() => {
    document.title = `${title} · Winnie Admin`;
  }, [title]);
}
