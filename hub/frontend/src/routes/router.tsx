import { createRootRoute, createRoute, createRouter, Outlet } from '@tanstack/react-router'

import { AppShell } from '../components/shell/app-shell'
import { LoginPage } from '../routes/screens/login-page'
import { MessengerPage } from '../routes/screens/messenger-page'
import { RobotsPage } from '../routes/screens/robots-page'
import { TraderPage } from '../routes/screens/trader-page'

function ShellLayout() {
  return (
    <AppShell>
      <Outlet />
    </AppShell>
  )
}

const rootRoute = createRootRoute({
  component: Outlet,
})

const loginRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/',
  component: LoginPage,
})

const shellRoute = createRoute({
  getParentRoute: () => rootRoute,
  id: 'shell',
  component: ShellLayout,
})

const robotsRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: '/robots',
  component: RobotsPage,
})

const traderRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: '/robots/trader',
  component: TraderPage,
})

const messengerRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: '/robots/messenger',
  component: MessengerPage,
})

const routeTree = rootRoute.addChildren([
  loginRoute,
  shellRoute.addChildren([robotsRoute, traderRoute, messengerRoute]),
])

export const router = createRouter({ routeTree })

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}
