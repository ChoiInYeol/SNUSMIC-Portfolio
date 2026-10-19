import { ReportDashboard } from "@/components/report-dashboard";
import { reportDataQuality, reportDataset } from "@/lib/report-model";
import { packDashboard } from "@/lib/dashboard-transport";

export default function Home() {
  return <ReportDashboard packed={packDashboard(reportDataset)} quality={reportDataQuality} />;
}
