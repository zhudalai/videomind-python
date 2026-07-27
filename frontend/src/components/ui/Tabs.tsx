import * as React from 'react'
import { cn } from '@/lib/utils'

export interface TabsProps extends React.HTMLAttributes<HTMLDivElement> {
  value: string
  onValueChange: (value: string) => void
  children: React.ReactNode
}

export interface TabsListProps extends React.HTMLAttributes<HTMLDivElement> {
  children: React.ReactNode
}

export interface TabsTriggerProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  value: string
  disabled?: boolean
}

export interface TabsContentProps extends React.HTMLAttributes<HTMLDivElement> {
  value: string
  forceMount?: boolean
}

const Tabs = React.forwardRef<HTMLDivElement, TabsProps>(
  ({ className, value, onValueChange, children, ...props }, ref) => (
    <div ref={ref} className={cn('flex flex-col', className)} {...props}>
      {children}
    </div>
  ),
)
Tabs.displayName = 'Tabs'

const TabsList = React.forwardRef<HTMLDivElement, TabsListProps>(
  ({ className, children, ...props }, ref) => (
    <div
      ref={ref}
      className={cn(
        'inline-flex h-10 items-center justify-center rounded-md bg-muted p-1 text-muted-foreground',
        className,
      )}
      {...props}
    >
      {children}
    </div>
  ),
)
TabsList.displayName = 'TabsList'

const TabsTrigger = React.forwardRef<HTMLButtonElement, TabsTriggerProps>(
  ({ className, value, disabled, onClick, ...props }, ref) => (
    <button
      ref={ref}
      className={cn(
        'inline-flex items-center justify-center whitespace-nowrap rounded-sm px-3 py-1.5 text-sm font-medium ring-offset-background transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50 data-[state=active]:bg-background data-[state=active]:text-foreground data-[state=active]:shadow-sm',
        className,
      )}
      value={value}
      disabled={disabled}
      onClick={(e) => {
        onClick?.(e)
        if (!disabled) {
          const parent = e.currentTarget.closest('[role="tablist"]')
          if (parent) {
            parent.querySelectorAll('[role="tab"]').forEach(el => {
              el.setAttribute('data-state', el === e.currentTarget ? 'active' : 'inactive')
            })
          }
        }
      }}
      {...props}
    />
  ),
)
TabsTrigger.displayName = 'TabsTrigger'

const TabsContent = React.forwardRef<HTMLDivElement, TabsContentProps>(
  ({ className, value, forceMount, children, ...props }, ref) => (
    <div
      ref={ref}
      className={cn(
        'mt-2 ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2',
        className,
      )}
      {...props}
    >
      {children}
    </div>
  ),
)
TabsContent.displayName = 'TabsContent'

export { Tabs, TabsList, TabsTrigger, TabsContent }