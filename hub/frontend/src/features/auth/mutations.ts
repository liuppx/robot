import { api } from '../../lib/api'
import { connectWallet, requestOptionalUcan, saveWalletHistory, signWalletMessage } from '../../lib/web3'
import type { AuthChallenge, AuthSession } from '../../lib/types'

export async function walletLogin(): Promise<AuthSession> {
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

export async function logout(): Promise<void> {
  await api('/api/v1/public/auth/logout', {
    method: 'POST',
  })
}
