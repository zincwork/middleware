import { Paper } from '@mui/material';
import { FC, ReactNode } from 'react';

import { FlexBox } from '@/components/FlexBox';
import { Line } from '@/components/Text';

/**
 * A titled panel.
 *
 * Deliberately not `CardRoot` from the DORA cards — that carries
 * `cursor: pointer` and a hover brightness change because every DORA card
 * opens an overlay. Nothing on this page is clickable, and a card that looks
 * clickable but is not is worse than a plain one.
 */
export const CockpitSection: FC<{
  title: string;
  subtitle?: ReactNode;
  children: ReactNode;
}> = ({ title, subtitle, children }) => (
  <FlexBox col gap1 fullWidth component={Paper} p={2}>
    <FlexBox col>
      <Line big medium>
        {title}
      </Line>
      {subtitle ? (
        <Line tiny secondary>
          {subtitle}
        </Line>
      ) : null}
    </FlexBox>
    {children}
  </FlexBox>
);
