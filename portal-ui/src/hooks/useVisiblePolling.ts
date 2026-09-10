import { useEffect, useRef } from 'react'

/**
 * Call `tick` every `intervalMs` while the tab is visible. Hidden tabs stop
 * polling entirely and fire one catch-up tick the moment they come back, so a
 * laptop left open overnight does not hammer the proxy (or wake apps by
 * accident -- these are all GETs, but the log noise is real).
 *
 * P1 has no websockets on purpose; this is the whole live-status mechanism.
 */
export function useVisiblePolling(
  tick: () => void,
  intervalMs = 15_000,
  enabled = true,
): void {
  const tickRef = useRef(tick)
  tickRef.current = tick

  useEffect(() => {
    if (!enabled) return

    let timer: ReturnType<typeof setInterval> | undefined

    const start = () => {
      if (timer !== undefined) return
      timer = setInterval(() => tickRef.current(), intervalMs)
    }
    const stop = () => {
      if (timer === undefined) return
      clearInterval(timer)
      timer = undefined
    }
    const onVisibility = () => {
      if (document.visibilityState === 'visible') {
        tickRef.current()
        start()
      } else {
        stop()
      }
    }

    if (document.visibilityState === 'visible') start()
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      stop()
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [intervalMs, enabled])
}
