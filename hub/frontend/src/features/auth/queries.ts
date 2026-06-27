import { useQuery } from '@tanstack/react-query'

import { api } from '../../lib/api'
import type { AuthSession } from '../../lib/types'

export function useAuthSession() {
  return useQuery({
    queryKey: ['auth', 'me'],
    queryFn: () => api<AuthSession>('/api/v1/public/auth/me'),
    retry: false,
  })
}
