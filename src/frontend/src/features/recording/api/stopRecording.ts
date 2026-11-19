import { useMutation, UseMutationOptions } from '@tanstack/react-query'
import { fetchApi } from '@/api/fetchApi'
import type { ApiError } from '@/api/ApiError'

export interface StopRecordingParams {
  id: string
}

export interface StopRecordingResponse {
  message: string
  // The room owner receives the recording: is it the user who stopped it?
  recipient_is_you?: boolean
  share_summary?: boolean
}

const stopRecording = ({
  id,
}: StopRecordingParams): Promise<StopRecordingResponse> => {
  return fetchApi(`rooms/${id}/stop-recording/`, {
    method: 'POST',
  })
}

export function useStopRecording(
  options?: UseMutationOptions<
    StopRecordingResponse,
    ApiError,
    StopRecordingParams
  >
) {
  return useMutation<StopRecordingResponse, ApiError, StopRecordingParams>({
    mutationFn: stopRecording,
    onSuccess: options?.onSuccess,
    onError: options?.onError,
  })
}
