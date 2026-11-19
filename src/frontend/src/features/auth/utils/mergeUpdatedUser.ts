import type { ApiUser } from '../api/ApiUser'

/** The cached user updated by a write response, which lacks `can_create` and `linto`. */
export const mergeUpdatedUser = (
  previous: ApiUser | false | undefined,
  updated: ApiUser
): ApiUser => (previous ? { ...previous, ...updated } : updated)
