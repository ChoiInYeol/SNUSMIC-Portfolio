import raw from "../../public/api/v2/portfolio.json";
import { PortfolioDocumentSchema } from "./sota-portfolio";

// Web pages and Telegram consume the same published resource.
export const portfolio = PortfolioDocumentSchema.parse(raw).data;
