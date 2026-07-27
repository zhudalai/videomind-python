import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Link, useNavigate } from 'react-router-dom'
import { ArrowLeft, Construction, AlertCircle } from 'lucide-react'
import { cn } from '@/lib/utils'

interface PlaceholderPageProps {
  title: string
  description: string
  icon?: React.ReactNode
  action?: {
    label: string
    href: string
  }
}

export function PlaceholderPage({ title, description, icon, action }: PlaceholderPageProps) {
  const navigate = useNavigate()

  return (
    <div className="flex flex-col items-center justify-center min-h-[calc(100vh-4rem)] p-8">
      <Card className="w-full max-w-md">
        <CardHeader className="text-center">
          <div className={cn('mx-auto mb-4 p-3 rounded-full bg-muted', 'w-16 h-16 flex items-center justify-center')}>
            {icon || <Construction className="h-8 w-8 text-muted-foreground" />}
          </div>
          <CardTitle className="text-xl">{title}</CardTitle>
          <CardDescription>{description}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <AlertCircle className="h-4 w-4" />
            <span>此功能正在开发中，敬请期待</span>
          </div>
          <div className="flex gap-2">
            <Button variant="outline" onClick={() => navigate(-1)}>
              <ArrowLeft className="h-4 w-4 mr-2" />
              返回
            </Button>
            {action && (
              <Button asChild>
                <Link to={action.href}>{action.label}</Link>
              </Button>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  )
}