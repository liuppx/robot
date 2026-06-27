import { useQuery } from '@tanstack/react-query'

import { api } from '../../lib/api'
import type {
  BotInstanceDiagnoseResponse,
  BotInstanceListResponse,
  BotInstanceLogsResponse,
  RobotListResponse,
  RobotTypesResponse,
  RobotWorkspaceSummary,
} from '../../lib/types'

export function useRobots() {
  return useQuery({
    queryKey: ['robots'],
    queryFn: () => api<RobotListResponse>('/api/v1/public/robots'),
  })
}

export function useRobotWorkspaceSummary(robotKey: string) {
  return useQuery({
    queryKey: ['robot-workspace', robotKey],
    queryFn: () => api<RobotWorkspaceSummary>(`/api/v1/public/robots/${robotKey}/summary`),
    enabled: Boolean(robotKey),
  })
}

export function useMessengerInstances() {
  return useQuery({
    queryKey: ['messenger', 'instances'],
    queryFn: () => api<BotInstanceListResponse>('/api/v1/public/robot/instances'),
  })
}

export function useMessengerInstanceLogs(instanceId: string | null) {
  return useQuery({
    queryKey: ['messenger', 'instance-logs', instanceId],
    queryFn: () => api<BotInstanceLogsResponse>(`/api/v1/public/robot/instances/${instanceId}/logs?lines=260`),
    enabled: Boolean(instanceId),
    refetchInterval: 5000,
  })
}

export function useMessengerInstanceDiagnose(instanceId: string | null) {
  return useQuery({
    queryKey: ['messenger', 'instance-diagnose', instanceId],
    queryFn: () =>
      api<BotInstanceDiagnoseResponse>(`/api/v1/public/robot/instances/${instanceId}/diagnose?auto_recover=false`),
    enabled: Boolean(instanceId),
  })
}

export function useMessengerRobotTypes() {
  return useQuery({
    queryKey: ['messenger', 'robot-types'],
    queryFn: () => api<RobotTypesResponse>('/api/v1/public/robot/types'),
  })
}
