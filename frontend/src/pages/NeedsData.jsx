import { useMemo, useState } from "react";
import { Check, Loader2 } from "lucide-react";
import { api } from "../api.js";
import { fmtDate } from "../format.js";
import { GroupDrawer } from "./Book.jsx";

// Every field the model uses, beyond identity (group + renewal date, fixed —
// editing those would target a different renewal record entirely, not correct
// this one). Same canonical names + unit conventions as the upload template
// (backend/upload_feed.py FIELDS): ratio fields ("pct") are typed as a percent
// (62) and the backend auto-detects/divides by 100 the same way a file upload
// does (upload_feed.coerce_one) — one shared rule, not two.
//   from(g)  — the group's CURRENT value, pre-filled into the cell
//   type     — "num" | "pct" | "text" | "tri" (Unknown/Yes/No — a plain checkbox
//              can't represent "unknown" without lying about being touched)
const EDIT_COLUMNS = [
  { key: "product", label: "Product", type: "text", from: (g) => g.product },
  { key: "lives", label: "Lives", type: "num", from: (g) => g.group_size },
  { key: "annual_premium", label: "Premium", type: "num", from: (g) => g.annual_premium },
  { key: "premium_stoploss", label: "SL Premium", type: "num", from: (g) => g.premium_stoploss },
  { key: "nlr", label: "Loss Ratio %", type: "pct", from: (g) => g.loss_ratio },
  { key: "isl_loss_ratio", label: "ISL LR %", type: "pct", from: (g) => g.isl_loss_ratio },
  { key: "agg_loss_ratio", label: "Agg LR %", type: "pct", from: (g) => g.agg_loss_ratio },
  { key: "ratio_to_attachment", label: "Ratio/Attach %", type: "pct", from: (g) => g.ratio_to_attachment },
  { key: "mature_to_attachment", label: "Mature/Attach %", type: "pct", from: (g) => g.mature_to_attachment },
  { key: "corridor", label: "Corridor %", type: "pct", from: (g) => g.corridor },
  { key: "total_increase_pct", label: "Renewal Inc %", type: "num", from: (g) => g.rate_increase_pct },
  { key: "initial_uw_increase_pct", label: "Initial UW Inc %", type: "num", from: (g) => g.initial_uw_increase_pct },
  { key: "fixed_increase_pct", label: "Fixed Inc %", type: "num", from: (g) => g.fixed_increase_pct },
  { key: "lasers_current", label: "Lasers (cur)", type: "num", from: (g) => g.lasers_current },
  { key: "lasers_renewal", label: "Lasers (renewal)", type: "num", from: (g) => g.lasers_renewal_count },
  { key: "laser_liability", label: "Laser Liability $", type: "num", from: (g) => g.laser_liability },
  { key: "captive_offer", label: "Captive Offered", type: "tri", from: (g) => g.captive_offer },
  { key: "tenure_years", label: "Tenure (yrs)", type: "num", from: (g) => g.tenure_years },
  { key: "bor_change", label: "BOR Change", type: "tri", from: (g) => g.recent_bor },
  { key: "broker", label: "Broker", type: "text", from: (g) => g.broker_name },
  { key: "rsd", label: "RSD", type: "text", from: (g) => g.rsd },
  { key: "am", label: "AM", type: "text", from: (g) => g.am },
  { key: "carrier", label: "Carrier", type: "text", from: (g) => g.carrier },
  { key: "tpa", label: "TPA", type: "text", from: (g) => g.tpa },
  { key: "state", label: "State", type: "text", from: (g) => g.state },
  { key: "network", label: "Network", type: "text", from: (g) => g.network },
  { key: "broker_years_with_cs", label: "Brk Yrs/CS", type: "num", from: (g) => g.broker_years_with_cs },
  { key: "broker_groups_with_cs", label: "Brk Groups/CS", type: "num", from: (g) => g.broker_groups_with_cs },
  { key: "broker_products_sold", label: "Brk Products", type: "num", from: (g) => g.broker_products_sold },
  { key: "broker_preferred", label: "Preferred Broker", type: "tri", from: (g) => g.broker_preferred },
];

const BLANK_TEXT = new Set([null, undefined, "", "—", "Unknown"]);
const cleanText = (v) => (BLANK_TEXT.has(v) ? "" : v);
const cleanNum = (v) => (v == null ? "" : v);
const cleanPct = (v) => (v == null ? "" : Math.round(v * 1000) / 10); // decimal -> percent
const triVal = (v) => (v == null ? "unknown" : v ? "yes" : "no");

function initialValue(col, group) {
  const raw = col.from(group);
  if (col.type === "pct") return cleanPct(raw);
  if (col.type === "num") return cleanNum(raw);
  if (col.type === "tri") return triVal(raw);
  return cleanText(raw);
}

// Renewals that came from the pipeline list with no loss ratio / rate loaded yet — the
// model can't score them with confidence, so they wait here (out of Upcoming Renewals)
// until their underwriting is filled in, then they graduate automatically.
export default function NeedsData({ data, onDataChange }) {
  const [selected, setSelected] = useState(null);

  const rows = useMemo(
    () =>
      (data.groups || [])
        .filter((g) => g.prediction_confidence === "low")
        .sort((a, b) => new Date(a.renewal_date) - new Date(b.renewal_date)),
    [data.groups]);

  return (
    <>
      <div className="card">
        <div className="card-title">
          {rows.length} renewals awaiting underwriting
        </div>
        <div className="card-sub">
          These came from the pipeline list with <b>no loss ratio / quoted rate loaded yet</b>, so
          they aren&apos;t scored with confidence and are kept out of Upcoming Renewals. Every field
          the model uses is shown and editable below, pre-filled with what's already known — unknown
          fields stay blank, never guessed. Fill in what you have and hit Save; the group moves into
          <b> Upcoming Renewals</b> the instant it has a loss ratio and renewal increase. (Updating a
          batch of groups at once? Use the <b>upload</b> on the Model Maintenance page instead.)
        </div>
        <div className="table-wrap">
          <table className="uw-table">
            <thead>
              <tr>
                <th className="uw-col-sticky">Group</th>
                <th>Group ID</th>
                <th>Renewal Date</th>
                {EDIT_COLUMNS.map((c) => <th key={c.key}>{c.label}</th>)}
                <th>Save</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((g) => (
                <EditableRow key={g.group_id} group={g} onSaved={onDataChange} onOpen={() => setSelected(g)} />
              ))}
              {rows.length === 0 && (
                <tr>
                  <td colSpan={EDIT_COLUMNS.length + 4} className="cell-dim" style={{ textAlign: "center", padding: 24 }}>
                    Nothing waiting — every renewal in the window has its underwriting loaded. 🎉
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
      {selected && (
        <GroupDrawer
          group={selected}
          onClose={() => setSelected(null)}
          onDeleted={async () => { setSelected(null); await onDataChange?.(); }}
        />
      )}
    </>
  );
}

function EditableRow({ group, onSaved, onOpen }) {
  const [values, setValues] = useState(() =>
    Object.fromEntries(EDIT_COLUMNS.map((c) => [c.key, initialValue(c, group)]))
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [done, setDone] = useState(false);

  const set = (key, v) => setValues((prev) => ({ ...prev, [key]: v }));

  const save = async () => {
    if (saving) return;
    setSaving(true);
    setError(null);
    try {
      const fields = {};
      for (const c of EDIT_COLUMNS) {
        const v = values[c.key];
        if (c.type === "tri") {
          if (v !== "unknown") fields[c.key] = v === "yes";
        } else if (v !== "" && v != null) {
          fields[c.key] = v;
        }
      }
      await api.updateUnderwriting(group.group_id, fields);
      setDone(true);
      await onSaved?.();
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <tr>
      <td className="uw-col-sticky" onClick={onOpen} style={{ cursor: "pointer" }}>
        <div className="cell-main">{group.group_name}</div>
        <div className="cell-dim">{group.line_of_business} · {group.industry}</div>
      </td>
      <td className="mono cell-dim" onClick={onOpen} style={{ cursor: "pointer" }}>{group.group_id}</td>
      <td className="mono" onClick={onOpen} style={{ cursor: "pointer" }}>{fmtDate(group.renewal_date)}</td>
      {EDIT_COLUMNS.map((c) => (
        <td key={c.key}>
          {c.type === "tri" ? (
            <select className="uw-cell-input uw-cell-select" value={values[c.key]}
                    onChange={(e) => set(c.key, e.target.value)}>
              <option value="unknown">—</option>
              <option value="yes">Yes</option>
              <option value="no">No</option>
            </select>
          ) : (
            <input
              className="uw-cell-input"
              type={c.type === "text" ? "text" : "number"}
              value={values[c.key]}
              onChange={(e) => set(c.key, e.target.value)}
            />
          )}
        </td>
      ))}
      <td>
        {done ? (
          <span className="uw-saved"><Check size={13} /> Saved</span>
        ) : (
          <>
            <button className="uw-save" onClick={save} disabled={saving}>
              {saving ? <Loader2 size={13} className="uw-spin" /> : "Save"}
            </button>
            {error && <span className="uw-error" title={error}>Failed</span>}
          </>
        )}
      </td>
    </tr>
  );
}
