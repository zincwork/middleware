import { NextRequest, NextResponse } from 'next/server';
import { getToken } from 'next-auth/jwt';

import { getFeaturesFromReq } from '@/api-helpers/features';
import { defaultFlags } from '@/constants/feature';
import { isAllowedEmail, isAuthDisabled } from '@/utils/auth-gate';

// Routes that previously got the feature_flags rewrite
const FEATURE_FLAG_PREFIXES = [
  '/api/auth/session',
  '/api/integrations',
  '/api/internal',
  '/api/resources'
];

// next-auth's own endpoints (except our /api/auth/session) and the ALB health check
const isPublicPath = (pathname: string) =>
  (pathname.startsWith('/api/auth') &&
    !pathname.startsWith('/api/auth/session')) ||
  pathname === '/api/status';

export async function middleware(request: NextRequest) {
  const url = request.nextUrl.clone();
  const { pathname } = url;

  if (isPublicPath(pathname)) {
    return NextResponse.next();
  }

  if (!isAuthDisabled()) {
    const token = await getToken({
      req: request as any,
      secret: process.env.NEXTAUTH_SECRET
    });

    if (!token || !isAllowedEmail(token.email)) {
      if (pathname.startsWith('/api')) {
        return NextResponse.json({ error: 'unauthenticated' }, { status: 401 });
      }
      // Behind the ALB request.url is plain http, so prefer the public URL
      const signInUrl = new URL(
        '/api/auth/signin',
        process.env.NEXTAUTH_URL || request.url
      );
      signInUrl.searchParams.set('callbackUrl', `${pathname}${url.search}`);
      return NextResponse.redirect(signInUrl);
    }
  }

  if (!FEATURE_FLAG_PREFIXES.some((prefix) => pathname.startsWith(prefix))) {
    return NextResponse.next();
  }

  const flagOverrides = getFeaturesFromReq(request as any);
  const flags = { ...defaultFlags, ...flagOverrides };
  url.searchParams.append('feature_flags', JSON.stringify(flags));

  return NextResponse.rewrite(url);
}

export const config = {
  matcher: [
    // Everything except Next.js internals and static files in public/
    '/((?!_next/static|_next/image|favicon.ico|assets/|static/|icon-|manifest.json|robots.txt|imageStatusApiWorker.js).*)'
  ]
};
