import {
  Alert,
  Box,
  Paper,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow
} from '@mui/material';
import { FC, ReactNode } from 'react';

import { FlexBox } from '@/components/FlexBox';
import { Line } from '@/components/Text';

/**
 * How change failure rate is calculated in Zinc's Middleware.
 *
 * Linked from the "?" on the Change Failure Rate card. Upstream Middleware
 * only counts revert PRs, blamed on the last deploy before them; this fork
 * adds rollbacks and Shortcut regressions and matches deploys to PRs by
 * commit. Keep this page in step with backend/analytics_server/mhq/service/
 * incidents (regressions.py, rollbacks.py, incidents.py) and
 * service/deployments/deployment_commits.py.
 */
export const ChangeFailureRateGuide: FC = () => (
  <FlexBox col gap={2} fullWidth maxWidth="900px" pb={4}>
    <Section title="What it measures">
      <P>
        Of the production deployments in the selected period, the share that
        caused a failure.
      </P>
      <Formula>
        Change failure rate = failed deployments ÷ all production deployments
      </Formula>
      <P>
        It counts <b>deployments</b>, not PRs or bugs. A deployment either
        failed or it didn&apos;t: one bad PR in a batch of ten fails the whole
        deployment, and three bugs traced to the same deployment still count as
        one failure.
      </P>
    </Section>

    <Section title="What counts as a deployment">
      <P>
        For repos set to use CircleCI (currently <Code>mvp-api</Code> and{' '}
        <Code>mvp-app</Code>), a deployment is a successful run of the
        production deploy job on <Code>main</Code>, e.g.{' '}
        <Code>build-and-deploy/deploy-production</Code>. Pipelines whose
        approval gate was never approved are not deployments. Repos without a
        deploy workflow count each PR merged to the production branch as a
        deployment.
      </P>
      <P>
        Each CircleCI deployment records the commit it shipped. Middleware
        compares it with the previous deployment&apos;s commit to work out
        exactly which PRs went out in it. It does this by commit rather than by
        time because deploys wait for approval: approving an older pipeline
        after a newer PR has merged does not ship the newer PR.
      </P>
    </Section>

    <Section title="What counts as a failure">
      <P>A deployment fails when any of these points at it:</P>
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Signal</TableCell>
            <TableCell>How it&apos;s detected</TableCell>
            <TableCell>Which deployment fails</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          <TableRow>
            <TableCell>
              <b>Rollback</b>
            </TableCell>
            <TableCell>
              Automatic. A deploy job ships an <i>older</i> commit than the one
              already live.
            </TableCell>
            <TableCell>
              Every deployment since that older commit was last live, or just
              the previous one if it never was. Not the rollback itself.
            </TableCell>
          </TableRow>
          <TableRow>
            <TableCell>
              <b>Revert</b>
            </TableCell>
            <TableCell>
              A PR made with GitHub&apos;s <b>Revert</b> button (its branch is
              named <Code>revert-123-…</Code>).
            </TableCell>
            <TableCell>The deployment that shipped the reverted PR.</TableCell>
          </TableRow>
          <TableRow>
            <TableCell>
              <b>Regression</b>
            </TableCell>
            <TableCell>
              A Shortcut bug labelled <Code>regression</Code>, whose linked fix
              PR names the PR that caused it.
            </TableCell>
            <TableCell>
              The deployment that shipped the PR the fix names.
            </TableCell>
          </TableRow>
        </TableBody>
      </Table>
      <P>
        A revert and a regression that point at the same PR are one incident,
        not two.
      </P>
    </Section>

    <Section title="How a regression is traced to a deployment">
      <FlexBox col gap={1 / 2}>
        <Step n={1}>
          The Shortcut bug is labelled <Code>regression</Code>.
        </Step>
        <Step n={2}>
          Its fix PR is linked to the story. Shortcut does this when the branch
          or PR mentions the story, e.g. a branch{' '}
          <Code>blue/sc-4821/fix-basket-total</Code> or <Code>[sc-4821]</Code>{' '}
          in the PR title.
        </Step>
        <Step n={3}>
          The fix PR names the PR that broke things, either in its title, e.g.{' '}
          <Code>Fix #1234: basket total rounding</Code> (also{' '}
          <Code>fixes #1234</Code>, <Code>reverts #1234</Code>), or in its
          branch, e.g. <Code>hotfix/1234-basket-total</Code>.
        </Step>
        <Step n={4}>
          The failed deployment is the one that shipped PR #1234.
        </Step>
      </FlexBox>
      <Alert severity="info">
        If any link in that chain is missing, the regression is still listed but
        is left out of the rate, with the reason: no linked fix PR, no culprit
        named, or the named PR doesn&apos;t exist or never merged. The patterns
        used to find the culprit can be changed in the team&apos;s DORA metrics
        settings; setting any replaces the defaults above.
      </Alert>
    </Section>

    <Section title="GitHub Team (squad) view">
      <P>
        Deployments to <Code>mvp-api</Code> and <Code>mvp-app</Code> carry
        several squads&apos; work. With a GitHub Team selected:
      </P>
      <Bullets
        items={[
          <>
            A squad&apos;s deployments are those that shipped at least one of
            its PRs, the same rule as the Deployment Frequency card.
          </>,
          <>
            A shared deployment fails for a squad only if{' '}
            <b>the PR at fault is theirs</b>. If Skipper&apos;s PR breaks a
            deploy that also carried Bliss&apos;s work, it&apos;s a failure for
            Skipper and a normal deploy for Bliss.
          </>,
          <>
            A <b>rollback</b> doesn&apos;t say whose PR was at fault, so a
            rolled-back shared deployment fails for every squad with work in it.
          </>
        ]}
      />
      <P>
        Because of this, squad figures don&apos;t add up to the org-wide figure.
      </P>
    </Section>

    <Section title="What isn't counted">
      <Bullets
        items={[
          <>
            <b>Legacy or customer-found bugs</b> that weren&apos;t caused by a
            recent change. They say little about current release quality, so
            don&apos;t label them <Code>regression</Code>; use a separate label
            such as <Code>customer-reported</Code>.
          </>,
          <>
            <b>Failures fixed without a trace</b>: a hotfix PR that names no
            culprit, or a rollback done outside the CircleCI deploy job.
          </>,
          <>
            <b>Problems with no code change behind them</b>, such as
            infrastructure, third-party or data issues.
          </>
        ]}
      />
    </Section>

    <Section title="What teams need to do">
      <Bullets
        items={[
          <>
            Label bugs that a recent change caused <Code>regression</Code> in
            Shortcut.
          </>,
          <>
            Link the fix PR to the story with <Code>sc-1234</Code> in the
            branch, or <Code>[sc-1234]</Code> in the PR.
          </>,
          <>
            Name the culprit in the fix PR: <Code>fixes #123</Code> in the
            title, or a <Code>hotfix/123-…</Code> branch. Or undo it with
            GitHub&apos;s Revert button.
          </>,
          <>Rollbacks need nothing: they&apos;re picked up automatically.</>
        ]}
      />
    </Section>

    <Section title="How this affects Mean Time to Restore">
      <P>The same incidents feed Mean Time to Restore:</P>
      <Bullets
        items={[
          <>
            <b>Rollback</b>: from the undone deployment going out to the
            rollback going out.
          </>,
          <>
            <b>Revert</b>: from the reverted PR merging to the revert merging.
          </>,
          <>
            <b>Regression</b>: from the bug being raised in Shortcut to the fix
            PR merging.
          </>
        ]}
      />
    </Section>
  </FlexBox>
);

const Section: FC<{ title: string; children: ReactNode }> = ({
  title,
  children
}) => (
  <FlexBox col gap={1} fullWidth component={Paper} p={2}>
    <Line big medium white>
      {title}
    </Line>
    {children}
  </FlexBox>
);

const P: FC<{ children: ReactNode }> = ({ children }) => (
  <Line small sx={{ lineHeight: 1.6 }}>
    {children}
  </Line>
);

const Code: FC<{ children: ReactNode }> = ({ children }) => (
  <Box
    component="code"
    sx={{
      fontFamily: 'monospace',
      fontSize: '0.9em',
      px: 0.5,
      borderRadius: 0.5,
      bgcolor: 'action.hover'
    }}
  >
    {children}
  </Box>
);

const Formula: FC<{ children: ReactNode }> = ({ children }) => (
  <Box
    sx={{
      fontFamily: 'monospace',
      p: 1.5,
      borderRadius: 1,
      bgcolor: 'action.hover'
    }}
  >
    {children}
  </Box>
);

const Step: FC<{ n: number; children: ReactNode }> = ({ n, children }) => (
  <FlexBox gap={1} alignStart>
    <Line small bold sx={{ minWidth: '1.5em' }}>
      {n}.
    </Line>
    <Line small sx={{ lineHeight: 1.6 }}>
      {children}
    </Line>
  </FlexBox>
);

const Bullets: FC<{ items: ReactNode[] }> = ({ items }) => (
  <Box component="ul" sx={{ m: 0, pl: 3 }}>
    {items.map((item, index) => (
      <Box component="li" key={index} sx={{ mb: 0.5 }}>
        <Line small sx={{ lineHeight: 1.6 }}>
          {item}
        </Line>
      </Box>
    ))}
  </Box>
);
