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

// `admin: true` marks an operator surface — model internals, training-data
// health, and the retrain/upload workflow. Business users get the 10 pages
// without it; admins see all 15. The flag only controls what the sidebar
// offers: the gateway enforces the same split on the underlying endpoints
// (gateway/auth.py ADMIN_PREFIXES), so hiding a page is a UI courtesy, not
// the access control. Keep the two lists in agreement.
const RENEWAL_PAGES = [
  { id: "overview", label: "Overview", icon: LayoutDashboard },
  { id: "forecast", label: "Renewal Forecast", icon: CalendarRange },
  { id: "needsdata", label: "Needs Data", icon: Inbox },
  { id: "book", label: "Upcoming Renewals", icon: BookOpen },
  { id: "database", label: "Renewal Database", icon: Database },
  { id: "model", label: "Model Performance", icon: FlaskConical, admin: true },
  { id: "quality", label: "Data Quality", icon: ShieldCheck, admin: true },
  { id: "maintenance", label: "Model Maintenance", icon: Wrench, admin: true },
];

const NB_PAGES = [
  { id: "overview", label: "Overview", icon: LayoutDashboard },
  { id: "pipeline", label: "Open Pipeline", icon: BookOpen },
  { id: "performance", label: "Sales Performance", icon: TrendingUp },
  { id: "database", label: "Win/Loss Database", icon: Database },
  { id: "model", label: "Model Performance", icon: FlaskConical, admin: true },
  { id: "quality", label: "Data Quality", icon: ShieldCheck, admin: true },
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
  const [me, setMe] = useState(null);
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

  // Identity has to resolve BEFORE either product loads, because it decides
  // what gets fetched: the model-internals endpoints are admin-only and answer
  // 403, which would reject a batched load for everyone else (see api.js).
  useEffect(() => {
    api
      .me()
      .then(setMe)
      // Treat an unreadable identity as a non-admin rather than failing the
      // app: the worst case is a signed-in user seeing only the business
      // pages, which is the safe direction to fail in.
      .catch(() => setMe({ email: null, is_admin: false }));
  }, []);

  // Both products' data load in parallel once identity is known, independently
  // — a failure in one (e.g. the New Business backend not running yet in dev)
  // never blocks the other, and switching tabs is instant with no per-switch
  // loading spinner.
  const isAdmin = me?.is_admin === true;

  useEffect(() => {
    if (!me) return;
    loadAll(isAdmin).then(setData).catch((e) => setError(e.message));
  }, [me, isAdmin]);

  useEffect(() => {
    if (!me) return;
    loadAllNb(isAdmin).then(setNbData).catch((e) => setNbError(e.message));
  }, [me, isAdmin]);

  const refreshNb = async () => setNbData(await loadAllNb(isAdmin));

  const handleRetrain = async () => {
    setRetraining(true);
    try {
      if (product === "renewals") {
        const result = await api.retrain();
        const fresh = await loadAll(isAdmin);
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

  const ALL_PAGES = product === "renewals" ? RENEWAL_PAGES : NB_PAGES;
  const PAGES = ALL_PAGES.filter((p) => !p.admin || isAdmin);
  const SUBTITLES = product === "renewals" ? RENEWAL_SUBTITLES : NB_SUBTITLES;
  // Fall back to the first VISIBLE page: `page` persists across a product
  // switch, so a non-admin could otherwise land on an admin page id and render
  // a blank main area.
  const activePage = PAGES.find((p) => p.id === page) ?? PAGES[0];
  const pageId = activePage?.id;

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
              className={`nav-item ${pageId === id ? "active" : ""}`}
              onClick={() => setPage(id)}
            >
              <Icon size={17} strokeWidth={2.2} />
              {label}
            </button>
          ))}

          <div className="sidebar-footer">
            {/* Who you are signed in as. Worth showing explicitly: with SSO
                there is no login screen to remind you, and an admin needs to
                be able to tell at a glance whether they are seeing the
                operator pages because of their role or not. */}
            {me?.email && (
              <div style={{ marginBottom: 8, lineHeight: 1.5, wordBreak: "break-all" }}>
                <div style={{ fontWeight: 600, color: "var(--text)" }}>{me.email}</div>
                <div style={{ color: isAdmin ? "var(--teal, #14b8a6)" : "var(--text-faint)" }}>
                  {isAdmin ? "Administrator" : "Standard access"}
                </div>
              </div>
            )}
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
          {product === "renewals" && pageId === "overview" && <Overview data={data} />}
          {product === "renewals" && pageId === "forecast" && <Forecast data={data} />}
          {product === "renewals" && pageId === "needsdata" && (
            <NeedsData data={data} onDataChange={async () => setData(await loadAll(isAdmin))} />
          )}
          {product === "renewals" && pageId === "book" && (
            <Book data={data} onDataChange={async () => setData(await loadAll(isAdmin))} />
          )}
          {product === "renewals" && pageId === "database" && <RenewalDatabase />}
          {product === "renewals" && pageId === "model" && <ModelLab data={data} />}
          {product === "renewals" && pageId === "quality" && <DataQuality />}
          {product === "renewals" && pageId === "maintenance" && (
            <ModelMaintenance onDataChange={async () => setData(await loadAll(isAdmin))} />
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
          {product === "newbusiness" && nbData && pageId === "overview" && <NbOverview data={nbData} />}
          {product === "newbusiness" && nbData && pageId === "pipeline" && (
            <NbPipeline
              data={nbData}
              onDataChange={refreshNb}
              onNotify={(msg) => { setToast(msg); setTimeout(() => setToast(null), 6000); }}
            />
          )}
          {product === "newbusiness" && nbData && pageId === "performance" && <NbPerformance data={nbData} />}
          {product === "newbusiness" && nbData && pageId === "database" && <NbDatabase />}
          {product === "newbusiness" && nbData && pageId === "model" && (
            <NbModel data={nbData} onDataChange={refreshNb} />
          )}
          {product === "newbusiness" && nbData && pageId === "quality" && <NbDataQuality />}
          {product === "newbusiness" && nbData && pageId === "pricinglogic" && <NbPricingLogic />}
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
