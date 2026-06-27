import { useMutation, useQueryClient } from '@tanstack/react-query'

import { api } from '../core/api'
import type {
  BotInstanceActionResponse,
  BotInstanceCreateRequest,
  BotInstancePairResponse,
  RobotWorkspaceActionResponse,
  RobotWorkspaceConfigUpdateResponse,
} from '../core/types'

async function runRobotAction(robotKey: string, action: 'run-once' | 'start' | 'stop') {
  return api<RobotWorkspaceActionResponse>(`/api/v1/public/robots/${robotKey}/actions/${action}`, {
    method: 'POST',
  })
}

async function updateRobotConfig(
  robotKey: string,
  payload: {
    broker: string
    strategy_id?: string | null
    strategy: Record<string, unknown>
  },
) {
  return api<RobotWorkspaceConfigUpdateResponse>(`/api/v1/public/robots/${robotKey}/config`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

async function runMessengerInstanceAction(instanceId: string, action: 'start' | 'stop') {
  return api<BotInstanceActionResponse>(`/api/v1/public/robot/instances/${instanceId}/${action}`, {
    method: 'POST',
  })
}

async function createMessengerInstance(payload: BotInstanceCreateRequest) {
  return api<BotInstanceActionResponse['instance']>('/api/v1/public/robot/instances', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

async function pairMessengerWhatsapp(instanceId: string) {
  return api<BotInstancePairResponse>(`/api/v1/public/robot/instances/${instanceId}/pair-whatsapp`, {
    method: 'POST',
  })
}

function invalidateRobotWorkspace(queryClient: ReturnType<typeof useQueryClient>, robotKey: string) {
  return Promise.all([
    queryClient.invalidateQueries({ queryKey: ['robot-workspace', robotKey] }),
    queryClient.invalidateQueries({ queryKey: ['robots'] }),
  ])
}

function invalidateMessenger(queryClient: ReturnType<typeof useQueryClient>, instanceId?: string) {
  return Promise.all([
    queryClient.invalidateQueries({ queryKey: ['messenger', 'instances'] }),
    queryClient.invalidateQueries({ queryKey: ['robot-workspace', 'messenger'] }),
    ...(instanceId ? [queryClient.invalidateQueries({ queryKey: ['messenger', 'instance-logs', instanceId] })] : []),
    queryClient.invalidateQueries({ queryKey: ['robots'] }),
  ])
}

export function useRobotAction(robotKey: string, action: 'run-once' | 'start' | 'stop') {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: () => runRobotAction(robotKey, action),
    onSuccess: async () => {
      await invalidateRobotWorkspace(queryClient, robotKey)
    },
  })
}

export function useRobotConfigUpdate(robotKey: string) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (payload: { broker: string; strategy_id?: string | null; strategy: Record<string, unknown> }) =>
      updateRobotConfig(robotKey, payload),
    onSuccess: async () => {
      await invalidateRobotWorkspace(queryClient, robotKey)
    },
  })
}

export function useMessengerInstanceAction(instanceId: string | null, action: 'start' | 'stop') {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async () => {
      if (!instanceId) {
        throw new Error('未选择实例')
      }
      return runMessengerInstanceAction(instanceId, action)
    },
    onSuccess: async (_data) => {
      await invalidateMessenger(queryClient, instanceId ?? undefined)
    },
  })
}

export function useMessengerInstanceCreate() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (payload: BotInstanceCreateRequest) => createMessengerInstance(payload),
    onSuccess: async () => {
      await invalidateMessenger(queryClient)
    },
  })
}

export function useMessengerWhatsappPair(instanceId: string | null) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async () => {
      if (!instanceId) {
        throw new Error('未选择实例')
      }
      return pairMessengerWhatsapp(instanceId)
    },
    onSuccess: async () => {
      await invalidateMessenger(queryClient, instanceId ?? undefined)
    },
  })
}
