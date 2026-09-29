// Edge-safe helpers shared by middleware.ts and the next-auth route.

// The bypass only works in dev builds. The root .env (which has
// AUTH_DISABLED=true locally) is loaded by next.config.js and copied into the
// prod image, so the prod image must never honour it.
export const isAuthDisabled = () =>
  process.env.AUTH_DISABLED === 'true' &&
  process.env.NEXT_PUBLIC_APP_ENVIRONMENT === 'development';

export const isAllowedEmail = (email?: string | null) => {
  if (!email) return false;
  const domain = (process.env.ALLOWED_EMAIL_DOMAIN || 'zincwork.com')
    .toLowerCase()
    .replace(/^@/, '');
  return email.toLowerCase().endsWith(`@${domain}`);
};
