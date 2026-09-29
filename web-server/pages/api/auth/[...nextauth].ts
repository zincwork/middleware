import NextAuth, { NextAuthOptions } from 'next-auth';
import Auth0Provider from 'next-auth/providers/auth0';

import { isAllowedEmail } from '@/utils/auth-gate';

// Note: /api/auth/session is served by our own pages/api/auth/session.ts
// (org details), which takes precedence over this catch-all. So next-auth's
// client-side useSession/SessionProvider can't be used; the app is gated
// server-side in middleware.ts via getToken() instead.
export const authOptions: NextAuthOptions = {
  providers: [
    Auth0Provider({
      clientId: process.env.AUTH0_CLIENT_ID,
      clientSecret: process.env.AUTH0_CLIENT_SECRET,
      issuer: process.env.AUTH0_ISSUER
    })
  ],
  secret: process.env.NEXTAUTH_SECRET,
  session: { strategy: 'jwt', maxAge: 8 * 60 * 60 },
  callbacks: {
    async signIn({ profile }) {
      const p = profile as { email?: string; email_verified?: boolean };
      return p?.email_verified === true && isAllowedEmail(p.email);
    },
    async jwt({ token, profile }) {
      if (profile?.email) token.email = profile.email;
      return token;
    }
  }
};

export default NextAuth(authOptions);
