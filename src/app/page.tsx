import { ReportDashboard } from "@/components/report-dashboard";
import { reportDataQuality, reportDataset } from "@/lib/report-model";

export default function Home() {
  return <ReportDashboard dataset={reportDataset} quality={reportDataQuality} />;
}
