import ExtendedSidebarLayout from 'src/layouts/ExtendedSidebarLayout';

import { Authenticated } from '@/components/Authenticated';
import { FlexBox } from '@/components/FlexBox';
import Loader from '@/components/Loader';
import { FetchState } from '@/constants/ui-states';
import { useRedirectWithSession } from '@/constants/useRoute';
import { CockpitBody } from '@/content/Cockpit/CockpitBody';
import { PageWrapper } from '@/content/PullRequests/PageWrapper';
import { useAuth } from '@/hooks/useAuth';
import { useSelector } from '@/store';
import { PageLayout } from '@/types/resources';

/**
 * Same shape as pages/dora-metrics/index.tsx on purpose.
 *
 * Passing `teamDateSelectorMode` is what gives this page the team picker,
 * the date range and the GitHub team dropdown — all three already live in
 * the shared PageHeader, so none of them needed wiring.
 */
function Page() {
  useRedirectWithSession();
  const isLoading = useSelector(
    (s) => s.cockpit.requests?.flow === FetchState.REQUEST
  );
  const { integrationList } = useAuth();

  return (
    <PageWrapper
      title={
        <FlexBox gap1 alignCenter>
          Cockpit
        </FlexBox>
      }
      pageTitle="Cockpit"
      isLoading={isLoading}
      teamDateSelectorMode="single"
    >
      {integrationList.length > 0 ? <CockpitBody /> : <Loader />}
    </PageWrapper>
  );
}

Page.getLayout = (page: PageLayout) => (
  <Authenticated>
    <ExtendedSidebarLayout>{page}</ExtendedSidebarLayout>
  </Authenticated>
);

export default Page;
