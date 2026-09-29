import { Authenticated } from 'src/components/Authenticated';

import { useRedirectWithSession } from '@/constants/useRoute';
import { ChangeFailureRateGuide } from '@/content/DoraMetrics/ChangeFailureRateGuide';
import { PageWrapper } from '@/content/PullRequests/PageWrapper';
import ExtendedSidebarLayout from '@/layouts/ExtendedSidebarLayout';
import { PageLayout } from '@/types/resources';

function ChangeFailureRateGuidePage() {
  useRedirectWithSession();

  return (
    <PageWrapper
      title="How Change Failure Rate is calculated"
      hideAllSelectors
      pageTitle="How Change Failure Rate is calculated"
      showEvenIfNoTeamSelected={true}
    >
      <ChangeFailureRateGuide />
    </PageWrapper>
  );
}

ChangeFailureRateGuidePage.getLayout = (page: PageLayout) => (
  <Authenticated>
    <ExtendedSidebarLayout>{page}</ExtendedSidebarLayout>
  </Authenticated>
);

export default ChangeFailureRateGuidePage;
