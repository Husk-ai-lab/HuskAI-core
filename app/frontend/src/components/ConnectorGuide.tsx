import { ExternalLink } from "lucide-react";
import { useT } from "../i18n";
import { GUIDE, type ConnectorKind } from "../lib/connectors";

/** The step-by-step guide of one connector (shared by the Settings popup and the first-login popup). */
export function ConnectorGuide({ kind }: { kind: ConnectorKind }) {
  const t = useT();
  const g = GUIDE[kind];
  return (
    <div>
      <ol className="steps4" style={{ marginBottom: 18 }}>
        {Array.from({ length: g.steps }, (_, i) => i + 1).map((n) => (
          <li key={n}>
            <span className="n">{n}</span>
            <div>
              <b>{t(`connectors.${kind}.step${n}_title`)}</b>
              <div>{t(`connectors.${kind}.step${n}_body`)}</div>
              {g.links[n] && (
                <a className="btn sm" style={{ marginTop: 8 }} href={g.links[n]} target="_blank" rel="noreferrer">
                  {t(`connectors.${kind}.link${n}`)} <ExternalLink size={13} />
                </a>
              )}
            </div>
          </li>
        ))}
      </ol>
      {Array.from({ length: g.extraCount }, (_, i) => i + 1).map((n) => (
        <p key={n} className="muted" style={{ fontSize: 13, margin: "6px 0" }}>{t(`connectors.${kind}.${g.extras}${n}`)}</p>
      ))}
    </div>
  );
}
