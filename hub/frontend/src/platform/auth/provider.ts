import { api } from '../core/api'
import type { AuthChallenge, AuthSession, PlatformSession } from '../core/types'
import { connectWallet, requestOptionalUcan, saveWalletHistory, signWalletMessage } from './web3'

export type PlatformAuthProvider = {
  id: 'cookie-wallet' | 'ucan'
  getSession: () => Promise<AuthSession>
  deriveSession: (raw: AuthSession | null | undefined) => PlatformSession
  login: () => Promise<AuthSession>
  logout: () => Promise<void>
}

async function fetchCookieWalletSession(): Promise<AuthSession> {
  return api<AuthSession>('/api/v1/public/auth/me')
}

async function loginWithCookieWallet(): Promise<AuthSession> {
  const { wallet, chainId, provider } = await connectWallet()

  const challenge = await api<AuthChallenge>('/api/v1/public/auth/wallet/challenge', {
    method: 'POST',
    body: JSON.stringify({
      wallet_id: wallet,
      chain_id: chainId,
    }),
  })

  const signature = await signWalletMessage({
    provider,
    wallet,
    message: challenge.challenge,
  })

  const { ucanSession, ucanSignature } = await requestOptionalUcan({ provider })

  const session = await api<AuthSession>('/api/v1/public/auth/wallet/verify', {
    method: 'POST',
    body: JSON.stringify({
      wallet_id: wallet,
      chain_id: chainId,
      challenge: challenge.challenge,
      challenge_token: challenge.challenge_token,
      signature,
      ucan_session: ucanSession,
      ucan_signature: ucanSignature,
    }),
  })

  saveWalletHistory(wallet)
  return session
}

async function logoutCurrentSession(): Promise<void> {
  await api('/api/v1/public/auth/logout', {
    method: 'POST',
  })
}

export function createCookieWalletAuthProvider(
  deriveSession: (raw: AuthSession | null | undefined) => PlatformSession,
): PlatformAuthProvider {
  return {
    id: 'cookie-wallet',
    getSession: fetchCookieWalletSession,
    deriveSession,
    login: loginWithCookieWallet,
    logout: logoutCurrentSession,
  }
}
