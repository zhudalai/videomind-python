import { Link, NavLink } from 'react-router-dom'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/Button'
import {
  LayoutDashboard,
  Upload,
  Video,
  MessageSquare,
  Brain,
  Activity,
  Settings,
  ChevronLeft,
  ChevronRight,
} from 'lucide-react'
import { useState } from 'react'

const navigation = [
  { name: '概览', href: '/', icon: LayoutDashboard },
  { name: '上传视频', href: '/upload', icon: Upload },
  { name: '视频库', href: '/videos', icon: Video },
  { name: '智能问答', href: '/chat', icon: MessageSquare },
  { name: '深度分析', href: '/analysis', icon: Brain },
  { name: '健康监控', href: '/health', icon: Activity },
  { name: '设置', href: '/settings', icon: Settings },
]

export function Sidebar() {
  const [collapsed, setCollapsed] = useState(false)

  return (
    <aside
      className={cn(
        'fixed left-0 top-0 z-40 h-screen border-r bg-card transition-all duration-300',
        collapsed ? 'w-16' : 'w-64',
      )}
    >
      <div className="flex h-full flex-col">
        {/* Logo */}
        <div className={cn('flex h-16 items-center justify-between border-b px-4', collapsed && 'justify-center')}>
          {!collapsed && (
            <Link to="/" className="flex items-center gap-2 font-bold text-xl text-primary">
              <Video className="h-6 w-6" />
              <span>VideoMind</span>
            </Link>
          )}
          <Button
            variant="ghost"
            size="icon"
            onClick={() => setCollapsed(!collapsed)}
            className="h-8 w-8"
            aria-label={collapsed ? '展开侧边栏' : '折叠侧边栏'}
          >
            {collapsed ? <ChevronRight className="h-4 w-4" /> : <ChevronLeft className="h-4 w-4" />}
          </Button>
        </div>

        {/* Navigation */}
        <nav className="flex-1 space-y-1 p-3" aria-label="主导航">
          {navigation.map((item) => {
            const Icon = item.icon
            return (
              <NavLink
                key={item.name}
                to={item.href}
                className={({ isActive }) =>
                  cn(
                    'flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors',
                    isActive
                      ? 'bg-primary text-primary-foreground shadow-sm'
                      : 'text-muted-foreground hover:bg-accent hover:text-accent-foreground',
                    collapsed && 'justify-center',
                  )
                }
                title={collapsed ? item.name : undefined}
              >
                <Icon className="h-5 w-5 flex-shrink-0" aria-hidden="true" />
                {!collapsed && <span>{item.name}</span>}
              </NavLink>
            )
          })}
        </nav>

        {/* Bottom status */}
        <div className={cn('border-t p-3', collapsed && 'hidden')}>
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <span className="flex h-2 w-2 rounded-full bg-green-500" />
            <span>系统运行正常</span>
          </div>
        </div>
      </div>
    </aside>
  )
}