import { LintoBotProfile } from '../types/linto'

const codes = (value: unknown): string[] =>
  Array.isArray(value)
    ? value.filter((code): code is string => typeof code === 'string' && !!code)
    : []

/**
 * A profile from GET rooms/{id}/linto/profiles as the panel uses it. The
 * backend sends language codes; anything else is dropped here, since the
 * pickers render every entry as a code and a single object among them (a
 * Studio translator, once) unmounted the whole meeting page.
 */
export const sanitizeLintoProfile = (
  profile: LintoBotProfile
): LintoBotProfile => ({
  ...profile,
  languages: codes(profile.languages),
  translations: codes(profile.translations),
})
