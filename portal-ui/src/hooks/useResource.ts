import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError } from '../api/client'

export interface Resource<T> {
  data: T | null
  error: ApiError | null
  /** True only for the very first load, so refreshes never flash a spinner. */
  loading: boolean
  reload: (opts?: { quiet?: boolean }) => void
}

/**
 * Load something from the API once, then on demand. `key` identifies the
 * request; changing it starts over (and aborts whatever was in flight).
 */
export function useResource<T>(
  key: string,
  loader: (signal: AbortSignal) => Promise<T>,
): Resource<T> {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<ApiError | null>(null)
  const [loading, setLoading] = useState(true)

  const loaderRef = useRef(loader)
  loaderRef.current = loader

  const aliveRef = useRef(true)
  useEffect(() => {
    aliveRef.current = true
    return () => {
      aliveRef.current = false
    }
  }, [])

  const inFlight = useRef<AbortController | null>(null)

  const run = useCallback(
    async (quiet: boolean) => {
      inFlight.current?.abort()
      const controller = new AbortController()
      inFlight.current = controller
      if (!quiet) setLoading(true)
      try {
        const next = await loaderRef.current(controller.signal)
        if (controller.signal.aborted || !aliveRef.current) return
        setData(next)
        setError(null)
      } catch (err) {
        if (controller.signal.aborted || !aliveRef.current) return
        if (err instanceof DOMException && err.name === 'AbortError') return
        setError(
          err instanceof ApiError
            ? err
            : new ApiError(0, err instanceof Error ? err.message : String(err)),
        )
      } finally {
        if (!controller.signal.aborted && aliveRef.current) setLoading(false)
      }
    },
    [],
  )

  useEffect(() => {
    setData(null)
    setError(null)
    void run(false)
    return () => inFlight.current?.abort()
    // `key` is the identity of the request; `run` is stable.
  }, [key, run])

  const reload = useCallback(
    (opts?: { quiet?: boolean }) => void run(opts?.quiet ?? false),
    [run],
  )

  return { data, error, loading, reload }
}
