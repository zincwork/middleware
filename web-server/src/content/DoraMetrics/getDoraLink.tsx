import Link from 'next/link';
import { GoLinkExternal } from 'react-icons/go';

import { Line } from '@/components/Text';
import { OPEN_IN_NEW_TAB_PROPS } from '@/utils/url';

const FOUR_KEYS_URL = `https://cloud.google.com/blog/products/devops-sre/using-the-four-keys-to-measure-your-devops-performance#:~:text=Calculating%20the%20metrics`;

/** `href` defaults to Google's Four Keys write-up; pass an in-app path to link
 * to our own explanation instead (opens in place, no external-link icon). */
export const getDoraLink = (text: string, href: string = FOUR_KEYS_URL) => (
  <Link
    href={href}
    passHref
    {...(href.startsWith('/') ? {} : OPEN_IN_NEW_TAB_PROPS)}
  >
    <Line
      tiny
      sx={{
        cursor: 'pointer',
        display: 'flex',
        whiteSpace: 'pre',
        alignItems: 'center',
        gap: 1 / 2
      }}
      underline
      dotted
      medium
      white
    >
      <span>{text}</span> {!href.startsWith('/') && <GoLinkExternal />}
    </Line>
  </Link>
);
