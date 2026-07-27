import { cn } from '@/lib/utils'
import { Sidebar } from './Sidebar'
import { Header } from './Header'
import { Outlet } from 'react-router-dom'

export function MainLayout() {
  return (
    <div className="min-h-screen bg-background">
      <Sidebar />
      <div className={cn('transition-all duration-300 lg:ml-64', 'min-h-screen')}>
        <Header />
        <main className="p-4 lg:p-6">
          <Outlet />
        </main>
      </div>
    </div>
  )
}