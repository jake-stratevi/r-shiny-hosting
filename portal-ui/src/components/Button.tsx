import type { AnchorHTMLAttributes, ButtonHTMLAttributes, ReactNode } from 'react'
import { Link } from 'react-router-dom'

export type ButtonVariant = 'primary' | 'outline' | 'ghost' | 'danger'
export type ButtonSize = 'sm' | 'md'

const BASE =
  'inline-flex select-none items-center justify-center gap-1.5 rounded-tile font-medium transition-colors disabled:cursor-not-allowed'

const SIZES: Record<ButtonSize, string> = {
  sm: 'px-2.5 py-1.5 text-[13px]',
  md: 'px-3.5 py-2 text-sm',
}

const VARIANTS: Record<ButtonVariant, string> = {
  primary:
    'bg-accent text-white hover:bg-accent-hover disabled:bg-line disabled:text-faint',
  outline:
    'border border-line bg-surface text-ink hover:border-accent-line hover:bg-accent-soft hover:text-accent disabled:border-line-soft disabled:bg-canvas disabled:text-faint disabled:hover:bg-canvas disabled:hover:text-faint',
  ghost:
    'text-muted hover:bg-canvas hover:text-ink disabled:text-faint disabled:hover:bg-transparent',
  danger:
    'bg-amber-700 text-white hover:bg-amber-800 disabled:bg-line disabled:text-faint',
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
