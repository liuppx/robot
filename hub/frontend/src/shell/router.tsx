import { createRootRoute, createRoute, createRouter, Outlet } from '@tanstack/react-router'

import { LoginPage } from '../apps/hub/login-page'
import { RobotsPage } from '../apps/hub/robots-page'
import { MessengerPage } from '../apps/messenger/messenger-page'
import { TraderHomePage } from '../apps/trader/trader-home-page'
import { TraderRecordPage } from '../apps/trader/trader-record-page'
import { TraderStrategyPage } from '../apps/trader/trader-strategy-page'
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
  component: TraderHomePage,
})

const traderStrategyRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: '/robots/trader/$strategyId',
  component: TraderStrategyPage,
})

const traderRecordRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: '/robots/trader/$strategyId/records/$recordId',
  component: TraderRecordPage,
})

const messengerRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: '/robots/messenger',
  component: MessengerPage,
})

const routeTree = rootRoute.addChildren([
  loginRoute,
  shellRoute.addChildren([robotsRoute, traderRoute, traderStrategyRoute, traderRecordRoute, messengerRoute]),
])

export const router = createRouter({ routeTree })

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}
