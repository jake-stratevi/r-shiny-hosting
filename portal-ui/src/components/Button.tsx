import type { AnchorHTMLAttributes, ButtonHTMLAttributes, ReactNode } from 'react'
import { Link } from 'react-router-dom'

export type ButtonVariant = 'primary' | 'outline' | 'ghost' | 'danger'
export type ButtonSize = 'sm' | 'md' | 'icon'

/**
 * Buttons follow the house rule stated with the tokens in index.css:
 * **primary actions are ink**, never azure. A near-black button is the
 * editorial move; azure is the wayfinding move and stays on focus rings,
 * links and the active trail. So `primary` is `bg-primary` — ink on paper in
 * light, paper on ink in dark — and nothing in this file reaches for a hue.
 */
const BASE =
  'inline-flex shrink-0 select-none items-center justify-center gap-2 whitespace-nowrap rounded-md font-medium transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:ring-offset-1 focus-visible:ring-offset-background disabled:pointer-events-none disabled:opacity-50'

const SIZES: Record<ButtonSize, string> = {
  sm: 'h-8 px-3 text-[13px]',
  md: 'h-9 px-4 text-sm',
  icon: 'h-8 w-8',
}

const VARIANTS: Record<ButtonVariant, string> = {
  primary: 'bg-primary text-primary-foreground hover:bg-primary/90',
  outline:
    'border border-border bg-card text-foreground hover:bg-accent hover:text-accent-foreground dark:bg-input/30 dark:hover:bg-input/50',
  ghost: 'text-muted-foreground hover:bg-accent hover:text-accent-foreground',
  // Red is one of the four state colours; a destructive submit is the one
  // place a button is allowed to wear one.
  danger: 'bg-destructive text-destructive-foreground hover:bg-destructive/90',
}

export function buttonClass(
  variant: ButtonVariant = 'primary',
  size: ButtonSize = 'md',
  className = '',
): string {
  return `${BASE} ${SIZES[size]} ${VARIANTS[variant]} ${className}`
}

interface Common {
  variant?: ButtonVariant
  size?: ButtonSize
  children: ReactNode
  className?: string
}

export function Button({
  variant,
  size,
  className,
  ...rest
}: Common & ButtonHTMLAttributes<HTMLButtonElement>) {
  return <button className={buttonClass(variant, size, className)} {...rest} />
}

/** Same skin on an <a> — used for anything leaving the SPA. */
export function ButtonLink({
  variant,
  size,
  className,
  ...rest
}: Common & AnchorHTMLAttributes<HTMLAnchorElement>) {
  return <a className={buttonClass(variant, size, className)} {...rest} />
}

/** Same skin on a router link. */
export function ButtonRoute({
  variant,
  size,
  className,
  to,
  children,
}: Common & { to: string }) {
  return (
    <Link to={to} className={buttonClass(variant, size, className)}>
      {children}
    </Link>
  )
}
