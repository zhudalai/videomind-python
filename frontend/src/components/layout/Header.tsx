import { Button } from '@/components/ui/Button'
import { Bell, Search, Moon, Sun, Menu } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

export function Header() {
  const { t } = useTranslation()
  const [darkMode, setDarkMode] = useState(false)

  return (
    <header className="sticky top-0 z-30 flex h-16 items-center gap-4 border-b bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60 px-4">
      <div className="flex w-full items-center justify-between gap-4">
        {/* Left: Mobile menu button + Search */}
        <div className="flex items-center gap-4">
          <Button variant="ghost" size="icon" className="lg:hidden" aria-label={t('header.openMenu')}>
            <Menu className="h-5 w-5" />
          </Button>
          <div className="relative hidden sm:block">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <input
              type="search"
              placeholder={t('header.searchPlaceholder')}
              className="h-9 w-64 rounded-md border bg-background px-9 py-1.5 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring focus:border-transparent"
              aria-label={t('header.search')}
            />
          </div>
        </div>

        {/* Right: Theme toggle, Notifications, User */}
        <div className="flex items-center gap-2">
          <Button
            variant="ghost"
            size="icon"
            onClick={() => setDarkMode(!darkMode)}
            aria-label={darkMode ? t('header.switchToLight') : t('header.switchToDark')}
          >
            {darkMode ? <Sun className="h-5 w-5" /> : <Moon className="h-5 w-5" />}
          </Button>
          <Button variant="ghost" size="icon" aria-label={t('header.notifications')}>
            <Bell className="h-5 w-5" />
          </Button>
          <div className="relative">
            <Button variant="ghost" className="gap-2" aria-label={t('header.userMenu')}>
              <div className="flex h-8 w-8 items-center justify-center rounded-full bg-primary text-primary-foreground text-sm font-medium">
                {t('header.userAvatar')}
              </div>
              <span className="hidden sm:block">{t('header.user')}</span>
            </Button>
          </div>
        </div>
      </div>
    </header>
  )
}
