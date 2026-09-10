import { useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import { IDLE_KEY_CHECK, type KeyCheck } from '../lib/createDraft'

export const KEY_CHECK_DEBOUNCE_MS = 400

/**
 * Live `POST /apps/validate-key` behind the key field.
 *
 * Ported from assembled.work's `checkNameAvailability`, including the two
 * things that make it usable rather than infuriating:
 *
 * 1. A changed key immediately invalidates the previous verdict, then
 *    re-checks after a pause — otherwise the user stares at a disabled
 *    Continue and a stale green tick.
 * 2. A sequence guard, so a slow early request resolving last cannot
 *    overwrite the answer for what is now in the field.
 *
 * The rejection text is never paraphrased: portal-p2a.md deliberately words
 * the denylist refusal ("pick a project codename") so it does not reveal why
 * a specific term is banned, and that wording only survives if we print it.
 */
export function useKeyAvailability(
  key: string,
  delayMs = KEY_CHECK_DEBOUNCE_MS,
): KeyCheck {
  const [check, setCheck] = useState<KeyCheck>(IDLE_KEY_CHECK)
  const seq = useRef(0)

  useEffect(() => {
    const trimmed = key.trim()
    const mine = ++seq.current

    if (trimmed === '') {
      setCheck(IDLE_KEY_CHECK)
      return
    }

    // The old answer is about the old key. Say so while the new one is typed.
    setCheck(IDLE_KEY_CHECK)

    const controller = new AbortController()
    const timer = setTimeout(() => {
      setCheck({ state: 'checking', host: null, reason: null })
      api
        .validateKey(trimmed, controller.signal)
        .then((result) => {
          if (seq.current !== mine) return
          setCheck(
            result.ok
              ? { state: 'ok', host: result.host ?? null, reason: null }
              : {
                  state: 'rejected',
                  host: null,
                  reason: result.reason ?? 'That key cannot be used.',
                },
          )
        })
        .catch((err: unknown) => {
          if (seq.current !== mine) return
          if (err instanceof DOMException && err.name === 'AbortError') return
          setCheck({
            state: 'unknown',
            host: null,
            reason: err instanceof Error ? err.message : String(err),
          })
        })
    }, delayMs)

    return () => {
      clearTimeout(timer)
      controller.abort()
    }
  }, [key, delayMs])

  return check
}
