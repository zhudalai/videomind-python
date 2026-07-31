import { createContext, useContext, useEffect, useRef, useState } from 'react'
import i18n from '@/i18n'
import { userApi } from '@/lib/api'
import { useQuery } from '@tanstack/react-query'

interface LanguageContextType {
  currentLanguage: string
  setLanguage: (lang: string) => void
}

const LanguageContext = createContext<LanguageContextType>({
  currentLanguage: 'en-US',
  setLanguage: () => {},
})

export function LanguageProvider({ children }: { children: React.ReactNode }) {
  const { data: config } = useQuery({
    queryKey: ['user', 'config'],
    queryFn: () => userApi.getConfig(),
    staleTime: 60000,
  })

  const [currentLanguage, setCurrentLanguage] = useState(i18n.language || 'en-US')
  // Sync ONCE: only the first time DB config resolves — subsequent refetches / invalidations
  // must not override the user's in-UI choice before they explicitly save.
  const hasSyncedRef = useRef(false)

  useEffect(() => {
    if (hasSyncedRef.current) return
    if (!config?.language) return
    const lang = config.language as string
    if (lang !== currentLanguage) {
      setCurrentLanguage(lang)
      void i18n.changeLanguage(lang)
    }
    hasSyncedRef.current = true
  }, [config, currentLanguage])

  const setLanguage = (lang: string) => {
    setCurrentLanguage(lang)
    void i18n.changeLanguage(lang)
  }

  return (
    <LanguageContext.Provider value={{ currentLanguage, setLanguage }}>
      {children}
  </LanguageContext.Provider>
  )
}

export function useLanguage() {
  return useContext(LanguageContext)
}
