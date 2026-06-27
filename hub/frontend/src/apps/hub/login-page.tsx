import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { CheckCircle2, LoaderCircle, ShieldCheck, Wallet } from 'lucide-react'

import { walletLogin } from '../../platform/auth/mutations'
import { usePlatformSession } from '../../platform/auth/queries'
import { loadWalletHistory, shortWallet } from '../../platform/auth/web3'
import { ApiError } from '../../platform/core/api'

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return error.message
  }
  if (error instanceof Error) {
    return error.message
  }
  return '登录失败'
}

export function LoginPage() {
  const navigate = useNavigate()
  const { session, isLoading, refetch } = usePlatformSession()
  const [submitting, setSubmitting] = useState(false)
  const [statusText, setStatusText] = useState<string | null>(null)
  const [errorText, setErrorText] = useState<string | null>(null)
  const walletHistory = useMemo(() => loadWalletHistory(), [])

  useEffect(() => {
    if (session.isAuthenticated) {
      void navigate({ to: '/robots', replace: true })
    }
  }, [navigate, session])

  async function handleLogin() {
    setSubmitting(true)
    setErrorText(null)
    setStatusText('正在请求钱包签名...')
    try {
      await walletLogin()
      setStatusText('登录成功，正在进入机器人列表...')
      await refetch()
      await navigate({ to: '/robots', replace: true })
    } catch (error) {
      setErrorText(errorMessage(error))
      setStatusText(null)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="min-h-screen bg-slate-100 px-6 py-10">
      <div className="mx-auto grid max-w-5xl gap-6 lg:grid-cols-[1.15fr_0.85fr]">
        <section className="rounded-2xl border border-slate-200 bg-white p-8 shadow-sm">
          <div className="mb-10 flex items-center gap-3">
            <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-sky-600 text-sm font-semibold text-white">
              HB
            </div>
            <div>
              <h1 className="text-2xl font-semibold text-slate-950">Hub</h1>
              <p className="text-sm text-slate-500">机器人控制面 React 工作区</p>
            </div>
          </div>
          <div className="space-y-4">
            <h2 className="text-3xl font-semibold tracking-tight text-slate-950">钱包登录入口</h2>
            <p className="max-w-2xl text-sm leading-6 text-slate-600">
              使用现有 Hub 后端的 challenge / verify / me / logout 接口建立会话。登录成功后进入机器人列表，
              当前先验证认证闭环，不替换默认生产入口。
            </p>
          </div>
          <div className="mt-8 flex flex-wrap gap-3">
            <button
              type="button"
              onClick={() => void handleLogin()}
              disabled={submitting || isLoading}
              className="inline-flex h-11 items-center gap-2 rounded-lg bg-sky-600 px-4 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-60"
            >
              {submitting || isLoading ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Wallet className="h-4 w-4" />}
              {submitting ? '等待签名' : '连接钱包'}
            </button>
            <button
              type="button"
              onClick={() => void refetch()}
              className="inline-flex h-11 items-center rounded-lg border border-slate-200 bg-white px-4 text-sm font-medium text-slate-700"
            >
              刷新会话
            </button>
          </div>
          {statusText && (
            <div className="mt-4 inline-flex items-center gap-2 rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-700">
              <CheckCircle2 className="h-4 w-4" />
              {statusText}
            </div>
          )}
          {errorText && (
            <div className="mt-4 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
              {errorText}
            </div>
          )}
          {session.walletId && (
            <div className="mt-4 rounded-lg border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-700">
              当前会话：{shortWallet(session.walletId)}
            </div>
          )}
        </section>
        <section className="grid gap-4">
          {[
            ['认证闭环', '已接入 challenge / verify / me / logout'],
            ['机器人列表', '登录成功后跳转到 /robots'],
            ['能力模型', session.capability.protocol === 'ucan' ? '已携带 UCAN 能力信息' : '当前仍以 cookie-wallet 会话为主'],
          ].map(([title, desc]) => (
            <div key={title} className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
              <div className="text-sm font-semibold text-slate-900">{title}</div>
              <div className="mt-2 text-sm text-slate-500">{desc}</div>
            </div>
          ))}
          <div className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
            <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-900">
              <ShieldCheck className="h-4 w-4" />
              历史钱包
            </div>
            <div className="flex flex-wrap gap-2">
              {walletHistory.length ? (
                walletHistory.map((wallet) => (
                  <span
                    key={wallet}
                    className="inline-flex h-9 items-center rounded-full border border-slate-200 bg-slate-50 px-3 text-sm text-slate-600"
                    title={wallet}
                  >
                    {shortWallet(wallet)}
                  </span>
                ))
              ) : (
                <span className="text-sm text-slate-500">暂无历史钱包</span>
              )}
            </div>
          </div>
        </section>
      </div>
    </div>
  )
}
