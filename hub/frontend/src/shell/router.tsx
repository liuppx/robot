import { createRootRoute, createRoute, createRouter, Outlet } from '@tanstack/react-router'

import { LoginPage } from '../apps/hub/login-page'
import { RobotsPage } from '../apps/hub/robots-page'
import { MessengerPage } from '../apps/messenger/messenger-page'
import { TraderPage } from '../apps/trader/trader-page'
import { AppShell } from './app-shell'

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
