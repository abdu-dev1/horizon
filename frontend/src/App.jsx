import { useEffect, useState } from "react";
import {
  Activity,
  Briefcase,
  BookOpen,
  CalendarRange,
  CheckCircle2,
  Database,
  FlaskConical,
  Inbox,
  LayoutDashboard,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  RefreshCw,
  Scale,
  ShieldCheck,
  Sun,
  TrendingUp,
  Wrench,
} from "lucide-react";
import { api, loadAll } from "./api.js";
import { apiNb, loadAllNb } from "./apiNb.js";
import Overview from "./pages/Overview.jsx";
import Forecast from "./pages/Forecast.jsx";
import Book from "./pages/Book.jsx";
import NeedsData from "./pages/NeedsData.jsx";
import RenewalDatabase from "./pages/RenewalDatabase.jsx";
import ModelLab from "./pages/ModelLab.jsx";
import DataQuality from "./pages/DataQuality.jsx";
import ModelMaintenance from "./pages/ModelMaintenance.jsx";
import NbOverview from "./pages/nb/NbOverview.jsx";
import NbPipeline from "./pages/nb/NbPipeline.jsx";
import NbDatabase from "./pages/nb/NbDatabase.jsx";
import NbModel from "./pages/nb/NbModel.jsx";
import NbDataQuality from "./pages/nb/NbDataQuality.jsx";
import NbPricingLogic from "./pages/nb/NbPricingLogic.jsx";
import NbPerformance from "./pages/nb/NbPerformance.jsx";

const RENEWAL_PAGES = [
  { id: "overview", label: "Overview", icon: LayoutDashboard },
  { id: "forecast", label: "Renewal Forecast", icon: CalendarRange },
  { id: "needsdata", label: "Needs Data", icon: Inbox },
  { id: "book", label: "Upcoming Renewals", icon: BookOpen },
  { id: "database", label: "Renewal Database", icon: Database },
  { id: "model", label: "Model Performance", icon: FlaskConical },
  { id: "quality", label: "Data Quality", icon: ShieldCheck },
  { id: "maintenance", label: "Model Maintenance", icon: Wrench },
];

const NB_PAGES = [
  { id: "overview", label: "Overview", icon: LayoutDashboard },
  { id: "pipeline", label: "Open Pipeline", icon: BookOpen },
  { id: "performance", label: "Sales Performance", icon: TrendingUp },
  { id: "database", label: "Win/Loss Database", icon: Database },
  { id: "model", label: "Model Performance", icon: FlaskConical },
  { id: "quality", label: "Data Quality", icon: ShieldCheck },
  { id: "pricinglogic", label: "Pricing Logic", icon: Scale },
];

const RENEWAL_SUBTITLES = {
  overview: "Two-quarter renewal outlook across the in-force HPS book",
  forecast: "Month-by-month expected renewals, lapses, and premium at risk",
  needsdata: "Renewals awaiting underwriting before they can be scored with confidence",
  book: "Data-complete renewals, scored and ranked by renewal likelihood",
  database: "Every historical renewal decision the model trains on, browsable",
  model: "Accuracy, calibration, and how the model improves each month",
  quality: "Completeness, cleaning, and the open items in the training data",
  maintenance: "The monthly workflow that keeps the forecast accurate",
};

const NB_SUBTITLES = {
  overview: "Open pipeline of first-time-client quotes, scored by win likelihood",
  pipeline: "Every open quote, ranked by win likelihood",
  performance: "What the book actually did — win rates by year, rep, product, size and season",
  database: "Every decided quote (won or lost) the model trains on, browsable",
  model: "Accuracy and how the model improves each retrain",
  quality: "Completeness of the data the model learns from and scores with",
  pricinglogic: "What the market-pricing comparison rule does, and where it shows up",
};

export default function App() {
  const [product, setProduct] = useState(
    () => localStorage.getItem("horizon-product") ?? "renewals"
  );
  const [page, setPage] = useState("overview");
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [nbData, setNbData] = useState(null);
  const [nbError, setNbError] = useState(null);
  const [retraining, setRetraining] = useState(false);
  const [toast, setToast] = useState(null);
  const [theme, setTheme] = useState(() => localStorage.getItem("horizon-theme") ?? "dark");
  const [sidebarOpen, setSidebarOpen] = useState(
    () => (localStorage.getItem("horizon-sidebar") ?? "open") === "open"
  );

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("horizon-theme", theme);
  }, [theme]);

  useEffect(() => {
    localStorage.setItem("horizon-sidebar", sidebarOpen ? "open" : "closed");
  }, [sidebarOpen]);

  useEffect(() => {
    localStorage.setItem("horizon-product", product);
  }, [product]);

  // Both products' data load in parallel on startup, independently — a failure
  // in one (e.g. the New Business backend not running yet in dev) never blocks
  // the other, and switching tabs is instant with no per-switch loading spinner.
  useEffect(() => {
    loadAll().then(setData).catch((e) => setError(e.message));
  }, []);

  useEffect(() => {
    loadAllNb().then(setNbData).catch((e) => setNbError(e.message));
  }, []);

  const refreshNb = async () => setNbData(await loadAllNb());

  const handleRetrain = async () => {
    setRetraining(true);
    try {
      if (product === "renewals") {
        const result = await api.retrain();
        const fresh = await loadAll();
        setData(fresh);
        setToast(`Model ${result.version} trained — holdout AUC ${result.metrics.auc.toFixed(3)}`);
      } else {
        const result = await apiNb.retrain();
        await refreshNb();
        setToast(`Model ${result.version} trained — ROC AUC ${result.metrics.roc_auc.toFixed(3)}`);
      }
      setTimeout(() => setToast(null), 6000);
    } catch (e) {
      setToast(`Retrain failed: ${e.message}`);
      setTimeout(() => setToast(null), 6000);
    } finally {
      setRetraining(false);
    }
  };

  const switchProduct = (p) => {
    setProduct(p);
    setPage("overview");
  };

  const PAGES = product === "renewals" ? RENEWAL_PAGES : NB_PAGES;
  const SUBTITLES = product === "renewals" ? RENEWAL_SUBTITLES : NB_SUBTITLES;
  const activePage = PAGES.find((p) => p.id === page) ?? PAGES[0];

  if (product === "renewals" && error)
    return (
      <div className="loading-screen">
        <div style={{ color: "var(--red)", fontWeight: 700 }}>Cannot reach the Renewals API</div>
        <div style={{ fontSize: 12.5 }}>
          Start the backend first: <span className="mono">uvicorn app.main:app --port 8000</span>
        </div>
        <div style={{ fontSize: 12, color: "var(--text-faint)" }}>{error}</div>
      </div>
    );

  if (product === "renewals" && !data)
    return (
      <div className="loading-screen">
        <div className="spinner" />
        <div style={{ fontWeight: 600 }}>Loading the renewal forecast…</div>
      </div>
    );

  return (
    <div className="shell">
      {sidebarOpen && (
        <aside className="sidebar" data-product={product}>
          <div className="brand">
            <div className="brand-mark">
              <img src="/logo.png" alt="Crumdale Specialty" />
            </div>
            <div>
              <div className="brand-name">Horizon</div>
              <div className="brand-sub">Crumdale Specialty</div>
            </div>
          </div>

          <div className="product-switch">
            <button
              className={`product-switch-btn ${product === "renewals" ? "active" : ""}`}
              data-product="renewals"
              onClick={() => switchProduct("renewals")}
            >
              <CalendarRange size={13} /> Renewals
            </button>
            <button
              className={`product-switch-btn ${product === "newbusiness" ? "active" : ""}`}
              data-product="newbusiness"
              onClick={() => switchProduct("newbusiness")}
            >
              <Briefcase size={13} /> New Business
            </button>
          </div>

          {PAGES.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              className={`nav-item ${page === id ? "active" : ""}`}
              onClick={() => setPage(id)}
            >
              <Icon size={17} strokeWidth={2.2} />
              {label}
            </button>
          ))}

          <div className="sidebar-footer">
            {product === "renewals" ? (
              <>
                <div className="model-chip">
                  <Activity size={11} /> {data?.health.model_version}
                </div>
                <div>Real renewal data</div>
                <div>HPS Level Funded &amp; Self Funded</div>
                <div>Forecast horizon: 2 quarters</div>
              </>
            ) : (
              <>
                <div className="model-chip">
                  <Activity size={11} /> {nbData?.health.model_version ?? "—"}
                </div>
                <div>First-time-client quotes</div>
                <div>Win-likelihood forecast</div>
              </>
            )}
          </div>
        </aside>
      )}

      <div className="main">
        <header className="topbar">
          <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
            <button
              className="btn btn-ghost"
              title={sidebarOpen ? "Hide sidebar" : "Show sidebar"}
              onClick={() => setSidebarOpen(!sidebarOpen)}
            >
              {sidebarOpen ? <PanelLeftClose size={16} /> : <PanelLeftOpen size={16} />}
            </button>
            <div>
              <div className="page-title">{activePage.label}</div>
              <div className="page-sub">{SUBTITLES[page]}</div>
            </div>
          </div>
          <div className="topbar-right">
            <button
              className="btn btn-ghost"
              title={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
              onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
            >
              {theme === "dark" ? <Sun size={16} /> : <Moon size={16} />}
            </button>
            {(product === "renewals" || nbData) && (
              <button className="btn btn-primary" onClick={handleRetrain} disabled={retraining}>
                <RefreshCw
                  size={14}
                  style={retraining ? { animation: "spin 0.9s linear infinite" } : {}}
                />
                {retraining ? "Retraining…" : "Monthly Retrain"}
              </button>
            )}
          </div>
        </header>

        <main className="content">
          {product === "renewals" && page === "overview" && <Overview data={data} />}
          {product === "renewals" && page === "forecast" && <Forecast data={data} />}
          {product === "renewals" && page === "needsdata" && (
            <NeedsData data={data} onDataChange={async () => setData(await loadAll())} />
          )}
          {product === "renewals" && page === "book" && (
            <Book data={data} onDataChange={async () => setData(await loadAll())} />
          )}
          {product === "renewals" && page === "database" && <RenewalDatabase />}
          {product === "renewals" && page === "model" && <ModelLab data={data} />}
          {product === "renewals" && page === "quality" && <DataQuality />}
          {product === "renewals" && page === "maintenance" && (
            <ModelMaintenance onDataChange={async () => setData(await loadAll())} />
          )}

          {product === "newbusiness" && nbError && (
            <div className="loading-screen" style={{ position: "static", minHeight: "60vh" }}>
              <div style={{ color: "var(--red)", fontWeight: 700 }}>Cannot reach the New Business API</div>
              <div style={{ fontSize: 12.5 }}>
                Start the backend first: <span className="mono">uvicorn app.main:app --port 8001</span>{" "}
                (from NewBusiness/backend/)
              </div>
              <div style={{ fontSize: 12, color: "var(--text-faint)" }}>{nbError}</div>
            </div>
          )}
          {product === "newbusiness" && !nbError && !nbData && (
            <div className="loading-screen" style={{ position: "static", minHeight: "60vh" }}>
              <div className="spinner" />
              <div style={{ fontWeight: 600 }}>Loading the New Business pipeline…</div>
            </div>
          )}
          {product === "newbusiness" && nbData && page === "overview" && <NbOverview data={nbData} />}
          {product === "newbusiness" && nbData && page === "pipeline" && (
            <NbPipeline
              data={nbData}
              onDataChange={refreshNb}
              onNotify={(msg) => { setToast(msg); setTimeout(() => setToast(null), 6000); }}
            />
          )}
          {product === "newbusiness" && nbData && page === "performance" && <NbPerformance data={nbData} />}
          {product === "newbusiness" && nbData && page === "database" && <NbDatabase />}
          {product === "newbusiness" && nbData && page === "model" && (
            <NbModel data={nbData} onDataChange={refreshNb} />
          )}
          {product === "newbusiness" && nbData && page === "quality" && <NbDataQuality />}
          {product === "newbusiness" && nbData && page === "pricinglogic" && <NbPricingLogic />}
        </main>
      </div>

      {toast && (
        <div className="toast">
          <CheckCircle2 size={16} color="var(--green)" /> {toast}
        </div>
      )}
    </div>
  );
}
