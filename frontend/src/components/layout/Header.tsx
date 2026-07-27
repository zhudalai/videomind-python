import { Button } from '@/components/ui/Button'
import { Bell, Search, Moon, Sun, Menu } from 'lucide-react'
import { useState } from 'react'

export function Header() {
  const [darkMode, setDarkMode] = useState(false)

  return (
    <header className="sticky top-0 z-30 flex h-16 items-center gap-4 border-b bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60 px-4">
      <div className="flex w-full items-center justify-between gap-4">
        {/* Left: Mobile menu button + Search */}
        <div className="flex items-center gap-4">
          <Button variant="ghost" size="icon" className="lg:hidden" aria-label="打开菜单">
            <Menu className="h-5 w-5" />
          </Button>
          <div className="relative hidden sm:block">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <input
              type="search"
              placeholder="搜索视频、内容..."
              className="h-9 w-64 rounded-md border bg-background px-9 py-1.5 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring focus:border-transparent"
              aria-label="搜索"
            />
          </div>
        </div>

        {/* Right: Theme toggle, Notifications, User */}
        <div className="flex items-center gap-2">
          <Button
            variant="ghost"
            size="icon"
            onClick={() => setDarkMode(!darkMode)}
            aria-label={darkMode ? '切换到浅色模式' : '切换到深色模式'}
          >
            {darkMode ? <Sun className="h-5 w-5" /> : <Moon className="h-5 w-5" />}
          </Button>
          <Button variant="ghost" size="icon" aria-label="通知">
            <Bell className="h-5 w-5" />
          </Button>
          <div className="relative">
            <Button variant="ghost" className="gap-2" aria-label="用户菜单">
              <div className="flex h-8 w-8 items-center justify-center rounded-full bg-primary text-primary-foreground text-sm font-medium">
                用
              </div>
              <span className="hidden sm:block">用户</span>
            </Button>
          </div>
        </div>
      </div>
    </header>
  )
}